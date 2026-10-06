# -*- coding: utf-8 -*-
"""方向①：ST 信号源参数扫描（period × multiplier）。

为隔离「信号源」这一层，v3_filter=False（且 V3 内部硬编码 st_periods=10/3.0，
不关 V3 会在错误 ST 上下文上判断）。固定统一出场（基线 1.5/70 st trail=T），
这样跨组对比的是「ST 翻转本身的质量」而非出场。

输出信号质量指纹：翻转数(=信号频率) / 胜率 / PF / 分年净U / 回撤，
跨 2022-26 全段防过拟合。胜率是信号质量的直接度量（不靠出场尾部 cover）。
口径：1h·费0.05%·fixed100U×10x·周末不开新仓。
"""
from __future__ import annotations
import datetime as dt
import sqlite3
from concurrent.futures import ProcessPoolExecutor, as_completed

import bt_1h_exit_opt as M
from backtest_engine import run_backtest
from position import ExitRules

UTC = dt.timezone.utc


def ts_of(y, m=1, d=1) -> int:
    return int(dt.datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


PERIODS = [7, 10, 14, 20, 30]
MULTS = [2.0, 3.0, 4.0]
EXIT = ExitRules(enabled=True, reverse_close=False, move_sl_to_entry=True,
                 tp1_pct=1.5, tp1_ratio=70.0, sl_mode="st", sl_pct=2.0,
                 trail_with_st=True)

WINS = [("2022", ts_of(2022), ts_of(2023), 1000.0),
        ("2023", ts_of(2023), ts_of(2024), 1000.0),
        ("2024", ts_of(2024), ts_of(2025), 1000.0),
        ("2025", ts_of(2025), ts_of(2026), 1000.0),
        ("2026*", ts_of(2026), None, 1000.0),
        ("22-26全", ts_of(2022), None, 5000.0)]


def run(lo, hi, init, stp):
    cs = [c for c in M._G["base"] if lo <= c["ts"] < hi]
    p = dict(stp)
    return run_backtest(cs, p, init_cash=init, fee_rate=M.FEE, allow_short=True,
                        sizing="fixed", margin_usdt=100.0, leverage=10,
                        exit_rules=EXIT, v3_filter=False, gate_tf=M.TF,
                        candles_by_tf={M.TF: cs, "4h": M._G["cbtf4"]},
                        block_if=M.is_weekend_et)


def worker(task):
    period, mult, lo, hi, init = task
    stp = {"periods": period, "multiplier": mult, "src": "hl2",
           "change_atr": True}
    try:
        r = run(lo, hi, init, stp)
    except Exception as e:  # pragma: no cover
        return (period, mult, lo, hi, None, repr(e)[:80])
    return (period, mult, lo, hi, r, None)


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
    tasks = [(p, m, lo, hi, init)
             for p in PERIODS for m in MULTS for (_, lo, hi, init) in W]
    res = {}
    with ProcessPoolExecutor(max_workers=12, initializer=M._init_worker) as ex:
        futs = [ex.submit(worker, t) for t in tasks]
        for n, f in enumerate(as_completed(futs), 1):
            period, mult, lo, hi, r, err = f.result()
            if r is not None and "error" not in r:
                res[(period, mult, lo, hi)] = r
            if n % 30 == 0:
                print(f"  …{n}/{len(tasks)}", flush=True)

    print("=" * 120)
    print("### ST 信号源扫描（v3_filter=False，固定基线出场）· 翻转数=信号频率，胜率=信号质量")
    print("=" * 120)
    hdr = f"  {'period/mult':<14}{'翻转':>6}{'胜率':>7}{'PF':>7}{'回撤':>8}"
    hdr += "".join(f"{l:>10}" for l, *_ in W)
    print(hdr)
    print("  " + "─" * 113)
    for p in PERIODS:
        for m in MULTS:
            rs = [res.get((p, m, lo, hi)) for (_, lo, hi, _) in W]
            if any(r is None for r in rs):
                print(f"  {p}/{m:<10} 数据缺失")
                continue
            full = rs[-1]
            flips = full["trades"]
            wr = full["win_rate"]
            pf = full["profit_factor"] or 0.0
            dd = full["max_dd_pct"]
            line = f"  {p}/{m:<10}{flips:>6}{wr:>6.1f}%{pf:>7.2f}{dd:>7.1f}%"
            for r in rs:
                line += f"{r['final']-r['init_cash']:>+10.0f}" if r else f"{'—':>10}"
            print(line)

    print("\n  【候选筛选：全段胜率>=45% 且 分年正年数>=4/5】")
    yr = W[:-1]
    for p in PERIODS:
        for m in MULTS:
            rs = [res.get((p, m, lo, hi)) for (_, lo, hi, _) in yr]
            if any(r is None for r in rs):
                continue
            full = res[(p, m, W[-1][1], W[-1][2])]
            npos = sum(1 for r in rs if r["final"] - r["init_cash"] > 0)
            if full["win_rate"] >= 45 and npos >= 4:
                print(f"  ✓ {p}/{m}: 全段胜率{full['win_rate']:.1f}% "
                      f"PF{full['profit_factor']} 分年正{npos}/5 "
                      f"全段净U{full['final']-full['init_cash']:+.0f} "
                      f"回撤{full['max_dd_pct']:.1f}%")


if __name__ == "__main__":
    main()
