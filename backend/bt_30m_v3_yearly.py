# -*- coding: utf-8 -*-
"""30m V3 跨年稳定性验证：2024 / 2025 / 2026 分年对比。

上一轮 30m 2026 的结果里 V3 看着很美（简化出场 +7.56% → +25.16%），
但那是单一标的、单一窗口、137 笔 —— 完全可能是运气。
这份脚本把它拆到三个自然年，看两件事：

  1) Δ收益（V3 − 纯ST）是否**年年同号**、量级是否可重复
  2) V3 的放行率（拦掉百分之几）是否稳定
     —— 如果某年放行率忽高忽低，说明它在跟着行情 regime 漂，那就不叫"站稳"

口径与 bt_2026_weekend.py 完全一致（同一套引擎、出场、费率、休市规则），
保证 2026 那一格能和上一轮对得上。
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
TF = "30m"


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
          f"fixed 100U×10x / 本金 1000U")
    print(f"      美东周六周日不开新仓（反向平仓照常）· V3 喂 4h 上下文\n")

    base_30m = resample_ms(load_db("BTC-USDT", "15m"), 30 * 60_000, 2)
    cbtf_4h = load_tf("BTC-USDT", "4h")

    # ⚠️ 合计窗口必须用更大的本金。fixed sizing 下 equity 承担全部盈亏，
    # 一旦连亏到 equity < 保证金(100U)，open_pos 会静默 n_skip 掉之后所有信号。
    # 实测：2024-2026 合计用 1000U 本金会 skip 掉 175 次（785 个工作日翻转只剩
    # 610 笔），合计那一格就等于"策略亏光后停摆"，完全失真。本金放大到 10000U
    # （保证金占 1%）才不会击穿。
    windows = [
        ("2024", ts_of(2024), ts_of(2025), 1000.0),
        ("2025", ts_of(2025), ts_of(2026), 1000.0),
        ("2026*", ts_of(2026), data_end, 1000.0),
        ("合计#", ts_of(2024), data_end, 10000.0),
    ]

    # 每个窗口的 flip 统计先算好。注意：engine 返回的 r["trades"] 是【平仓记录数】，
    # 生产出场下 TP1 平一半会再产生一条记录，所以 trades ≠ 交易笔数 ≠ flip 数。
    # 放行率必须用 flip 数做分母才准。
    win_cs, flip_stat = {}, {}
    for label, lo, hi, _init in windows:
        cs = [c for c in base_30m if lo <= c["ts"] <= hi]
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

    for exit_tag, rules in (("生产出场 4.0/50", PROD_EXIT),
                            ("简化出场(翻向反手)", SIMPLE_EXIT)):
        print("=" * 104)
        print(f"### {TF} · {exit_tag}")
        print("=" * 104)
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
            pf = lambda r: (r["profit_factor"] if r["profit_factor"] is not None
                            else 0.0)
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

    # ── 信号级：V3 每年拦掉多少，是否稳定 ──
    print("=" * 104)
    print("### 信号级：V3 行为一致性（放行率忽高忽低 = 在跟着行情漂，不算站稳）")
    print("=" * 104)
    print(f"  {'窗口':<7}{'翻转总数':>10}{'落在休市日':>11}{'工作日翻转':>11}"
          f"{'V3放行':>9}{'放行率%':>10}{'被V3拦':>9}")
    rows = []
    for label, lo, hi, init in windows:
        cs = win_cs[label]
        if len(cs) < 500:
            continue
        st = super_trend([c["o"] for c in cs], [c["h"] for c in cs],
                         [c["l"] for c in cs], [c["c"] for c in cs],
                         periods=10, multiplier=3.0, change_atr=True)
        flips = st["flips"]
        n_rest = sum(1 for f in flips if is_weekend_et(cs[f["i"]]["ts"]))
        cbtf = {TF: cs, "4h": cbtf_4h}
        rv = run_backtest(cs, ST_P, **common(init), exit_rules=SIMPLE_EXIT,
                          v3_filter=True, gate_tf=TF, candles_by_tf=cbtf,
                          block_if=is_weekend_et)
        wk = len(flips) - n_rest
        passed = rv["trades"]
        rate = passed / wk * 100 if wk else 0
        rows.append((label, rate))
        print(f"  {label:<7}{len(flips):>10}{n_rest:>11}{wk:>11}"
              f"{passed:>9}{rate:>10.1f}{wk - passed:>9}")

    rates = [r for _, r in rows if _ != "合计"]
    if rates:
        print(f"\n  放行率极差 {max(rates) - min(rates):.1f}pp"
              f"（越小越稳定）｜区间 {min(rates):.1f}% ~ {max(rates):.1f}%")
    print("=" * 104)


if __name__ == "__main__":
    main()
