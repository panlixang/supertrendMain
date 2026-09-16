# -*- coding: utf-8 -*-
"""V2 阈值寻优 —— 窗口 2026-03-01 ~ 2026-09-30（半年）。

复用 _v2_threshold_scan 的 gate 语义（score_engine='v2', full=half=alert=t），
在 03-09 单窗口上扫描阈值，按稳健准则选 t*：
  - 候选 t：03-09 上 E>0 且 PF>1 且 成交数 >= 0.5*V1成交数（不过度剔单）
  - t* = 候选中“连续合格段”的中位（避免取边缘最优点）
若无可连续候选：取满足成交下限中 E 最大者；再无：取 E 最大者。
选完后做稳健性校验：把 03-09 切成 03-06 / 07-09 两段，看 t* 在两段是否都 E>0、PF>1。
同时对比旧阈值 40/44/44。
"""
from __future__ import annotations
import json
import os
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _v2_threshold_scan import (fetch_symbols, slice_cbtf, exit_rules,
                                trade_cfg, DATA, metrics, run)  # noqa: E402

OPT_LO = int(datetime(2026, 3, 1, tzinfo=timezone.utc).timestamp() * 1000)
OPT_HI = int(datetime(2026, 9, 30, 23, 59, tzinfo=timezone.utc).timestamp() * 1000)
S1_LO = int(datetime(2026, 3, 1, tzinfo=timezone.utc).timestamp() * 1000)
S1_HI = int(datetime(2026, 6, 30, 23, 59, tzinfo=timezone.utc).timestamp() * 1000)
S2_LO = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp() * 1000)
S2_HI = int(datetime(2026, 9, 30, 23, 59, tzinfo=timezone.utc).timestamp() * 1000)
THRESHOLDS = list(range(28, 84, 2))
V2_SYMS = ["MU", "ETH", "SPCX"]
OLD = {"MU": 40.0, "ETH": 44.0, "SPCX": 44.0}


def mk_cfg(s, t):
    return replace(trade_cfg(s), score_engine="v2",
                   scoring_full_threshold=float(t),
                   scoring_half_threshold=float(t),
                   scoring_alert_threshold=float(t))


def max_contiguous(vals, step=2):
    vals = sorted(vals)
    best = cur = []
    for v in vals:
        if not cur or v - cur[-1] <= step:
            cur.append(v)
        else:
            if len(cur) > len(best):
                best = cur
            cur = [v]
    if len(cur) > len(best):
        best = cur
    return best


def main():
    t0 = time.time()
    syms = fetch_symbols()
    sym_map = {x["symbol"].replace("-USDT-SWAP", "").replace("-USDT", ""): x
               for x in syms}
    out = {}

    print("=== V2 阈值寻优  窗口 03-01 ~ 09-30（半年）===\n")
    for name in V2_SYMS:
        if name not in sym_map:
            print(f"!! {name} 不在线上配置，跳过")
            continue
        s = sym_map[name]
        ex = exit_rules(s)
        cache = os.path.join(DATA, f"{name}.json")
        if not os.path.exists(cache):
            print(f"!! {name} 缓存缺失 {cache}")
            continue
        cbtf_full = {k: v for k, v in json.load(open(cache, encoding="utf-8")).items()
                     if isinstance(v, list) and v}
        cbtf_opt = slice_cbtf(cbtf_full, OPT_LO, OPT_HI)
        v1 = metrics(run(s, cbtf_opt, replace(trade_cfg(s), score_engine=""), ex))
        floor = 0.5 * v1["t"]

        scan = []
        for t in THRESHOLDS:
            m = metrics(run(s, cbtf_opt, mk_cfg(s, t), ex))
            scan.append({"t": t, "trades": m["t"], "pnl": m["pnl"], "e": m["e"],
                        "pf": m["pf"], "dd": m["dd"], "wr": m["wr"]})

        qual = [r for r in scan if r["e"] > 0 and r["pf"] > 1 and r["trades"] >= floor]
        if qual:
            seg = max_contiguous([r["t"] for r in qual])
            t_star = seg[len(seg) // 2]
        else:
            cand = [r for r in scan if r["trades"] >= floor]
            t_star = max(cand, key=lambda r: r["e"])["t"] if cand else \
                max(scan, key=lambda r: r["e"])["t"]

        # 稳健性校验：03-06 与 07-09 两段
        cbtf_s1 = slice_cbtf(cbtf_full, S1_LO, S1_HI)
        cbtf_s2 = slice_cbtf(cbtf_full, S2_LO, S2_HI)
        m_s1 = metrics(run(s, cbtf_s1, mk_cfg(s, t_star), ex))
        m_s2 = metrics(run(s, cbtf_s2, mk_cfg(s, t_star), ex))
        m_old = metrics(run(s, cbtf_opt, mk_cfg(s, OLD[name]), ex))
        m_star = next(r for r in scan if r["t"] == t_star)

        out[name] = {"v1": v1, "floor_trades": floor, "t_star": t_star,
                     "old_thr": OLD[name], "scan": scan,
                     "at_star": m_star, "at_old": m_old,
                     "s1_0306": m_s1, "s2_0709": m_s2}

        print(f"--- {name}  V1成交={v1['t']}  成交下限(floor)={floor:.0f}  "
              f"旧阈值={OLD[name]:g} ---")
        print(f"  {'t':>3s} {'T':>4s} {'收益%':>7s} {'E%':>7s} {'PF':>5s} "
              f"{'WR%':>5s} {'DD%':>5s}  {'合格':>3s}")
        for r in scan:
            ok = "Y" if (r["e"] > 0 and r["pf"] > 1 and r["trades"] >= floor) else ""
            mark = " <== t*" if r["t"] == t_star else ""
            print(f"  {r['t']:>3d} {r['trades']:>4d} {r['pnl']:>7.2f} "
                  f"{r['e']:>7.2f} {r['pf']:>5.2f} {r['wr']:>5.1f} "
                  f"{r['dd']:>5.1f}  {ok:>3s}{mark}")
        print(f"  >> t*={t_star}  | E={m_star['e']} PF={m_star['pf']} "
              f"T={m_star['trades']}  vs 旧{t_star and OLD[name]:g} E={m_old['e']} "
              f"PF={m_old['pf']} T={m_old['t']}")
        print(f"  稳健性: 03-06 E={m_s1['e']} PF={m_s1['pf']} T={m_s1['t']} | "
              f"07-09 E={m_s2['e']} PF={m_s2['pf']} T={m_s2['t']}")
        chk = "稳定" if (m_s1["e"] > 0 and m_s1["pf"] > 1 and m_s2["e"] > 0
                         and m_s2["pf"] > 1) else "不稳(某段 E<=0 或 PF<=1)"
        print(f"  校验: {chk}")

    json.dump(out,
              open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "_v2_threshold_opt_0309.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"\nWrote _v2_threshold_opt_0309.json | 总耗时 {time.time() - t0:.0f}s",
          flush=True)


if __name__ == "__main__":
    main()
