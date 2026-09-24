# -*- coding: utf-8 -*-
"""给 808 笔加: 信号出现后(到下一ST翻转)触及的最高价/最低价
future_high = max(high) over [i, next_flip]
future_low  = min(low)  over [i, next_flip]
"""
import sqlite3, bisect, csv, datetime as dt, math, statistics as st

DB = r"d:\个人项目代码\supertrendMain\backend\candle_data.db"
CSV = r"d:\个人项目代码\supertrendMain\backend\backtest\st_signals_1h.csv"
SYM, ATR_LEN, MULT, H = "BTC-USDT", 10, 3, 300
UTC = dt.timezone.utc


def load(tf, start_ms):
    con = sqlite3.connect(DB)
    rows = con.execute("SELECT ts,o,h,l,c,vol FROM candles WHERE symbol=? AND tf=? "
                       "AND ts>=? ORDER BY ts", (SYM, tf, start_ms)).fetchall()
    con.close()
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]


def wilder_atr(h, l, c, p):
    n = len(c); tr = [0.0] * n; tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    atr = [float("nan")] * n
    if n >= p:
        atr[p - 1] = sum(tr[:p]) / p
        for i in range(p, n):
            atr[i] = (atr[i - 1] * (p - 1) + tr[i]) / p
    return atr


def supertrend(h, l, c, p, mult):
    n = len(c); atr = wilder_atr(h, l, c, p)
    hl2 = [(h[i] + l[i]) / 2 for i in range(n)]
    up = [hl2[i] - mult * atr[i] if not math.isnan(atr[i]) else float("nan") for i in range(n)]
    dn = [hl2[i] + mult * atr[i] if not math.isnan(atr[i]) else float("nan") for i in range(n)]
    upT = [float("nan")] * n; dnT = [float("nan")] * n; trend = [float("nan")] * n
    for i in range(n):
        if math.isnan(up[i]):
            continue
        if i == 0:
            upT[i], dnT[i], trend[i] = up[i], dn[i], 1
        else:
            upT[i] = max(up[i], upT[i - 1]) if c[i - 1] > upT[i - 1] else up[i]
            dnT[i] = min(dn[i], dnT[i - 1]) if c[i - 1] < dnT[i - 1] else dn[i]
            trend[i] = 1 if c[i - 1] > dnT[i - 1] else (-1 if c[i - 1] < upT[i - 1] else trend[i - 1])
    return trend, atr


bars = load("1h", 1661990400000)
ts = [b[0] for b in bars]; HI = [b[2] for b in bars]; LO = [b[3] for b in bars]; n = len(bars)
trend, _ = supertrend(HI, LO, [b[4] for b in bars], ATR_LEN, MULT)
flips = [i for i in range(1, n) if not math.isnan(trend[i]) and trend[i] != trend[i - 1]]
ts_to_i = {ts[i]: i for i in range(n)}

rows = list(csv.DictReader(open(CSV, encoding="utf-8-sig")))
out_cols = list(rows[0].keys()) + ["future_high", "future_low"]
new = []
for r in rows:
    i = ts_to_i.get(int(dt.datetime.strptime(r["time"], "%Y/%m/%d %H:%M").replace(tzinfo=UTC).timestamp() * 1000))
    if i is None:
        new.append(dict(r)); continue
    p = bisect.bisect_right(flips, i); nf = flips[p] if p < len(flips) else min(i + H, n - 1)
    fh = max(HI[i:nf + 1]); fl = min(LO[i:nf + 1])
    row = dict(r); row.update({"future_high": round(fh, 2), "future_low": round(fl, 2)})
    new.append(row)

with open(CSV, "w", newline="", encoding="utf-8-sig") as fp:
    w = csv.DictWriter(fp, fieldnames=out_cols); w.writeheader(); w.writerows(new)

print(f"已写入 808 笔 + future_high / future_low")
print("样例:")
for x in new[:5]:
    print(f"  {x['time']} sig{x['signal']} entry={x['close']}  最高={x['future_high']}  最低={x['future_low']}")
# 多空分别看最大有利/不利幅度(ATR归一参考)
import statistics as st
mfe_l = []; mae_l = []
for x in new:
    if x["signal"] == "1":
        mfe_l.append((float(x["future_high"]) - float(x["close"])))
        mae_l.append((float(x["close"]) - float(x["future_low"])))
    else:
        mfe_l.append((float(x["close"]) - float(x["future_low"])))
        mae_l.append((float(x["future_high"]) - float(x["close"])))
print(f"\n多空合并: 信号后最大有利变动 均值 {sum(mfe_l)/len(mfe_l):.1f}$  最大不利变动 均值 {sum(mae_l)/len(mae_l):.1f}$")
