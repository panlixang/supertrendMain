# -*- coding: utf-8 -*-
"""
③ 根因定位：为什么「剩余 30% 等反向信号平」在生产出场下一次都没发生
================================================================================

现象
----
在 BTC 1h、生产出场（tp1=1.5%/70% + 保本 + 跟超趋线）下跑 949 笔，
`reverse_count = 0`。也就是 `position.py` 文档写的设计：

    「分批止盈 1.5% 平 70% → 止损抬到开仓价 → 剩余 30% 等反向信号出现时全部平掉」

**最后那一步从未被触发**，剩余仓位 100% 被止损/保本止损先平掉。

嫌疑对象（`ExitRules` 里同时打开的两条）
----------------------------------------
1. `move_sl_to_entry=True`  止盈后把止损抬到开仓价
2. `trail_with_st=True`     剩余仓位跟随超趋线移动止损

只要这两条开着，止损线就会一路贴着价格走，**必然比"等下一个反向翻向"先触发**。

本脚本用 2×2 矩阵把罪魁钉出来，并量化"止损比反向信号早多少根 K 线"。

用法
----
    cd backend
    python sl2_exitdiag.py
    python sl2_exitdiag.py --symbol BTC-USDT --mult 3.0
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter

import numpy as np

from backtest_engine import run_backtest
from position import ExitRules
from sl2_tf_sweep import load_tf

_SIMPLE = ExitRules(enabled=False, trail_with_st=False)


def rules(trail=True, bne=True, tp1=1.5, ratio=70.0, sl_mode="pct", sl_pct=3.0):
    return ExitRules(enabled=True, tp1_pct=tp1, tp1_ratio=ratio,
                     move_sl_to_entry=bne, sl_mode=sl_mode, sl_pct=sl_pct,
                     trail_with_st=trail, reverse_close=False)


def reasons(trades):
    return Counter(t["reason"] for t in trades)


def main(argv=None):
    ap = argparse.ArgumentParser(description="reverse_count=0 根因定位")
    ap.add_argument("--symbol", default="BTC-USDT")
    ap.add_argument("--tf", default="1h")
    ap.add_argument("--period", type=int, default=10)
    ap.add_argument("--mult", type=float, default=3.0)
    ap.add_argument("--fee", type=float, default=0.0005)
    a = ap.parse_args(argv)

    cs = load_tf(a.symbol, a.tf)
    if len(cs) < 500:
        print(f"数据不足：{len(cs)} 根")
        return 1
    p = {"periods": a.period, "multiplier": a.mult, "src": "hl2", "change_atr": True}
    kw = dict(init_cash=10000.0, fee_rate=a.fee, sizing="equity")

    print("=" * 120)
    print(f"③ 根因定位 · {a.symbol} {a.tf} · SuperTrend({a.period},{a.mult}) · {len(cs)} 根")
    print("=" * 120)

    # ── 参照：简化出场的均持仓 = 「反向翻向」本来的间隔 ──
    r0 = run_backtest(cs, p, **kw, exit_rules=_SIMPLE, full_trades=True)
    base_bars = r0["avg_bars"]
    print(f"\n  参照（简化出场，全部由反向翻向平仓）：")
    print(f"    笔数 {r0['trades']} · 均持仓 {base_bars} 根 · 总收益 {r0['return_pct']:+.2f}%"
          f" · 反向平仓 {r0['reverse_count']} 次")
    print(f"    → 这就是「反向翻向」本来的时间尺度：平均 {base_bars:.1f} 根 K 线一次")

    # ── 2×2 矩阵：trail_with_st × move_sl_to_entry ──
    print(f"\n  2×2 矩阵：trail_with_st × move_sl_to_entry（其余固定 tp1=1.5%/70%, sl=pct 3%）")
    print(f"  {'配置':<40}{'笔数':>7}{'胜率%':>8}{'收益%':>10}{'回撤%':>9}"
          f"{'均持仓':>9}{'TP1':>6}{'止损':>6}{'反向平':>8}")
    rows = []
    for trail in (True, False):
        for bne in (True, False):
            r = run_backtest(cs, p, **kw, exit_rules=rules(trail=trail, bne=bne),
                             full_trades=True)
            tag = (f"trail_with_st={str(trail):<5} move_sl_to_entry={str(bne):<5}")
            print(f"  {tag:<40}{r['trades']:>7}{r['win_rate']:>8.1f}"
                  f"{r['return_pct']:>10.2f}{r['max_dd_pct']:>9.1f}"
                  f"{r['avg_bars']:>9.1f}{r['tp1_count']:>6}{r['stop_count']:>6}"
                  f"{r['reverse_count']:>8}")
            rows.append((trail, bne, r))

    # ── 逐条规则单独关闭 ──
    print(f"\n  逐条单关（其余按生产：两条都开）")
    print(f"  {'配置':<40}{'笔数':>7}{'胜率%':>8}{'收益%':>10}{'回撤%':>9}"
          f"{'均持仓':>9}{'TP1':>6}{'止损':>6}{'反向平':>8}")
    combos = [
        ("生产原样（两条都开）", rules(True, True)),
        ("只关 trail_with_st", rules(False, True)),
        ("只关 move_sl_to_entry", rules(True, False)),
        ("两条都关（= 只有 TP1 + 固定止损）", rules(False, False)),
        ("只留 TP1，止损放宽到 6%", rules(False, False, sl_pct=6.0)),
        ("只留 TP1，止损用超趋线", rules(False, False, sl_mode="st")),
    ]
    for tag, rr in combos:
        r = run_backtest(cs, p, **kw, exit_rules=rr, full_trades=True)
        print(f"  {tag:<40}{r['trades']:>7}{r['win_rate']:>8.1f}"
              f"{r['return_pct']:>10.2f}{r['max_dd_pct']:>9.1f}"
              f"{r['avg_bars']:>9.1f}{r['tp1_count']:>6}{r['stop_count']:>6}"
              f"{r['reverse_count']:>8}")

    # ── 离场原因分布（生产原样） ──
    rP = rows[0][2]
    print(f"\n  生产原样下的离场原因分布（共 {rP['trades']} 笔）")
    c = reasons(rP["trades_list"])
    tot = sum(c.values())
    for k, v in c.most_common():
        print(f"    {k:<14}{v:>6} 笔  ({v/tot*100:>5.1f}%)")
    print(f"    → 「反向信号」{c.get('反向信号', 0)} 笔。设计的最后一步从未发生。")

    # ── 时间尺度对比 ──
    print(f"\n  时间尺度对比（这是「从未触发」的机制解释）")
    print(f"    反向翻向本来的间隔          ：{base_bars:>6.1f} 根")
    print(f"    生产出场下实际持仓          ：{rP['avg_bars']:>6.1f} 根")
    print(f"    → 止损平均比反向信号早 {base_bars - rP['avg_bars']:.1f} 根 K 线"
          f"（≈ {(base_bars - rP['avg_bars']):.1f} 小时 @1h）触发")
    print(f"    保本止损的代价：把「剩余 30% 吃趋势」这条腿彻底废掉了，"
          f"同时把 {rP['tp1_count']} 笔的 70% 锁死在 +1.5%")

    print("\n" + "=" * 120)
    print("判读：")
    print("  · 若「只关 move_sl_to_entry」后 reverse 仍为 0 → 罪魁是 trail_with_st（超趋线止损太紧）")
    print("  · 若「只关 trail_with_st」后 reverse 出现   → 罪魁是止损上移+跟随的组合")
    print("  · 若两条都关才出现 reverse                → 保本机制本身就和「跟随超趋线」互斥")
    print("  · 无论哪种，都要回答：修好这条腿（让剩余仓位真的跟趋势）值不值")
    print("=" * 120)
    return 0


if __name__ == "__main__":
    sys.exit(main())
