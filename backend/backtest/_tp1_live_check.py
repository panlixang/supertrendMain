# -*- coding: utf-8 -*-
"""弱档 tp1 在【实盘真实止损】下的再评估。

背景(两个已确认的坑):
  1) 原寻优 _weak_profile_tune.py 用 make_quick(enabled=False)，而
     position_enhanced.check_enhanced 里 enabled 只控制价格止损、止盈照常。
     => 原寻优是【完全无止损】下跑的，系统性高估高 tp1。
  2) 实盘 executor.py 用 enhanced_initial_stop(..., cfg.leverage)，
     sl_pct 会乘 lev_factor(10x -> 2.5)。配置 sl_pct=1.5 => 实际止损 3.75% 价格。
     回测默认走 position.initial_stop（不放大），所以这里直接把 sl_pct 设成
     3.75 来等价模拟实盘 10x 的止损距离。

对比两个口径:
  A 无止损(原寻优口径)      : enabled=False, sl_pct=1.0
  B 实盘真实止损(3.75%价格) : enabled=True,  sl_pct=3.75, sl_mode="pct"
输出每个品种在 tp1 网格下的弱ER段: 笔数 / 胜率 / 单笔期望E% / 总pnl%。
并给出该 tp1 在该止损下的【保本胜率】，用于判断 R:R 是否成立。
"""
from __future__ import annotations
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _live_cfg_backtest import _get, trade_cfg, exit_rules  # noqa: E402
from _weak_profile_matrix import (run_sym, summarize, LIVE_URLS, CACHE, DATA)  # noqa: E402
from position_enhanced import EnhancedExitRules  # noqa: E402

SYMS = ["BTC", "ETH", "SPCX", "NVDA", "CL"]
TP1_GRID = [1.0, 1.2, 1.3, 1.5, 1.8, 2.0, 2.5, 3.0]
# 实盘: 配置 sl_pct=1.5, 杠杆10x -> lev_factor=2.5 -> 实际止损 3.75% 价格
LIVE_STOP_PCT = 1.5 * 2.5


def make_quick(tp1_pct, stop_pct, enabled):
    if not enabled:
        # 原寻优口径：enabled=False => 无价格止损，止盈照常
        return EnhancedExitRules(
            enabled=False,
            tp1_pct=tp1_pct, tp1_ratio=100.0,
            tp2_pct=999.0, tp2_ratio=0.0,
            tp3_pct=999.0, tp3_ratio=0.0, tp3_mode="pct",
            move_sl_to_entry=False, trail_with_st=False,
            sl_mode="pct", sl_pct=1.0, sl_buffer_atr=0.3, sl_min_pct=1.0,
            protect_profit_at=999.0, protect_trail_pct=0.0,
            max_loss_enabled=False, max_loss_pct=8.0,
        )
    # 实盘口径：有止损，止损距离 = stop_pct% 价格
    return EnhancedExitRules(
        enabled=True,
        tp1_pct=tp1_pct, tp1_ratio=100.0,
        tp2_pct=999.0, tp2_ratio=0.0,
        tp3_pct=999.0, tp3_ratio=0.0, tp3_mode="pct",
        move_sl_to_entry=False, trail_with_st=False,
        sl_mode="pct", sl_pct=stop_pct, sl_buffer_atr=0.3, sl_min_pct=stop_pct,
        protect_profit_at=999.0, protect_trail_pct=0.0,
        max_loss_enabled=False, max_loss_pct=8.0,
    )


def main():
    t0 = time.time()
    sym_map: dict[str, dict] = {}
    for url in LIVE_URLS:
        try:
            d = _get(url)
            for x in d.get("symbols", []):
                sym_map.setdefault(
                    x["symbol"].replace("-USDT-SWAP", "").replace("-USDT", ""), x)
        except Exception:
            continue

    print(f"实盘止损模拟: 配置sl_pct=1.5 × lev_factor(10x)=2.5 => 止损距离 {LIVE_STOP_PCT}% 价格\n")
    summary = {}

    for name in SYMS:
        if name not in sym_map or name not in CACHE:
            print(f"{name}: 缺失, 跳过"); continue
        sym = sym_map[name]
        cache = os.path.join(DATA, CACHE[name])
        if not os.path.exists(cache):
            print(f"{name}: 无缓存, 跳过"); continue
        cbtf = {k: v for k, v in json.load(open(cache, encoding="utf-8")).items()
                if isinstance(v, list) and v and "ts" in (v[0] or {})}
        cfg = trade_cfg(sym)
        normal = exit_rules(sym)

        print(f"===== {name} (lev={sym.get('leverage')}) =====")
        print(f"  {'tp1':>5s} | {'A无止损: n':>10s}{'胜率%':>8s}{'E%':>8s}{'pnl%':>9s}"
              f" | {'B实盘止损: n':>12s}{'胜率%':>8s}{'E%':>8s}{'pnl%':>9s} | 保本胜率%")
        rows = []
        for tp1 in TP1_GRID:
            sa = summarize(run_sym(sym, cbtf, cfg, normal,
                                   make_quick(tp1, LIVE_STOP_PCT, enabled=False)))
            sb = summarize(run_sym(sym, cbtf, cfg, normal,
                                   make_quick(tp1, LIVE_STOP_PCT, enabled=True)))
            if sa is None or sb is None:
                continue
            qa, qb = sa["quick_seg"], sb["quick_seg"]
            be = LIVE_STOP_PCT / (tp1 + LIVE_STOP_PCT) * 100
            print(f"  {tp1:>5g} | {qa['t']:>10d}{qa['wr']:>8.1f}{qa['e']:>8.3f}{qa['pnl']:>9.2f}"
                  f" | {qb['t']:>12d}{qb['wr']:>8.1f}{qb['e']:>8.3f}{qb['pnl']:>9.2f}"
                  f" | {be:>7.1f}")
            rows.append({"tp1": tp1, "a": qa, "b": qb, "be_wr": round(be, 1)})
        if not rows:
            continue
        best_a = max(rows, key=lambda r: r["a"]["pnl"])
        best_b = max(rows, key=lambda r: r["b"]["pnl"])
        good_b = [r for r in rows if r["b"]["wr"] >= r["be_wr"] and r["b"]["e"] > 0]
        print(f"  -> A(无止损)最优 tp1={best_a['tp1']:g} pnl%={best_a['a']['pnl']:.2f}"
              f" 胜率={best_a['a']['wr']:.1f}%")
        print(f"  -> B(实盘止损)最优 tp1={best_b['tp1']:g} pnl%={best_b['b']['pnl']:.2f}"
              f" 胜率={best_b['b']['wr']:.1f}% (保本需{next(r['be_wr'] for r in rows if r['tp1']==best_b['tp1']):.1f}%)")
        print(f"  -> B下【期望为正且胜率达标】的 tp1: "
              f"{[r['tp1'] for r in good_b] or '无'}")
        summary[name] = rows
        print()

    json.dump({"live_stop_pct": LIVE_STOP_PCT, "rows": summary},
              open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "_tp1_live_check.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2, default=str)
    print(f"耗时 {time.time()-t0:.0f}s | Wrote _tp1_live_check.json")


if __name__ == "__main__":
    main()
