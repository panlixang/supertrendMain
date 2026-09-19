# -*- coding: utf-8 -*-
"""A 方案：给 quick(弱档)出场规则加回价格止损后重跑 tp1 寻优。

之前 quick 规则 enabled=False(无止损)，导致 tp1 越大越吃样本期顺风、曲线一路
冲顶到网格上界(3.0)——过拟合假峰。本脚本把 quick 规则改成 enabled=True + 硬
止损 sl_pct，使下行有界，从而得到一个"真实有界"的 tp1 最优。

对每个品种：
  - OFF(弱ER走标准出场)
  - ON_quick(sl=1.0 硬止损) 逐 tp1 ∈ [0.6,0.8,1.0,1.2,1.5,1.8,2.0,2.5,3.0]
  - 在 sl=1.0 最优 tp1 附近对 sl ∈ [0.6,0.8,1.0,1.5,2.0] 做止损敏感度扫描
统计弱ER单的 止盈离场数(n_tp1) / 被打止损数(n_stop)，看最优 tp1 下的风险结构。
"""
from __future__ import annotations
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _live_cfg_backtest import _get, trade_cfg, exit_rules  # noqa: E402
from _weak_profile_matrix import run_sym, LIVE_URLS, CACHE, DATA  # noqa: E402
from position_enhanced import EnhancedExitRules  # noqa: E402

SYMS = ["BTC", "ETH", "SPCX", "NVDA", "CL"]
TP1_GRID = [0.6, 0.8, 1.0, 1.2, 1.5, 1.8, 2.0, 2.5, 3.0]
SL_SWEEP = [0.6, 0.8, 1.0, 1.5, 2.0]


def make_quick_stop(tp1_pct, sl_pct=1.0):
    return EnhancedExitRules(
        enabled=True,
        tp1_pct=tp1_pct, tp1_ratio=100.0,
        tp2_pct=999.0, tp2_ratio=0.0,
        tp3_pct=999.0, tp3_ratio=0.0, tp3_mode="pct",
        move_sl_to_entry=False, trail_with_st=False,
        sl_mode="pct", sl_pct=sl_pct, sl_buffer_atr=0.3, sl_min_pct=sl_pct,
        protect_profit_at=999.0, protect_trail_pct=0.0,
        max_loss_enabled=False, max_loss_pct=8.0,
    )


def metrics(tl):
    if not tl:
        return {"t": 0, "pnl": 0.0, "e": 0.0, "wr": 0.0, "pf": 0.0}
    n = len(tl)
    wins = [t["pnl_pct"] for t in tl if t["pnl_pct"] > 0]
    loss = [t["pnl_pct"] for t in tl if t["pnl_pct"] <= 0]
    wr = len(wins) / n * 100
    aw = sum(wins) / len(wins) if wins else 0.0
    al = sum(loss) / len(loss) if loss else 0.0
    e = wr / 100 * aw - (1 - wr / 100) * abs(al)
    loss_abs = abs(sum(loss))
    gain = sum(wins)
    pf = gain / loss_abs if loss_abs > 0 else (gain if gain else 0.0)
    return {"t": n, "pnl": round(sum(t["pnl_pct"] for t in tl), 2),
            "e": round(e, 3), "wr": round(wr, 1), "pf": round(pf, 2)}


def summarize(r):
    if r is None or "error" in r:
        return None
    tl = r.get("trades_list") or r.get("trade_list") or []
    m = metrics(tl)
    quick = [t for t in tl if t.get("profile") == "quick"]
    n_tp1 = sum(1 for t in quick if "止盈1" in (t.get("reason") or ""))
    n_stop = sum(1 for t in quick if any(k in (t.get("reason") or "")
                                         for k in ("止损", "爆仓")))
    return {"overall": m, "quick_seg": metrics(quick), "n_quick": len(quick),
            "n_tp1": n_tp1, "n_stop": n_stop}


def main():
    t0 = time.time()
    sym_map: dict[str, dict] = {}
    for url in LIVE_URLS:
        try:
            d = _get(url)
            for x in d.get("symbols", []):
                sym_map.setdefault(x["symbol"].replace("-USDT-SWAP", "").replace("-USDT", ""), x)
        except Exception:
            continue

    results = {}
    for name in SYMS:
        if name not in sym_map or name not in CACHE:
            print(f"{name}: 缺失, 跳过"); continue
        sym = sym_map[name]
        cbtf = {k: v for k, v in json.load(open(os.path.join(DATA, CACHE[name]), encoding="utf-8")).items()
                if isinstance(v, list) and v and "ts" in (v[0] or {})}
        cfg = trade_cfg(sym)
        normal = exit_rules(sym)
        off = summarize(run_sym(sym, cbtf, cfg, normal, normal))
        if off is None or off["n_quick"] == 0:
            print(f"{name}: 无弱ER成交, 跳过"); continue
        off_pnl = off["overall"]["pnl"]

        # 1) sl=1.0 的 tp1 曲线
        curve = []
        for tp1 in TP1_GRID:
            s = summarize(run_sym(sym, cbtf, cfg, normal, make_quick_stop(tp1, 1.0)))
            if s is None:
                continue
            curve.append({"tp1": tp1, "pnl": s["overall"]["pnl"],
                          "e_quick": s["quick_seg"]["e"], "n_quick": s["n_quick"],
                          "n_tp1": s["n_tp1"], "n_stop": s["n_stop"],
                          "gain": s["overall"]["pnl"] - off_pnl})
        opt = max(curve, key=lambda c: c["pnl"])
        opt_tp1 = opt["tp1"]
        print(f"\n--- {name} (OFF整体pnl%={off_pnl}, 弱ER={off['n_quick']})  sl=1.0 ---")
        print(f"  {'tp1':>5s}{'整体pnl%':>10s}{'Δ':>8s}{'弱ER_E':>9s}{'止盈':>5s}{'止损':>5s}")
        for c in curve:
            mark = " *" if c["tp1"] == opt_tp1 else ""
            print(f"  {c['tp1']:>5g}{c['pnl']:>10.2f}{c['gain']:>+8.2f}{c['e_quick']:>9.3f}"
                  f"{c['n_tp1']:>5d}{c['n_stop']:>5d}{mark}")

        # 2) 止损敏感度：在 opt_tp1 附近对 sl 扫描
        sl_line = []
        for sl in SL_SWEEP:
            s = summarize(run_sym(sym, cbtf, cfg, normal, make_quick_stop(opt_tp1, sl)))
            if s is None:
                continue
            sl_line.append({"sl": sl, "pnl": s["overall"]["pnl"],
                            "n_stop": s["n_stop"], "n_tp1": s["n_tp1"]})
        best_sl = max(sl_line, key=lambda c: c["pnl"])
        print(f"  sl敏感度@tp1={opt_tp1:g}: " +
              "  ".join(f"sl={c['sl']:g}→pnl%={c['pnl']:.2f}(止盈{c['n_tp1']}/止损{c['n_stop']})"
                        for c in sl_line))
        print(f"  => sl=1.0最优 tp1={opt_tp1:g}(pnl%={opt['pnl']:.2f}); "
              f"全组合最优 sl={best_sl['sl']:g}/tp1={opt_tp1:g}(pnl%={best_sl['pnl']:.2f})")
        results[name] = {"off_pnl": off_pnl, "n_quick": off["n_quick"],
                         "opt_tp1_sl1": opt_tp1, "opt_pnl_sl1": opt["pnl"],
                         "best_sl": best_sl["sl"], "best_pnl": best_sl["pnl"],
                         "sl_line": sl_line, "curve": curve}

    print("\n=== 带止损(有界)tp1 寻优汇总 ===")
    print(f"  {'品种':<6s}{'OFF':>8s}{'sl1最优tp1':>12s}{'该点pnl%':>10s}"
          f"{'全组合sl':>9s}{'全组合pnl%':>11s}")
    for n, r in results.items():
        print(f"  {n:<6s}{r['off_pnl']:>8.2f}{r['opt_tp1_sl1']:>12g}{r['opt_pnl_sl1']:>10.2f}"
              f"{r['best_sl']:>9g}{r['best_pnl']:>11.2f}")
    print(f"\n耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
