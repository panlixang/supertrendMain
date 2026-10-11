# -*- coding: utf-8 -*-
"""43 首页 5 品种（AMD/BTC/ETH/INTC/XAU）出场 tp1 寻优。

固定各自当前线上 ST + 评分闸门（即已落地的最优值），只扫
  tp1_pct ∈ [0.5,0.8,1.0,1.5,2.0]
  tp1_ratio ∈ [30,50,70]
每个组合算 全样本 与 后 40% OOS 的 Σ净%，并打 pct×ratio 矩阵看稳定性。
口径与 bt_home_2026 / bt_home_opt4 完全一致：固定 100U × 1x。
"""
from __future__ import annotations

import os
import sys
import time

import bt_home_2026 as H
from bt_home_2026 import (M, run_backtest, stats, y26_lo,
                          BARS26, MARGIN, LEV, INIT)

SYMS = ["AMD", "BTC", "ETH", "INTC", "XAU"]
TFS = ["1h", "4h", "1d"]
TP1_PCT = [0.5, 0.8, 1.0, 1.5, 2.0]
TP1_RATIO = [30, 50, 70]


def run(seg, cbtf_seg, sym):
    ex = M.exit_rules(sym)
    cfg = M.trade_cfg(sym)
    r = run_backtest(seg, sym["params"], init_cash=INIT, fee_rate=0.0005,
                     allow_short=True, exit_rules=ex, sizing="fixed",
                     margin_usdt=MARGIN, leverage=LEV, live_gate=cfg,
                     gate_tf=sym["allow_tfs"][0], candles_by_tf=cbtf_seg)
    if "error" in r:
        return None
    return stats(r)


def bh(seg):
    return (seg[-1]["c"] / seg[0]["c"] - 1) * 100 if len(seg) > 1 else 0.0


def main():
    t0 = time.time()
    live = H.load_live()
    base = {nm: live["43"][nm] for nm in SYMS if live["43"].get(nm)}
    if len(base) < len(SYMS):
        print("!! 43 首页缺少:", [s for s in SYMS if s not in base])

    for nm, sym in base.items():
        print("\n" + "=" * 100)
        p = sym["params"]
        print(f"【{nm}】{sym['symbol']}  ST {p['periods']}×{p['multiplier']}/{p['src']}  "
              f"se={sym.get('score_engine')!r} thr={sym.get('scoring_full_threshold')} "
              f"range={sym.get('range_filter_enabled')}")
        print(f"   当前出场: tp1={sym['exit_rules'].get('tp1_pct')}/"
              f"{sym['exit_rules'].get('tp1_ratio')} sl={sym['exit_rules'].get('sl_pct')}"
              f"({sym['exit_rules'].get('sl_mode')}) trail={sym['exit_rules'].get('trail_with_st')}")
        print("=" * 100)

        cbtf = {}
        for tf in TFS:
            arr = M.fetch_candles(sym["symbol"], tf, BARS26[tf])
            arr = [c for c in arr if c["ts"] >= y26_lo()]
            cbtf[tf] = arr
        seg = cbtf.get("1h") or []
        if len(seg) < 100:
            print("   !! 1h 数据不足，跳过")
            continue
        hi = seg[-1]["ts"]
        cbtf_seg = {tf: [c for c in a if y26_lo() <= c["ts"] <= hi]
                    for tf, a in cbtf.items() if a}
        k = int(len(seg) * 0.60)
        test0 = seg[k]["ts"]
        seg_test = seg[k:]
        cbtf_test = {tf: [c for c in a if c["ts"] >= test0]
                     for tf, a in cbtf_seg.items() if a}
        print(f"   1h {len(seg)} 根 {M.ts_fmt(seg[0]['ts'])}~{M.ts_fmt(hi)}  "
              f"买入持有 全 {bh(seg):+.2f}% / OOS {bh(seg_test):+.2f}%")

        rows = []
        for pct in TP1_PCT:
            for ratio in TP1_RATIO:
                s = dict(sym)
                s["exit_rules"] = dict(sym["exit_rules"])
                s["exit_rules"]["tp1_pct"] = pct
                s["exit_rules"]["tp1_ratio"] = ratio
                st = run(seg, cbtf_seg, s)
                if not st or st["n"] < 10:
                    continue
                stt = run(seg_test, cbtf_test, s)
                rows.append(dict(pct=pct, ratio=ratio, tot=st["tot"], n=st["n"],
                                 pf=st["pf"], mdd=st["mdd_u"], wr=st["wr"],
                                 oos=(stt["tot"] if stt else 0.0)))
        if not rows:
            print("   !! 无有效组合")
            continue
        rows.sort(key=lambda r: r["tot"], reverse=True)
        print(f"\n   有效 {len(rows)} 组，Top10（按全样本Σ净%）：")
        print(f"   {'#':>3} {'tp1%':>6} {'ratio':>6} {'笔':>5} {'Σ%':>8} "
              f"{'PF':>6} {'回撤U':>7} {'胜率':>6} {'OOS%':>8}")
        for i, r in enumerate(rows[:10], 1):
            print(f"   {i:>3} {r['pct']:>6.1f} {r['ratio']:>6} {r['n']:>5} "
                  f"{r['tot']:>8.2f} {r['pf']:>6.2f} {r['mdd']:>7.2f} {r['wr']:>6.1f} "
                  f"{r['oos']:>8.2f}")

        lut = {(r["pct"], r["ratio"]): r["tot"] for r in rows}
        print(f"\n   tp1_pct × tp1_ratio 全样本Σ净%（矩阵）：")
        print("         " + "".join(f"r{r:>5}" for r in TP1_RATIO))
        for pct in TP1_PCT:
            line = f"   {pct:>4.1f}%"
            for r in TP1_RATIO:
                v = lut.get((pct, r))
                line += f"{'—':>7}" if v is None else f"{v:>7.2f}"
            print(line)

        best = rows[0]
        # 同时给出“全样本与OOS都正且最小者最大”的稳健选择
        robust = [r for r in rows if r["oos"] > 0]
        if robust:
            robust.sort(key=lambda r: r["oos"], reverse=True)
            rb = robust[0]
            flag = "（OOS 最优）"
        else:
            rb = best
            flag = "（无OOS为正，取全样本最优）"
        print(f"\n   ★ {nm} 推荐 tp1 = {best['pct']}/{best['ratio']}  "
              f"→ 全样本 {best['tot']:+.2f}%  OOS {best['oos']:+.2f}%  "
              f"PF {best['pf']:.2f} 回撤 {best['mdd']:.2f}U 胜率 {best['wr']:.1f}%")
        print(f"   ★ 稳健档 {flag}: tp1 = {rb['pct']}/{rb['ratio']}  "
              f"→ 全样本 {rb['tot']:+.2f}%  OOS {rb['oos']:+.2f}%")

    print(f"\n总耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
