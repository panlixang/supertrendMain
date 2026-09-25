# -*- coding: utf-8 -*-
"""计算 2026 年 BTC 1h 回测收益
出场: v4-exit (SL1.5ATR / TP=2ATR半仓止盈 / 剩余ST尾随)
口径A: 2026 全部信号, 满仓(1.0)
口径B: 2026 信号按 V4 分级 S(>=80,1.0)/A(65-80,0.7), 含仓位权重
权益曲线: 逐笔顺序复利 (equity *= 1 + size*pnl%/100), 含扣费
"""
import sqlite3, math, bisect, csv, datetime as dt

DB = r"d:\个人项目代码\supertrendMain\backend\candle_data.db"
CSV = r"d:\个人项目代码\supertrendMain\backend\backtest\st_signals_1h.csv"
SYM, ATR_LEN, MULT, H = "BTC-USDT", 10, 3, 300
UTC = dt.timezone.utc
Y2026 = dt.datetime(2026, 1, 1, tzinfo=UTC)


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
ts = [b[0] for b in bars]; HI = [b[2] for b in bars]; LO = [b[3] for b in bars]; C = [b[4] for b in bars]; nB = len(bars)
trend, atr = supertrend(HI, LO, C, ATR_LEN, MULT)
flips = [i for i in range(1, nB) if not math.isnan(trend[i]) and trend[i] != trend[i - 1]]
ts_to_i = {ts[i]: i for i in range(nB)}


def exit_v4(i, sig, a, entry, nf, sl_mult=1.5, tp_mult=2):
    if sig == 1:
        sl, tp1 = entry - sl_mult * a, entry + tp_mult * a
        for j in range(i + 1, min(i + H + 1, nB)):
            if LO[j] <= sl: return (sl - entry) / entry * 100
            if HI[j] >= tp1: return 0.5 * (tp1 - entry) / entry * 100 + 0.5 * (C[nf] - entry) / entry * 100
            if j == nf: return (C[nf] - entry) / entry * 100
    else:
        sl, tp1 = entry + sl_mult * a, entry - tp_mult * a
        for j in range(i + 1, min(i + H + 1, nB)):
            if HI[j] >= sl: return (sl - entry) / entry * 100
            if LO[j] <= tp1: return 0.5 * (entry - tp1) / entry * 100 + 0.5 * (entry - C[nf]) / entry * 100
            if j == nf: return (entry - C[nf]) / entry * 100
    return (C[min(i + H, nB - 1)] - entry) / entry * 100 if sig == 1 else (entry - C[min(i + H, nB - 1)]) / entry * 100


# 取 2026 信号
rows = list(csv.DictReader(open(CSV, encoding="utf-8-sig")))
sig26 = []
for r in rows:
    dtobj = dt.datetime.strptime(r["time"], "%Y/%m/%d %H:%M").replace(tzinfo=UTC)
    if dtobj < Y2026:
        continue
    i = ts_to_i.get(int(dtobj.timestamp() * 1000))
    if i is None:
        continue
    sg = int(r["signal"]); p = bisect.bisect_right(flips, i); nf = flips[p] if p < len(flips) else i + H
    sig26.append((i, sg, atr[i], C[i], nf, r["grade"]))


def equity(pnls_size, fee=0.0):
    eq = 1.0
    for size, pnl in pnls_size:
        ret = size * (pnl - fee) / 100.0  # fee 为每笔费率(%)
        eq *= (1 + ret)
    return eq - 1


def report(name, trades):
    # trades: list of (size, pnl)
    if not trades:
        print(f"  {name}: 无样本"); return
    pnls = [p for _, p in trades]
    m = sum(pnls) / len(pnls)
    gross = equity([(1.0, p) for _, p in trades])  # 满仓复利(忽略size差异)
    eq_full = math.prod(1 + p / 100 for p in pnls)
    eq_fee1 = math.prod(1 + (p - 0.1) / 100 for p in pnls)
    eq_fee2 = math.prod(1 + (p - 0.2) / 100 for p in pnls)
    eq_w = equity(trades, 0.0); eq_w2 = equity(trades, 0.2)
    print(f"  {name}: n={len(trades)} 均盈 {m:+.3f}%")
    print(f"     满仓复利(毛) {(eq_full-1)*100:+.1f}%  扣0.1% {(eq_fee1-1)*100:+.1f}%  扣0.2% {(eq_fee2-1)*100:+.1f}%")
    print(f"     含仓位权重复利(毛) {eq_w*100:+.1f}%  扣0.2% {eq_w2*100:+.1f}%")


print(f"2026 年信号数: {len(sig26)}")
all_trades = [(1.0, exit_v4(i, sg, a, en, nf)) for (i, sg, a, en, nf, g) in sig26]
report("口径A 全信号(满仓, v4-exit)", all_trades)

sa_trades = []
for (i, sg, a, en, nf, g) in sig26:
    if g in ("S", "A"):
        size = 1.0 if g == "S" else 0.7
        sa_trades.append((size, exit_v4(i, sg, a, en, nf, 1.5 if g == "S" else 1.2)))
report("口径B V4分级S+A(含仓位权重, v4-exit)", sa_trades)

# 对照: 全周期(2022-09起) 满仓复利
allrows = []
for r in rows:
    dtobj = dt.datetime.strptime(r["time"], "%Y/%m/%d %H:%M").replace(tzinfo=UTC)
    i = ts_to_i.get(int(dtobj.timestamp() * 1000))
    if i is None:
        continue
    sg = int(r["signal"]); p = bisect.bisect_right(flips, i); nf = flips[p] if p < len(flips) else i + H
    allrows.append(exit_v4(i, sg, atr[i], C[i], nf))
print(f"\n[对照] 全周期(2022-09~今) {len(allrows)}笔 满仓复利(毛) {(math.prod(1+p/100 for p in allrows)-1)*100:+.1f}%  "
      f"扣0.2% {(math.prod(1+(p-0.2)/100 for p in allrows)-1)*100:+.1f}%")
