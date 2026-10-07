# -*- coding: utf-8 -*-
"""诊断 SNDK-USDT 1h/4h 数据质量：异常值、跳变、间隔、重复。"""
import sqlite3
import datetime as dt

con = sqlite3.connect("candle_data.db")
for tf in ("1h", "4h"):
    rows = con.execute(
        "select ts,o,h,l,c,vol from candles where symbol=? and tf=? order by ts",
        ("SNDK-USDT", tf)).fetchall()
    n = len(rows)
    closes = [r[4] for r in rows]
    bad = [r for r in rows if min(r[1], r[2], r[3], r[4]) <= 0]
    print(f"[{tf}] n={n}  minC={min(closes):.4f} maxC={max(closes):.4f}  zero/neg bars={len(bad)}")
    mx, mi = 0.0, 0
    for k in range(1, n):
        prev = closes[k - 1]
        ch = (closes[k] - prev) / prev if prev else 0.0
        if abs(ch) > abs(mx):
            mx, mi = ch, k
    print(f"   最大相邻收盘涨跌={mx*100:.1f}% @idx {mi}  "
          f"ts={dt.datetime.utcfromtimestamp(rows[mi][0]/1000):%Y-%m-%d %H:%M}")
    # 看最大跳变那根的 OHLC
    if mi:
        a, b = rows[mi - 1], rows[mi]
        print(f"     前根 o={a[1]:.4f} c={a[4]:.4f} | 跳变根 o={b[1]:.4f} h={b[2]:.4f} l={b[3]:.4f} c={b[4]:.4f} vol={b[5]:.0f}")
    step = 3600 * 1000 if tf == "1h" else 14400 * 1000
    gaps = [(k, rows[k][0] - rows[k - 1][0]) for k in range(1, n)
            if (rows[k][0] - rows[k - 1][0]) > step * 1.5]
    print(f"   间隔异常(>1.5x) 数={len(gaps)} 例={gaps[:4]}")
    ts = [r[0] for r in rows]
    print(f"   重复ts数={n - len(set(ts))}")
    # 上市初几根
    for r in rows[:3]:
        print(f"   首根 {dt.datetime.utcfromtimestamp(r[0]/1000):%Y-%m-%d %H:%M} "
              f"o={r[1]:.4f} h={r[2]:.4f} l={r[3]:.4f} c={r[4]:.4f} vol={r[5]:.0f}")
