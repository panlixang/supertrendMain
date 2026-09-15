# -*- coding: utf-8 -*-
"""严格 Walk-Forward / Rolling-Window 验证。

协议：
- 4 折，每折 训练 3 个月 / 测试 1 个月，按月滚动：
    F1  训练 03~05  测试 06
    F2  训练 04~06  测试 07
    F3  训练 05~07  测试 08
    F4  训练 06~08  测试 09
- 每折：ONLY 在「训练区」扫描 28~80 选最佳阈值 → 锁定 → 应用到「测试区」。
  测试区绝不重新调参，得到真实 OOS 结果。
- 训练区选阈值的规则（in-sample，但仅用于该折锁定）：
    候选 = 训练 E>0 且 PF>1 且 训练交易数>=10 的连续阈值段；
    取「段内最高 E 所在段」的中位阈值，避免单点尖峰过拟合。
- 汇总：跨折合并测试交易，算 pooled E / PF / WR / PnL / maxDD，与单折 E 一起看。

仅做校准与验证，不新增策略模块。
"""
from __future__ import annotations
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _v2_threshold_scan as SC  # metrics/run/slice_cbtf/fetch_symbols/exit_rules/trade_cfg/max_contiguous/THRESHOLDS/DATA/SYMS

STEP = 4
THRESHOLDS = SC.THRESHOLDS

def TS(y, m, d, h=0, mi=0):
    return int(datetime(y, m, d, h, mi, tzinfo=timezone.utc).timestamp() * 1000)
FOLDS = [
    ("F1", TS(2026, 3, 1), TS(2026, 5, 31, 23, 59), TS(2026, 6, 1), TS(2026, 6, 30, 23, 59)),
    ("F2", TS(2026, 4, 1), TS(2026, 6, 30, 23, 59), TS(2026, 7, 1), TS(2026, 7, 31, 23, 59)),
    ("F3", TS(2026, 5, 1), TS(2026, 7, 31, 23, 59), TS(2026, 8, 1), TS(2026, 8, 31, 23, 59)),
    ("F4", TS(2026, 6, 1), TS(2026, 8, 31, 23, 59), TS(2026, 9, 1), TS(2026, 9, 30, 23, 59)),
]


def select_in_train(scan):
    cands = [r for r in scan
             if r["train"]["e"] > 0 and r["train"]["pf"] > 1 and r["train"]["t"] >= 10]
    if not cands:
        cands = [r for r in scan if r["train"]["t"] >= 5]
    if not cands:
        return None
    # 连续段
    segs, cur = [], []
    for r in sorted(cands, key=lambda x: x["t"]):
        if not cur or r["t"] - cur[-1]["t"] <= STEP:
            cur.append(r)
        else:
            segs.append(cur)
            cur = [r]
    if cur:
        segs.append(cur)
    best_seg = max(segs, key=lambda s: max(x["train"]["e"] for x in s))
    best_seg = sorted(best_seg, key=lambda x: x["t"])
    return best_seg[len(best_seg) // 2]["t"]


def pool_from(r):
    n = r.get("trades", 0) or 0
    if not n:
        return {"n": 0, "gp": 0.0, "gl": 0.0, "pnl": 0.0, "dd": 0.0, "wins": 0}
    wr = (r.get("win_rate") or 0.0) / 100.0
    aw = r.get("avg_win") or 0.0
    al = abs(r.get("avg_loss") or 0.0)
    wins = round(n * wr)
    gp = wins * aw
    gl = (n - wins) * al
    return {"n": n, "gp": gp, "gl": gl,
            "pnl": (r.get("final", 100) - 100),
            "dd": r.get("max_dd_pct", 0) or 0.0, "wins": wins}


def main():
    t0 = time.time()
    syms = SC.fetch_symbols()
    sym_map = {x["symbol"].replace("-USDT-SWAP", "").replace("-USDT", ""): x
               for x in syms}

    results = {}
    print(f"{'SYM':5s} {'FOLD':4s} {'训练区间':16s} {'测试区间':16s} "
          f"{'选阈值':>5s} {'测T':>4s} {'测E':>7s} {'PF':>6s} {'WR%':>5s} {'DD%':>5s} {'PnL':>7s}",
          flush=True)

    for name in SC.SYMS:
        if name not in sym_map:
            print(f"{name:5s} 不在线上配置", flush=True)
            continue
        s = sym_map[name]
        ex = SC.exit_rules(s)
        gate_tf = s["allow_tfs"][0]
        cache = os.path.join(SC.DATA, f"{name}.json")
        if not os.path.exists(cache):
            print(f"{name:5s} 缓存缺失", flush=True)
            continue
        cbtf_full = {k: v for k, v in json.load(open(cache, encoding="utf-8")).items()
                     if isinstance(v, list) and v}
        candles_full = cbtf_full.get(gate_tf)
        if not candles_full or len(candles_full) < 200:
            print(f"{name:5s} 数据不足", flush=True)
            continue

        sym_rows = []
        agg = {"n": 0, "gp": 0.0, "gl": 0.0, "pnl": 0.0, "dd": 0.0, "wins": 0,
               "folds_ok": 0, "folds_total": 0}

        for fid, tr_lo, tr_hi, te_lo, te_hi in FOLDS:
            cbtf_tr = SC.slice_cbtf(cbtf_full, tr_lo, tr_hi)
            cbtf_te = SC.slice_cbtf(cbtf_full, te_lo, te_hi)
            tr_lo_s = datetime.fromtimestamp(tr_lo / 1000, tz=timezone.utc).strftime("%m-%d")
            tr_hi_s = datetime.fromtimestamp(tr_hi / 1000, tz=timezone.utc).strftime("%m-%d")
            te_lo_s = datetime.fromtimestamp(te_lo / 1000, tz=timezone.utc).strftime("%m-%d")
            te_hi_s = datetime.fromtimestamp(te_hi / 1000, tz=timezone.utc).strftime("%m-%d")

            # 训练区扫描选阈值
            scan = []
            for t in THRESHOLDS:
                cfg = _cfg(s, "v2", t)
                m = SC.metrics(SC.run(s, cbtf_tr, cfg, ex))
                scan.append({"t": t, "train": m})
            t_star = select_in_train(scan)
            if t_star is None:
                print(f"{name:5s} {fid:4s} {tr_lo_s}~{tr_hi_s} {te_lo_s}~{te_hi_s} "
                      f" 训练数据不足 N/A", flush=True)
                sym_rows.append({"fold": fid, "t_star": None, "test": None})
                continue

            # 锁定 t_star 跑测试区
            cfg_te = _cfg(s, "v2", t_star)
            r_te = SC.run(s, cbtf_te, cfg_te, ex)
            m_te = SC.metrics(r_te)

            p = pool_from(r_te)
            agg["n"] += p["n"]
            agg["gp"] += p["gp"]
            agg["gl"] += p["gl"]
            agg["pnl"] += p["pnl"]
            agg["dd"] = max(agg["dd"], p["dd"])
            agg["wins"] += p["wins"]
            agg["folds_total"] += 1
            if m_te["e"] > 0 and m_te["pf"] > 1:
                agg["folds_ok"] += 1

            ok = "OK" if (m_te["e"] > 0 and m_te["pf"] > 1) else "BAD"
            print(f"{name:5s} {fid:4s} {tr_lo_s}~{tr_hi_s} {te_lo_s}~{te_hi_s} "
                  f"  t={t_star:>3} T={m_te['t']:>4} E={m_te['e']:>7.2f} "
                  f"PF={m_te['pf']:>6.2f} WR={m_te['wr']:>5.1f} DD={m_te['dd']:>5.1f} "
                  f"PnL={m_te['pnl']:>7.2f} {ok}", flush=True)
            sym_rows.append({"fold": fid, "t_star": t_star, "test": m_te})

        # 汇总
        n = agg["n"]
        pooled_e = round((agg["gp"] - agg["gl"]) / n, 3) if n else 0.0
        pooled_pf = round(agg["gp"] / agg["gl"], 2) if agg["gl"] > 0 else 0.0
        pooled_wr = round(100.0 * agg["wins"] / n, 1) if n else 0.0
        passed = (pooled_e > 0 and pooled_pf > 1 and agg["folds_ok"] >= (agg["folds_total"] + 1) // 2)
        summary = {
            "oos_trades": n, "oos_e": pooled_e, "oos_pf": pooled_pf,
            "oos_wr": pooled_wr, "oos_dd": round(agg["dd"], 1),
            "oos_pnl": round(agg["pnl"], 2),
            "folds_ok": agg["folds_ok"], "folds_total": agg["folds_total"],
            "passed": passed,
        }
        results[name] = {"rows": sym_rows, "summary": summary}
        print(f"  >> {name} OOS汇总: T={n} E={pooled_e} PF={pooled_pf} "
              f"WR={pooled_wr}% DD={summary['oos_dd']}% PnL={summary['oos_pnl']} "
              f"通过折={agg['folds_ok']}/{agg['folds_total']} "
              f"=> {'PASS' if passed else 'FAIL'}\n", flush=True)

    # 总表
    print("="*100, flush=True)
    print("汇总表（跨折合并 OOS）：", flush=True)
    print(f"{'SYM':5s} {'OOS_T':>6s} {'OOS_E':>7s} {'OOS_PF':>7s} {'OOS_WR':>6s} "
          f"{'OOS_DD':>6s} {'OOS_PnL':>8s} {'通过折':>7s} {'结论':>5s}", flush=True)
    for name, d in results.items():
        sm = d["summary"]
        print(f"{name:5s} {sm['oos_trades']:>6} {sm['oos_e']:>7.2f} {sm['oos_pf']:>7.2f} "
              f"{sm['oos_wr']:>6.1f} {sm['oos_dd']:>6.1f} {sm['oos_pnl']:>8.2f} "
              f"{sm['folds_ok']}/{sm['folds_total']:>3} "
              f"{'PASS' if sm['passed'] else 'FAIL':>5s}", flush=True)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "_v2_walkforward.json")
    json.dump(results, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\nWrote {out} | 耗时 {time.time()-t0:.0f}s", flush=True)


def _cfg(s, engine, t):
    from dataclasses import replace
    return replace(SC.trade_cfg(s), score_engine=engine,
                   scoring_full_threshold=float(t),
                   scoring_half_threshold=float(t),
                   scoring_alert_threshold=float(t))


if __name__ == "__main__":
    main()
