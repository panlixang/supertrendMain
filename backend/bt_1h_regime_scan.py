# -*- coding: utf-8 -*-
"""方向①终章：regime 过滤扫描（信号质量最后未试的杠杆）。

两种独立机制叠加扫描，固定在 ST=10/3.0 + 当前V3(v3_min=0) + 基线出场，
隔离「regime 是否提胜率」这一层：
  trend_align : 只顺 4h SuperTrend 大周期方向开仓（逆势 1h 信号直接拦）
  ER 阈值     : block_if，4h ER20 < er_min 禁止开仓（震荡市不出）

背景：V3 强度扫描已证伪——收紧 V3 不放行低胜率信号（三档路径胜率相近），
胜率恒 57%。但 ST 翻转固有弱点 = 逆大周期/震荡市的假信号。regime 过滤正是
砍这类信号，理论上能提胜率（而非只砍数量）。

口径：1h·费0.05%·fixed100U×10x·周末不开新仓。
"""
from __future__ import annotations
import bisect
import datetime as dt
import sqlite3
from concurrent.futures import ProcessPoolExecutor, as_completed

from indicators import super_trend

import bt_1h_exit_opt as M
from backtest_engine import run_backtest
from position import ExitRules

UTC = dt.timezone.utc


def ts_of(y, m=1, d=1) -> int:
    return int(dt.datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


EXIT = ExitRules(enabled=True, reverse_close=False, move_sl_to_entry=True,
                 tp1_pct=1.5, tp1_ratio=70.0, sl_mode="st", sl_pct=2.0,
                 trail_with_st=True)

# (名称, er_min, 用trend_align)
REGIMES = [
    ("无regime", 0.0, False),
    ("trend_align", 0.0, True),
    ("ER0.05", 0.05, False),
    ("ER0.10", 0.10, False),
    ("ER0.15", 0.15, False),
    ("align+ER0.10", 0.10, True),
]

WINS = [("2022", ts_of(2022), ts_of(2023), 1000.0),
        ("2023", ts_of(2023), ts_of(2024), 1000.0),
        ("2024", ts_of(2024), ts_of(2025), 1000.0),
        ("2025", ts_of(2025), ts_of(2026), 1000.0),
        ("2026*", ts_of(2026), None, 1000.0),
        ("22-26全", ts_of(2022), None, 5000.0)]

_CACHE: dict = {}


def er_series(c, n):
    out = [0.0] * len(c)
    cum = [0.0] * (len(c) + 1)
    for i in range(1, len(c)):
        cum[i] = cum[i - 1] + abs(c[i] - c[i - 1])
    for i in range(n, len(c)):
        den = cum[i] - cum[i - n]
        out[i] = abs(c[i] - c[i - n]) / den if den > 0 else 0.0
    return out


def build_regime():
    """用 4h K 线构造两个 per-1h-bar 映射：大周期方向、大周期 ER。"""
    if _CACHE:
        return _CACHE
    c4 = M._G["cbtf4"]
    o4 = [x["o"] for x in c4]; h4 = [x["h"] for x in c4]
    l4 = [x["l"] for x in c4]; cc4 = [x["c"] for x in c4]
    ts4 = [x["ts"] for x in c4]
    st = super_trend(o4, h4, l4, cc4, periods=10, multiplier=3.0)
    trend = st["trend"]
    tal4 = {}
    for k in range(len(ts4)):
        t = trend[k]
        if t == 1:
            tal4[ts4[k]] = 1
        elif t == -1:
            tal4[ts4[k]] = -1
    er4 = er_series(cc4, 20)
    tss = [x["ts"] for x in M._G["base"]]
    tal1h, er1h = {}, {}
    for t in tss:
        j = bisect.bisect_right(ts4, t) - 1
        if j >= 0:
            d = tal4.get(ts4[j])
            if d is not None:
                tal1h[t] = d
            er1h[t] = er4[j]
    _CACHE["tal"] = tal1h
    _CACHE["er"] = er1h
    return _CACHE


def run(lo, hi, init, er_min, use_align):
    rc = build_regime()
    cs = [c for c in M._G["base"] if lo <= c["ts"] < hi]
    kw = dict(init_cash=init, fee_rate=M.FEE, allow_short=True, sizing="fixed",
              margin_usdt=100.0, leverage=10, exit_rules=EXIT,
              gate_tf=M.TF, candles_by_tf={M.TF: cs, "4h": M._G["cbtf4"]},
              v3_filter=True, v3_min_score=0.0)
    if use_align:
        kw["trend_align"] = rc["tal"]
    em = rc["er"] if er_min > 0 else None
    base = M.is_weekend_et
    if em is not None:
        kw["block_if"] = lambda ts: base(ts) or em.get(ts, 1.0) < er_min
    else:
        kw["block_if"] = base
    return run_backtest(cs, M.ST_P, **kw)


def worker(task):
    name, lo, hi, init, er_min, use_align = task
    try:
        r = run(lo, hi, init, er_min, use_align)
    except Exception as e:  # pragma: no cover
        return (name, lo, hi, None, repr(e)[:80])
    return (name, lo, hi, r, None)


def main():
    con = sqlite3.connect("candle_data.db")
    end = con.execute(
        "select max(ts) from candles where symbol='BTC-USDT' and tf='15m'"
    ).fetchone()[0]
    con.close()
    W = list(WINS)
    W[4] = ("2026*", ts_of(2026), end, 1000.0)
    W[5] = ("22-26全", ts_of(2022), end, 5000.0)

    M._init_worker()
    tasks = [(n, lo, hi, init, em, ua) for (n, em, ua) in REGIMES
             for (_, lo, hi, init) in W]
    res = {}
    with ProcessPoolExecutor(max_workers=12, initializer=M._init_worker) as ex:
        for n2, f in enumerate(as_completed([ex.submit(worker, t) for t in tasks]), 1):
            nm, lo, hi, r, err = f.result()
            if r is not None and "error" not in r:
                res[(nm, lo, hi)] = r
            if n2 % 18 == 0:
                print(f"  …{n2}/{len(tasks)}", flush=True)

    base_full = res[("无regime", W[-1][1], W[-1][2])]["trades"]

    print("=" * 118)
    print("### regime 过滤扫描（trend_align 顺4h大势 + ER 阈值砍震荡）")
    print("=" * 118)
    hdr = f"  {'regime':<14}{'放行':>6}{'放行率':>8}{'胜率':>7}{'PF':>7}{'回撤':>8}"
    hdr += "".join(f"{l:>10}" for l, *_ in W)
    print(hdr)
    print("  " + "─" * 111)
    for nm, em, ua in REGIMES:
        rs = [res.get((nm, lo, hi)) for (_, lo, hi, _) in W]
        if any(r is None for r in rs):
            print(f"  {nm:<14} 数据缺失")
            continue
        full = rs[-1]
        tr = full["trades"]
        rate = 100 * tr / base_full if base_full else 0.0
        line = (f"  {nm:<14}{tr:>6}{rate:>7.1f}%{full['win_rate']:>6.1f}%"
                f"{(full['profit_factor'] or 0):>7.2f}{full['max_dd_pct']:>7.1f}%")
        for r in rs:
            line += f"{r['final'] - r['init_cash']:>+10.0f}"
        print(line)

    print("\n  对照「无regime」= 当前V3全放行(519笔)。trend_align=只顺4h ST方向开仓；"
          "ER=4h ER20<阈值禁开。")
    print("  关键看：胜率是否突破 57% 上限、分年正收益年数是否增多、全段净U是否不降。")


if __name__ == "__main__":
    main()
