# -*- coding: utf-8 -*-
"""方向①收尾·严谨化：ER 阈值细化 + IS/OOS 防过拟合验证。

IS  = 2022-2024（熊/复苏/牛，3年，调参窗口）
OOS = 2025-2026*（震荡下行，~1.75年，验证窗口）
在 IS 选「最优 ER 阈值」，看它在 OOS 是否仍排名靠前 → 证伪/证实过拟合。
两族：纯ER（只ER砍震荡）/ align+ER（ER + 顺4h大势）。
固定 ST=10/3.0 + V3全放行 + 基线出场 + 周末过滤。

ER=0.0 为基线（无ER；按族决定是否带 align）。
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

ER_GRID = [0.0, 0.08, 0.09, 0.10, 0.11, 0.12, 0.13, 0.14, 0.15, 0.16, 0.18]
FAMILIES = [("纯ER", False), ("align+ER", True)]

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
    er, align, lo, hi, init = task
    try:
        r = run(lo, hi, init, er, align)
    except Exception as e:  # pragma: no cover
        print(f"[ERR] er={er} align={align} win={lo}-{hi}: {repr(e)[:160]}",
              flush=True)
        return (er, align, lo, hi, None, repr(e)[:80])
    if "error" in r:
        print(f"[ERR] er={er} align={align} win={lo}-{hi}: {r['error'][:160]}",
              flush=True)
    return (er, align, lo, hi, r, None)


def main():
    con = sqlite3.connect("candle_data.db")
    end = con.execute(
        "select max(ts) from candles where symbol='BTC-USDT' and tf='15m'"
    ).fetchone()[0]
    con.close()

    IS = (ts_of(2022), ts_of(2025), 3000.0)
    OOS = (ts_of(2025), end, 2000.0)
    FULL = (ts_of(2022), end, 5000.0)
    WINS = [("IS", *IS), ("OOS", *OOS), ("全段", *FULL)]

    M._init_worker()
    tasks = [(er, al, lo, hi, init) for er in ER_GRID
             for (al, _) in FAMILIES for (_, lo, hi, init) in WINS]
    res = {}
    with ProcessPoolExecutor(max_workers=12, initializer=M._init_worker) as ex:
        for n2, f in enumerate(as_completed([ex.submit(worker, t) for t in tasks]), 1):
            er, al, lo, hi, r, err = f.result()
            if r is not None and "error" not in r:
                res[(er, al, lo, hi)] = r
            if n2 % 22 == 0:
                print(f"  …{n2}/{len(tasks)}", flush=True)

    print("=" * 100)
    print("### ER 阈值细化 + IS/OOS 验证  (IS=2022-2024, OOS=2025-2026*)")
    print("=" * 100)
    for fname, align in FAMILIES:
        print(f"\n── 家族 {fname} ──────────────────────────────────────────────────────────────────")
        hdr = (f"  {'ER':>6}{'IS_ret%':>9}{'OOS_ret%':>10}{'全_ret%':>9}"
               f"{'IS胜':>7}{'OOS胜':>7}{'全胜':>7}{'全回撤':>8}{'全PF':>7}{'全净U':>8}")
        print(hdr)
        rows = []
        for er in ER_GRID:
            ris = res.get((er, align, IS[0], IS[1]))
            roos = res.get((er, align, OOS[0], OOS[1]))
            rf = res.get((er, align, FULL[0], FULL[1]))
            if None in (ris, roos, rf):
                print(f"  ER{er}: 数据缺失（见上方[ERR]）")
                continue
            rows.append((er, ris, roos, rf))
        best_is = max(rows, key=lambda x: x[1]["return_pct"])
        ranked_oos = sorted(rows, key=lambda x: x[2]["return_pct"], reverse=True)
        rank_oos = {x[0]: i + 1 for i, x in enumerate(ranked_oos)}
        for er, ris, roos, rf in rows:
            star = " ★IS最优" if er == best_is[0] else ""
            line = (f"  {er:>6}{ris['return_pct']:>+8.1f}%{roos['return_pct']:>+9.1f}%"
                    f"{rf['return_pct']:>+8.1f}%{ris['win_rate']:>6.1f}%{roos['win_rate']:>6.1f}%"
                    f"{rf['win_rate']:>6.1f}%{rf['max_dd_pct']:>7.1f}%"
                    f"{(rf['profit_factor'] or 0):>7.2f}{rf['final']-rf['init_cash']:>+8.0f}{star}")
            print(line)
        bi = best_is[0]
        print(f"  IS最优=ER{bi}（IS_ret {best_is[1]['return_pct']:+.1f}%）；"
              f"其在OOS家族内排名第{rank_oos[bi]}/{len(rows)}")
        if rank_oos[bi] <= 4:
            print("  → IS最优在OOS仍居前，regime 过滤稳健、非过拟合 ✓")
        else:
            print("  → IS最优在OOS掉队，疑似过拟合；应选 OOS 也高且分年均匀的档")


if __name__ == "__main__":
    main()
