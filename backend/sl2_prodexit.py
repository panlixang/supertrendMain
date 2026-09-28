# -*- coding: utf-8 -*-
"""
口径对账：我一直在用的「简化出场」 vs 生产实际的「position.py 出场状态机」
================================================================================

为什么必须做这件事
------------------
前面所有 sl2_* 研究（volfilter / tf_sweep / riskeval / filter_recheck / chop_filter）
用的出场都是：

    SuperTrend 翻向开仓、**下一反向翻向全仓平掉**，中途不止盈、不保本、不跟踪。

而 pattern_trade.json 里生产**实际在跑**的是：

    TP1 浮盈 1.5% → 平掉 70% 仓 → 止损抬到开仓价（保本）
    → 剩余 30% 跟随 SuperTrend 线移动止损 → 反向信号全平
    出场模式 exit_mode="single"、reverse_close=False、filter_v3=True
    仓位 sizing_mode="fixed"（固定 $10 保证金 × 杠杆，**无复利**）

这两条策略的收益分布完全不同：1.5% 平 70% 会把**所有超过 1.5% 的行情砍掉 70%**，
于是收益被大量"小赢"主导，而单笔亏损是满仓的 −3%（sl_pct=3.0）。
→ 我测的那条策略，不是你跑的那条。

本脚本直接把两者的差值算出来，不靠嘴说。
`backtest_engine.run_backtest` 就是实盘的同源实现（内部调 `position.check/trail`）。

用法
----
    cd backend
    python sl2_prodexit.py
    python sl2_prodexit.py --symbol BTC-USDT --period 15 --mult 9.1
"""
from __future__ import annotations

import argparse
import sys

import numpy as np

from backtest_engine import run_backtest
from position import ExitRules
from sl2_tf_sweep import load_tf

# 不启用止盈止损的哨兵（与 backtest_engine._OFF 同义），用来还原我的简化出场
_SIMPLE = ExitRules(enabled=False, trail_with_st=False)


def prod_rules(sl_mode="pct", sl_pct=3.0, tp1=1.5, ratio=70.0):
    """生产实际参数（pattern_trade.json 里 6 个品种的公共部分）。"""
    return ExitRules(
        enabled=True,
        tp1_pct=tp1, tp1_ratio=ratio,
        move_sl_to_entry=True,
        sl_mode=sl_mode, sl_pct=sl_pct,
        trail_with_st=True,
        reverse_close=False,
    )


def show(tag, r):
    if not r or r.get("error"):
        print(f"  {tag:<34} {r.get('error') if r else '无结果'}")
        return None
    print(f"  {tag:<34}{r['trades']:>7}{r['win_rate']:>8.1f}"
          f"{r['return_pct']:>11.2f}{r['hold_pct']:>10.1f}{r['alpha_pct']:>10.2f}"
          f"{r['max_dd_pct']:>9.1f}{r['avg_bars']:>9.1f}"
          f"{r['tp1_count']:>7}{r['stop_count']:>7}{r['reverse_count']:>7}")
    return r


def yearly(trades, fee_pct, tag):
    """按**开仓年**拆解（用户第③点要求：一次性的长周期手续费会误导判断）。

    净% 用「名义价值口径」：引擎的 pnl_pct 是毛收益/开仓名义价值，
    减去往返两腿费率 2×fee_pct 即得净%。这样逐年可比，不被复利路径干扰。
    """
    import datetime as dt
    from collections import defaultdict
    g = defaultdict(list)
    for t in trades:
        y = dt.datetime.fromtimestamp(t["entry_ts"] / 1000, dt.UTC).strftime("%Y")
        g[y].append(t)
    if not g:
        return
    print(f"    {tag}")
    print(f"      {'年份':<7}{'笔数':>6}{'毛期望%':>10}{'净期望%':>10}{'胜率%':>8}"
          f"{'净USDT':>10}{'均持仓根':>10}   离场原因")
    for y in sorted(g):
        ts = g[y]
        n = len(ts)
        gross = float(np.mean([t["pnl_pct"] for t in ts]))
        net = float(np.mean([t["pnl_pct"] for t in ts])) - 2 * fee_pct
        win = float(np.mean([1.0 if t["pnl"] > 0 else 0.0 for t in ts])) * 100
        usd = float(np.sum([t["pnl"] for t in ts]))
        bars = float(np.mean([t["bars"] for t in ts]))
        from collections import Counter
        c = Counter(t["reason"] for t in ts)
        top = " ".join(f"{k}×{v}" for k, v in c.most_common(4))
        print(f"      {y:<7}{n:>6}{gross:>10.3f}{net:>10.3f}{win:>8.1f}"
              f"{usd:>10.2f}{bars:>10.1f}   {top}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="简化出场 vs 生产出场 口径对账")
    ap.add_argument("--symbol", default="BTC-USDT")
    ap.add_argument("--tf", default="1h")
    ap.add_argument("--period", type=int, default=10)
    ap.add_argument("--mult", type=float, default=3.0)
    ap.add_argument("--fee", type=float, default=0.0005, help="单边费率（小数）")
    ap.add_argument("--margin", type=float, default=10.0)
    ap.add_argument("--leverage", type=int, default=3)
    ap.add_argument("--tp1-ratio", type=float, default=70.0,
                    help="TP1 触发时平掉的仓位%%（生产=70；越小=留越多跟趋势）")
    a = ap.parse_args(argv)

    cs = load_tf(a.symbol, a.tf)
    if len(cs) < 500:
        print(f"数据不足：{a.symbol} {a.tf} 只有 {len(cs)} 根")
        return 1
    p = {"periods": a.period, "multiplier": a.mult, "src": "hl2", "change_atr": True}

    print("=" * 118)
    print(f"口径对账 · {a.symbol} {a.tf} · SuperTrend({a.period}, {a.mult}) · "
          f"{len(cs)} 根 · 费率 {a.fee*100:.2f}%/边")
    print("=" * 118)
    print(f"  {'方案':<34}{'笔数':>7}{'胜率%':>8}{'收益%':>11}{'买入持有%':>10}"
          f"{'超额pp':>10}{'回撤%':>9}{'均持仓根':>9}{'TP1':>7}{'止损':>7}{'反向平':>7}")

    print("\n  ── A 组：equity 满仓复利（我前面所有 sl2 结论用的口径）──")
    base = dict(init_cash=10000.0, fee_rate=a.fee, sizing="equity")
    rA = show("A1 简化出场（我一直在用）", run_backtest(cs, p, **base, exit_rules=_SIMPLE, full_trades=True))
    rB = show(f"A2 生产出场 1.5%/平{a.tp1_ratio:.0f}%+保本+ST跟踪",
              run_backtest(cs, p, **base, exit_rules=prod_rules(ratio=a.tp1_ratio), full_trades=True))

    print("\n  ── B 组：fixed 固定保证金（生产 sizing_mode）──")
    base_f = dict(init_cash=10000.0, fee_rate=a.fee, sizing="fixed",
                  margin_usdt=a.margin, leverage=a.leverage)
    rC = show(f"C1 简化出场 · {a.margin}U×{a.leverage}x",
              run_backtest(cs, p, **base_f, exit_rules=_SIMPLE))
    rD = show(f"C2 生产出场 · {a.margin}U×{a.leverage}x",
              run_backtest(cs, p, **base_f, exit_rules=prod_rules(ratio=a.tp1_ratio)))

    print("\n  ── C 组：出场参数敏感性（生产出场下）──")
    for tp1, ratio in ((1.5, 100.0), (1.5, 70.0), (1.5, 50.0), (1.5, a.tp1_ratio),
                       (1.5, 30.0), (3.0, 100.0)):
        show(f"D tp1={tp1}%/平{ratio:.0f}%（剩余{100-ratio:.0f}%）",
             run_backtest(cs, p, **base, exit_rules=prod_rules(tp1=tp1, ratio=ratio)))

    print("\n  ── D 组：只做多 vs 多空反手（生产出场下）──")
    show("E1 allow_short=True（反手）",
         run_backtest(cs, p, **base, exit_rules=prod_rules(), allow_short=True))
    show("E2 allow_short=False（只做多）",
         run_backtest(cs, p, **base, exit_rules=prod_rules(), allow_short=False))

    print("\n  ── E 组：止损来源 sl_mode ──")
    for mode, pct in (("pct", 3.0), ("pct", 2.0), ("st", None)):
        show(f"F sl_mode={mode}" + (f" {pct}%" if pct else "（超趋线）"),
             run_backtest(cs, p, **base,
                          exit_rules=prod_rules(sl_mode=mode,
                                                sl_pct=pct if pct else 2.0)))

    print("\n" + "=" * 118)
    print("逐年拆解：出场口径改了之后，收益分布在哪一年变了")
    print("=" * 118)
    if rA and rA.get("trades_list"):
        yearly(rA["trades_list"], a.fee * 100, "A1 简化出场（全仓持有到反向翻向）")
    if rB and rB.get("trades_list"):
        yearly(rB["trades_list"], a.fee * 100,
           f"A2 生产出场（1.5% 平 {a.tp1_ratio:.0f}% + 保本 + ST 跟踪）")

    print("\n" + "=" * 118)
    if rA and rB and not rA.get("error") and not rB.get("error"):
        print(f"结论对照：简化出场 总收益 {rA['return_pct']:+.1f}% / 夏普未知 / 笔数 {rA['trades']}")
        print(f"          生产出场 总收益 {rB['return_pct']:+.1f}% / 回撤 {rB['max_dd_pct']:.1f}% / "
              f"笔数 {rB['trades']} / 其中 TP1 触发 {rB['tp1_count']} 次、止损 {rB['stop_count']} 次、"
              f"反向平 {rB['reverse_count']} 次")
        print(f"          两者总收益差 {rB['return_pct'] - rA['return_pct']:+.1f} pp")
    print("读法：若两者差异巨大（>30pp），说明前面所有基于「简化出场」的结论")
    print("      都不能直接外推到生产系统，必须用 backtest_engine 重做。")
    print("=" * 118)
    return 0


if __name__ == "__main__":
    sys.exit(main())
