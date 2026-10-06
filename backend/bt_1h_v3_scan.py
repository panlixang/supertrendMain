# -*- coding: utf-8 -*-
"""方向①续：V3 过滤强度扫描（放行分数线 v3_min_score）。

signal_v3 给每笔信号打分层 score（路径1成熟趋势=100 / 路径2早期突破=80 /
路径3慢热接住=60，一级Fuse减权-20），但回测原本只用 execute(score>0) 放行，
score 数值从未当门槛。本脚本在 score 上加放行分数线，看「只做高质量信号」
能否提胜率 + 分年稳：
  None = 关V3（纯ST翻转，作基准分母）
  0    = 当前V3（全放行，score>0）
  41   = 排除一级Fuse减权的慢热(score=40)，保留干净慢热(60)
  61   = 只放行 path1+2（成熟趋势+早期突破），排除 path3 慢热
  81   = 只放行 path1 成熟趋势（score=100；空头 short_gate 也=100 故全放行）

固定 ST=10/3.0（与 V3 硬编码 st_periods/mult 对齐）、固定基线出场，
隔离「V3 质量闸门」这一层。口径：1h·费0.05%·fixed100U×10x·周末不开新仓。
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


V3_LEVELS = [None, 0.0, 41.0, 61.0, 81.0]
EXIT = ExitRules(enabled=True, reverse_close=False, move_sl_to_entry=True,
                 tp1_pct=1.5, tp1_ratio=70.0, sl_mode="st", sl_pct=2.0,
                 trail_with_st=True)

WINS = [("2022", ts_of(2022), ts_of(2023), 1000.0),
        ("2023", ts_of(2023), ts_of(2024), 1000.0),
        ("2024", ts_of(2024), ts_of(2025), 1000.0),
        ("2025", ts_of(2025), ts_of(2026), 1000.0),
        ("2026*", ts_of(2026), None, 1000.0),
        ("22-26全", ts_of(2022), None, 5000.0)]


def run(lo, hi, init, v3ms):
    cs = [c for c in M._G["base"] if lo <= c["ts"] < hi]
    kw = dict(init_cash=init, fee_rate=M.FEE, allow_short=True, sizing="fixed",
              margin_usdt=100.0, leverage=10, exit_rules=EXIT,
              gate_tf=M.TF, candles_by_tf={M.TF: cs, "4h": M._G["cbtf4"]},
              block_if=M.is_weekend_et)
    if v3ms is None:
        kw["v3_filter"] = False
    else:
        kw["v3_filter"] = True
        kw["v3_min_score"] = v3ms
    return run_backtest(cs, M.ST_P, **kw)


def worker(task):
    v3ms, lo, hi, init = task
    try:
        r = run(lo, hi, init, v3ms)
    except Exception as e:  # pragma: no cover
        return (v3ms, lo, hi, None, repr(e)[:80])
    return (v3ms, lo, hi, r, None)


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
    tasks = [(v, lo, hi, init) for v in V3_LEVELS
             for (_, lo, hi, init) in W]
    res = {}
    with ProcessPoolExecutor(max_workers=12, initializer=M._init_worker) as ex:
        for n, f in enumerate(as_completed([ex.submit(worker, t) for t in tasks]), 1):
            v3ms, lo, hi, r, err = f.result()
            if r is not None and "error" not in r:
                res[(v3ms, lo, hi)] = r
            if n % 15 == 0:
                print(f"  …{n}/{len(tasks)}", flush=True)

    denom_full = res[(None, W[-1][1], W[-1][2])]["trades"] if (None, W[-1][1], W[-1][2]) in res else None

    print("=" * 118)
    print("### V3 强度扫描（放行分数线）· 关V3=纯ST基准；放行率=相对纯ST翻转全段")
    print("=" * 118)
    hdr = f"  {'v3_min':<10}{'放行':>6}{'放行率':>8}{'胜率':>7}{'PF':>7}{'回撤':>8}"
    hdr += "".join(f"{l:>10}" for l, *_ in W)
    print(hdr)
    print("  " + "─" * 111)
    for v in V3_LEVELS:
        rs = [res.get((v, lo, hi)) for (_, lo, hi, _) in W]
        if any(r is None for r in rs):
            print(f"  {str(v):<10} 数据缺失")
            continue
        full = rs[-1]
        tr = full["trades"]
        rate = 100 * tr / denom_full if denom_full else 0.0
        wr = full["win_rate"]
        pf = full["profit_factor"] or 0.0
        dd = full["max_dd_pct"]
        line = f"  {str(v):<10}{tr:>6}{rate:>7.1f}%{wr:>6.1f}%{pf:>7.2f}{dd:>7.1f}%"
        for r in rs:
            line += f"{r['final'] - r['init_cash']:>+10.0f}"
        print(line)

    print("\n  档位：None=关V3 / 0=当前V3全放行 / 41=排除减权慢热 / "
          "61=只放行path1+2 / 81=只放行path1成熟趋势")
    print("  注：v3_min>=61 时多头只做强趋势，信号数骤降；看分年是否更稳、"
          "全段净U是否不降反升即为「质量>数量」。")


if __name__ == "__main__":
    main()
