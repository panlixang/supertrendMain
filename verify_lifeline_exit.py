# -*- coding: utf-8 -*-
"""出场口径对照: 验证"+227.5%"里有多少来自'出场也被过滤'造成的平仓延迟

甲  : 入场过滤(ST+美盘), 出场也用同一份过滤信号   ← 上一轮报出来的口径
甲' : 入场过滤(ST+美盘), 出场只用 ST 方向过滤(不看时段)
乙  : 入场过滤(ST+美盘), 出场用原始卖点(完全不过滤) ← 最严谨、最贴近你的规则
"""
import sys

sys.path.insert(0, r"d:/个人项目代码/supertrendMain")
from backtest_lifeline_ma30_15m import (
    load, build_signals, backtest, filt, year_ranges,
    SYMBOL, MA_N, pfstr,
)

TASKS = [("1h", "SMA", "只做多"), ("1h", "SMA", "多空双向"), ("4h", "SMA", "只做多")]
MODES = {"只做多": (True, True), "多空双向": (False, True)}


def main():
    for tf, ma, mode in TASKS:
        base = load(SYMBOL, tf)
        sigs, o, h, l, c, ts = build_signals(base, ma)
        long_only, rev = MODES[mode]
        entry = filt(sigs, "agree", "us")
        variants = [
            ("甲  出场=ST+时段过滤(上一轮口径)", entry),
            ("甲' 出场=仅ST过滤", filt(sigs, "agree", "none")),
            ("乙  出场=原始卖点(全不过滤)", sigs),
        ]
        yr = year_ranges(ts)
        years = sorted(yr)
        print("\n" + "=" * 104)
        print(f"  {tf} · {ma}{MA_N} · {mode} · 入场=ST方向过滤+美盘 · 纯规则出场(无止盈止损)")
        print("=" * 104)
        print(f"  {'出场口径':<30}{'笔数':>6}{'胜率':>8}{'盈亏比':>8}{'净收益':>11}{'最大回撤':>10}")
        print("-" * 104)
        for label, ex in variants:
            r = backtest(entry, c, h, l, None, None, long_only, rev, exit_list=ex)
            print(f"  {label:<30}{r['n']:>6}{r['win_rate']:>7.1f}%{pfstr(r['pf']):>8}"
                  f"{r['total_ret']:>10.2f}%{r['max_dd']:>9.2f}%")
            ys = "  ".join(
                f"{y}:{backtest(entry, c, h, l, None, None, long_only, rev, yr[y][0], yr[y][1], exit_list=ex)['total_ret']:>7.2f}%"
                for y in years)
            print(f"  {'  逐年:':<30}{ys}")


if __name__ == "__main__":
    main()
