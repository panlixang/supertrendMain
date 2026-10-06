# -*- coding: utf-8 -*-
"""纯 MA30 生命线系统 —— 不加 SuperTrend / 不加时段 / 不加止盈止损
只报告: 胜率、盈亏比、最大连续亏损
"""
import sys

sys.path.insert(0, r"d:/个人项目代码/supertrendMain")
from backtest_lifeline_ma30_15m import (
    load, build_signals, backtest, filt, SYMBOL, MA_N, INIT_CAP, pfstr,
)

TFS = ["15m", "1h", "4h"]
MA_TYPES = ["EMA", "SMA"]
MODES = [("多空双向", False, True), ("只做多", True, True)]


def consec_loss(trades):
    """返回 (最长连亏笔数, 连亏过程中最深的资金回撤%)"""
    eq = INIT_CAP
    streak = 0
    best_cnt = 0
    worst_pct = 0.0
    run = 0.0
    ref = INIT_CAP
    for t in trades:
        if t["pnl"] <= 0:
            if streak == 0:
                ref = eq
            streak += 1
            run += t["pnl"]
            best_cnt = max(best_cnt, streak)
            worst_pct = min(worst_pct, run / ref * 100)
        else:
            streak = 0
            run = 0.0
        eq += t["pnl"]
    return best_cnt, worst_pct


def main():
    print("纯 MA30 生命线系统 · 买1/买2/卖1/卖2 · 无过滤 无时段 无止盈止损")
    print("=" * 96)
    print(f"  {'周期':<6}{'均线':<5}{'模式':<10}{'笔数':>6}{'胜率':>8}{'盈亏比':>8}"
          f"{'最长连亏':>9}{'连亏最深回撤':>14}{'净收益':>10}{'最大回撤':>10}")
    print("-" * 96)
    for tf in TFS:
        base = load(SYMBOL, tf)
        for ma_type in MA_TYPES:
            sigs, o, h, l, c, ts = build_signals(base, ma_type)
            pure = filt(sigs, "none", "none")   # 不加 ST、不加时段
            for mode, long_only, rev in MODES:
                r = backtest(pure, c, h, l, None, None, long_only, rev)
                cnt, worst = consec_loss(r["trades"])
                print(f"  {tf:<6}{ma_type}{MA_N:<3}{mode:<10}{r['n']:>6}{r['win_rate']:>7.1f}%"
                      f"{pfstr(r['pf']):>8}{cnt:>8}笔{worst:>13.2f}%"
                      f"{r['total_ret']:>9.2f}%{r['max_dd']:>9.2f}%")
        print("-" * 96)


if __name__ == "__main__":
    main()
