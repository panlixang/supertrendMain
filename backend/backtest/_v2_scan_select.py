# -*- coding: utf-8 -*-
"""在已有 _v2_threshold_scan.json（扫描曲线）基础上，用更稳健的 Walk-Forward
选择逻辑重选 t* 并仅重算最终对比（不重跑扫描）。

选择原则（避免过拟合）：
- 候选：OOS E>0 且 OOS 交易数≥MIN_OOS_T 且 OOS PF>1 且 训练E>0
- 稳定区间：候选阈值的连续段
- t* = 区间内使 min(训练E, OOS E) 最大者；若落在连续段边缘则内移一步（防边界过拟合）
- 全程以 OOS 为主（用户优先级①），不追单点峰值。
"""
import json
import os
import sys
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _v2_threshold_scan as SC  # noqa: E402

MIN_OOS_T = 10
MAX_OOS_PF = 8.0  # OOS PF 超过此值通常是小样本（几笔全赢）伪信号
STEP = 4


def select(scan):
    cands = [r for r in scan
             if r["oos"]["e"] > 0 and r["oos"]["t"] >= MIN_OOS_T
             and r["oos"]["pf"] > 1 and r["oos"]["pf"] < MAX_OOS_PF
             and r["train"]["e"] > 0]
    if not cands:
        cands = [r for r in scan if r["oos"]["e"] > 0 and r["train"]["e"] > 0]
    if not cands:
        return scan[-1]["t"], [scan[0]["t"], scan[-1]["t"]]
    seg = SC.max_contiguous([r["t"] for r in cands], STEP)
    lo, hi = seg[0], seg[-1]
    best, best_sc = lo, -1e9
    for r in scan:
        if r["t"] < lo or r["t"] > hi:
            continue
        sc = min(r["train"]["e"], r["oos"]["e"])
        sc -= 0.02 * max(0, 14 - r["oos"]["t"])  # 交易过少轻微惩罚
        if sc > best_sc:
            best_sc, best = sc, r["t"]
    if best == lo and hi - lo >= STEP:
        best = lo + STEP
    if best == hi and hi - lo >= STEP:
        best = hi - STEP
    return best, [lo, hi]


def main():
    data = json.load(open(
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "_v2_threshold_scan.json"), encoding="utf-8"))

    syms = SC.fetch_symbols()
    sym_map = {x["symbol"].replace("-USDT-SWAP", "").replace("-USDT", ""): x
               for x in syms}

    refined = {}
    for name in SC.SYMS:
        if name in data:
            refined[name] = select(data[name]["scan"])

    # 重算最终对比（仅 6 次/品种）
    for name in SC.SYMS:
        if name not in sym_map or name not in data:
            continue
        s = sym_map[name]
        ex = SC.exit_rules(s)
        gate_tf = s["allow_tfs"][0]
        cbtf_full = {k: v for k, v in
                     json.load(open(os.path.join(SC.DATA, f"{name}.json"),
                                   encoding="utf-8")).items()
                     if isinstance(v, list) and v}
        cbtf_oos = SC.slice_cbtf(cbtf_full, SC.OOS_LO, SC.OOS_HI)
        t_star = refined[name][0]

        def mk(engine, thr=None):
            kw = {}
            if thr is not None:
                kw = dict(scoring_full_threshold=float(thr),
                          scoring_half_threshold=float(thr),
                          scoring_alert_threshold=float(thr))
            return replace(SC.trade_cfg(s), score_engine=engine, **kw)

        cmp = {
            "V1_full": SC.metrics(SC.run(s, cbtf_full, mk(""), ex)),
            "V2old_full": SC.metrics(SC.run(s, cbtf_full, mk("v2"), ex)),
            "V2new_full": SC.metrics(SC.run(s, cbtf_full, mk("v2", t_star), ex)),
            "V1_oos": SC.metrics(SC.run(s, cbtf_oos, mk(""), ex)),
            "V2old_oos": SC.metrics(SC.run(s, cbtf_oos, mk("v2"), ex)),
            "V2new_oos": SC.metrics(SC.run(s, cbtf_oos, mk("v2", t_star), ex)),
        }
        data[name]["t_star"] = t_star
        data[name]["stable_range"] = refined[name][1]
        data[name]["compare"] = cmp

        band = refined[name][1]
        print(f"\n########## {name}  稳定区间={band}  t*={t_star} ##########",
              flush=True)
        print("  扫描曲线 (t / TR_E / OOS_E / TR_T / OOS_T / TR_PF / OOS_PF):",
              flush=True)
        for r in data[name]["scan"]:
            mark = " <=" if r["t"] == t_star else ""
            print(f"    t={r['t']:>3}: TR_E={r['train']['e']:>6.2f} "
                  f"OOS_E={r['oos']['e']:>6.2f} TR_T={r['train']['t']:>3} "
                  f"OOS_T={r['oos']['t']:>3} TR_PF={r['train']['pf']:>5.2f} "
                  f"OOS_PF={r['oos']['pf']:>5.2f}{mark}", flush=True)
        print("  最终对比 (E% / T / PF / WR% / DD% / PnL_U):", flush=True)
        for k in ["V1_full", "V2old_full", "V2new_full", "V1_oos",
                  "V2old_oos", "V2new_oos"]:
            m = cmp[k]
            print(f"    {k:12s}: E={m['e']:>6.2f} T={m['t']:>3} "
                  f"PF={m['pf']:>5.2f} WR={m['wr']:>5.1f} "
                  f"DD={m['dd']:>4.1f} PnL={m['pnl']:>7.2f}", flush=True)

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "_v2_threshold_scan.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {out_path}", flush=True)


if __name__ == "__main__":
    main()
