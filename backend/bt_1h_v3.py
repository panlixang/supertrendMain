# -*- coding: utf-8 -*-
"""1h + V3 的 BTC 回测：重点 2025 vs 2026。

为什么是 1h：signal_v3 是在「1h 信号 + 4h 上下文」上拟合的
（pattern_trade.py:699-728 里 cs=信号周期、cs4=4h，allow_tfs 含 "1h"）。
上一轮把同一套 V3 外推到 30m，结论是「稳定少亏但三年净亏 -698U ~ -791U」，
V3 本身救不了 30m 的交易频率。这一步回到 V3 的原生周期，看是周期的问题还是
信号本身没有 alpha。

口径与 bt_30m_v3_yearly.py 完全一致（引擎、出场、费率、休市规则），
可以直接横向对比 30m 那一轮的每一行。

⚠️ 沿用上一轮踩到的坑：合计窗口必须用 10000U 本金。fixed sizing 下 equity
承担全部盈亏，连亏到 equity < 保证金(100U) 后 open_pos 会静默 skip 掉之后所有
信号（30m 合计那次 skip 掉 175 次，785 个信号只剩 610 笔，结果完全失真）。
这里保留 skip 列，跑出来必须是 0 才算数。
"""
from __future__ import annotations

import datetime as dt
import sqlite3

from backtest_engine import run_backtest
from bt_2026_weekend import (ST_P, FEE, PROD_EXIT, SIMPLE_EXIT,
                             is_weekend_et, resample_ms)
from indicators import super_trend
from sl2_tf_sweep import load_db, load_tf

UTC = dt.timezone.utc
TF = "1h"


def ts_of(y, m=1, d=1) -> int:
    return int(dt.datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


def main():
    con = sqlite3.connect("candle_data.db")
    data_end = con.execute(
        "select max(ts) from candles where symbol='BTC-USDT' and tf='15m'"
    ).fetchone()[0]
    con.close()
    print(f"数据末端 {dt.datetime.fromtimestamp(data_end/1000, tz=UTC):%Y-%m-%d %H:%M} UTC"
          f"（2026 是不完整年，只有到 9 月）")
    print(f"口径：{TF} · 生产 ST(10,3.0) hl2 changeATR · 费率 {FEE*100:.2f}%/边 · "
          f"fixed 100U×10x")
    print(f"      V3 = 原生周期（{TF} 信号 + 4h 上下文）· 美东周六周日不开新仓")
    print(f"      分年本金 1000U；合计窗口本金 10000U（保证金占 1%，防止资金枯竭截断）\n")

    # 从 15m 重采样，和 30m 那一轮同源，保证横向可比
    base = resample_ms(load_db("BTC-USDT", "15m"), 60 * 60_000, 4)
    cbtf_4h = load_tf("BTC-USDT", "4h")
    print(f"1h K 线 {len(base)} 根，起 {dt.datetime.fromtimestamp(base[0]['ts']/1000, tz=UTC):%Y-%m-%d}\n")

    windows = [
        ("2024", ts_of(2024), ts_of(2025), 1000.0),
        ("2025", ts_of(2025), ts_of(2026), 1000.0),
        ("2026*", ts_of(2026), data_end, 1000.0),
        ("合计#", ts_of(2024), data_end, 10000.0),
    ]

    win_cs, flip_stat = {}, {}
    for label, lo, hi, _init in windows:
        cs = [c for c in base if lo <= c["ts"] <= hi]
        win_cs[label] = cs
        if len(cs) < 500:
            flip_stat[label] = (0, 0, 0)
            continue
        st = super_trend([c["o"] for c in cs], [c["h"] for c in cs],
                         [c["l"] for c in cs], [c["c"] for c in cs],
                         periods=10, multiplier=3.0, change_atr=True)
        flips = st["flips"]
        n_rest = sum(1 for f in flips if is_weekend_et(cs[f["i"]]["ts"]))
        flip_stat[label] = (len(flips), n_rest, len(flips) - n_rest)

    def common(init: float):
        return dict(init_cash=init, fee_rate=FEE, allow_short=True,
                    sizing="fixed", margin_usdt=100.0, leverage=10)

    pf = lambda r: (r["profit_factor"] if r["profit_factor"] is not None else 0.0)

    for exit_tag, rules in (("生产出场 4.0/50", PROD_EXIT),
                            ("简化出场(翻向反手)", SIMPLE_EXIT)):
        print("=" * 116)
        print(f"### {TF} · {exit_tag}")
        print("=" * 116)
        print(f"  {'窗口':<7}{'买入持有%':>10}{'笔数':>6}│{'纯ST收益%':>10}{'回撤%':>8}"
              f"{'PF':>7}{'笔数':>6}│{'V3收益%':>10}{'回撤%':>8}{'PF':>7}"
              f"│{'Δ收益pp':>9}{'Δ回撤pp':>9}{'V3放行%':>9}{'skip':>6}")
        print("  " + "─" * 114)
        for label, lo, hi, init in windows:
            cs = win_cs[label]
            if len(cs) < 500:
                print(f"  {label:<7}数据不足（{len(cs)} 根）")
                continue
            cbtf = {TF: cs, "4h": cbtf_4h}
            bh = (cs[-1]["c"] - cs[0]["c"]) / cs[0]["c"] * 100
            rn = run_backtest(cs, ST_P, **common(init), exit_rules=rules,
                              block_if=is_weekend_et)
            rv = run_backtest(cs, ST_P, **common(init), exit_rules=rules,
                              v3_filter=True, gate_tf=TF, candles_by_tf=cbtf,
                              block_if=is_weekend_et)
            wk = flip_stat[label][2]
            rate = rv["trades"] / wk * 100 if wk else 0
            skp = rn["skipped_insufficient"] + rv["skipped_insufficient"]
            print(f"  {label:<7}{bh:>10.2f}{rn['trades']:>6}│"
                  f"{rn['return_pct']:>10.2f}{rn['max_dd_pct']:>8.1f}{pf(rn):>7.2f}"
                  f"{rv['trades']:>6}│{rv['return_pct']:>10.2f}"
                  f"{rv['max_dd_pct']:>8.1f}{pf(rv):>7.2f}"
                  f"│{rv['return_pct'] - rn['return_pct']:>9.2f}"
                  f"{rv['max_dd_pct'] - rn['max_dd_pct']:>9.2f}"
                  f"{rate:>9.1f}{skp:>6}")
        print()

    # ── 净 U（与本金无关的不变量，这样才能跨行比较）──
    print("=" * 116)
    print("### 净 U 汇总（100U 保证金口径，分年可加总；与百分比不同，不受本金缩放影响）")
    print("=" * 116)
    print(f"  {'窗口':<7}│{'生产纯ST':>10}{'生产V3':>10}{'Δ':>9}│"
          f"{'简化纯ST':>10}{'简化V3':>10}{'Δ':>9}")
    print("  " + "─" * 72)
    for label, lo, hi, init in windows:
        cs = win_cs[label]
        if len(cs) < 500:
            continue
        cbtf = {TF: cs, "4h": cbtf_4h}
        row = {}
        for tag, rules in (("prod", PROD_EXIT), ("simp", SIMPLE_EXIT)):
            for k, kw in (("n", {}),
                          ("v", dict(v3_filter=True, gate_tf=TF,
                                     candles_by_tf=cbtf))):
                r = run_backtest(cs, ST_P, **common(init), exit_rules=rules,
                                 block_if=is_weekend_et, **kw)
                row[tag + k] = r["final"] - init
        print(f"  {label:<7}│{row['prodn']:>10.0f}{row['prodv']:>10.0f}"
              f"{row['prodv'] - row['prodn']:>9.0f}│"
              f"{row['simpn']:>10.0f}{row['simpv']:>10.0f}"
              f"{row['simpv'] - row['simpn']:>9.0f}")
    print()

    # ── 信号级：V3 在原生的 1h 上到底拦掉多少 ──
    print("=" * 116)
    print("### 信号级：V3 行为一致性")
    print("=" * 116)
    print(f"  {'窗口':<7}{'翻转总数':>10}{'落在休市日':>11}{'工作日翻转':>11}"
          f"{'V3放行':>9}{'放行率%':>10}{'被V3拦':>9}")
    rates = []
    for label, lo, hi, init in windows:
        n_tot, n_rest, wk = flip_stat[label]
        if n_tot == 0:
            continue
        cs = win_cs[label]
        rv = run_backtest(cs, ST_P, **common(init), exit_rules=SIMPLE_EXIT,
                          v3_filter=True, gate_tf=TF,
                          candles_by_tf={TF: cs, "4h": cbtf_4h},
                          block_if=is_weekend_et)
        passed = rv["trades"]
        rate = passed / wk * 100 if wk else 0
        if "合计" not in label:
            rates.append(rate)
        print(f"  {label:<7}{n_tot:>10}{n_rest:>11}{wk:>11}"
              f"{passed:>9}{rate:>10.1f}{wk - passed:>9}")
    if rates:
        print(f"\n  放行率极差 {max(rates) - min(rates):.1f}pp"
              f"｜区间 {min(rates):.1f}% ~ {max(rates):.1f}%")

    print("\n注：每年前 120 根 1h，V3 因数据不足走「放行」分支"
          "（pattern_trade.py:708），放行率会被抬高约 1~2pp；两年同样受影响，对比仍公平。")
    print("=" * 116)


if __name__ == "__main__":
    main()
