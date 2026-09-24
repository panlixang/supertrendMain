# -*- coding: utf-8 -*-
"""
用 ST Score V3 驱动 4 组出场方案, 在 1h 808笔上验证期望
过滤: Score>=65 (S/A中>=65); 方案4 用 4h加权分(反向-10)
"""
import sqlite3, math, bisect, csv, datetime as dt

DB = r"d:\个人项目代码\supertrendMain\backend\candle_data.db"
CSV = r"d:\个人项目代码\supertrendMain\backend\backtest\st_signals_1h.csv"
SYM, ATR_LEN, MULT, H = "BTC-USDT", 10, 3, 300
UTC = dt.timezone.utc


def load(tf):
    con = sqlite3.connect(DB)
    rows = con.execute("SELECT ts,o,h,l,c,vol FROM candles WHERE symbol=? AND tf=? "
                       "AND ts>=? ORDER BY ts", (SYM, tf, 1661990400000)).fetchall()
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


def exits_for(i, sig, a, entry, nf, kind):
    """返回该笔 pnl% (float)"""
    c_ = C; h_ = H_; l_ = L
    if kind == "rev":
        ex = c_[nf]; return (ex - entry) / entry * 100 if sig == 1 else (entry - ex) / entry * 100
    if kind == "fixed":
        if sig == 1:
            sl, tp = entry - 1.5 * a, entry + 3 * a
            for j in range(i + 1, min(i + H + 1, n)):
                if l_[j] <= sl: return (sl - entry) / entry * 100
                if h_[j] >= tp: return (tp - entry) / entry * 100
        else:
            sl, tp = entry + 1.5 * a, entry - 3 * a
            for j in range(i + 1, min(i + H + 1, n)):
                if h_[j] >= sl: return (sl - entry) / entry * 100
                if l_[j] <= tp: return (tp - entry) / entry * 100
        ex = c_[min(i + H, n - 1)]; return (ex - entry) / entry * 100 if sig == 1 else (entry - ex) / entry * 100
    if kind == "trail":
        if sig == 1:
            sl = entry - 1.5 * a; reached = False
            for j in range(i + 1, min(i + H + 1, n)):
                if not reached:
                    if l_[j] <= sl: return (sl - entry) / entry * 100
                    if h_[j] >= entry + a: reached = True; sl = entry
                else:
                    if l_[j] <= sl: return (sl - entry) / entry * 100
                    if j == nf: return (c_[nf] - entry) / entry * 100
        else:
            sl = entry + 1.5 * a; reached = False
            for j in range(i + 1, min(i + H + 1, n)):
                if not reached:
                    if h_[j] >= sl: return (sl - entry) / entry * 100
                    if l_[j] <= entry - a: reached = True; sl = entry
                else:
                    if h_[j] >= sl: return (sl - entry) / entry * 100
                    if j == nf: return (entry - c_[nf]) / entry * 100
        ex = c_[min(i + H, n - 1)]; return (ex - entry) / entry * 100 if sig == 1 else (entry - ex) / entry * 100
    if kind == "tp1trail":
        if sig == 1:
            tp1 = entry + 2 * a
            for j in range(i + 1, min(i + H + 1, n)):
                if j == nf and h_[j] < tp1:
                    return (c_[nf] - entry) / entry * 100
                if h_[j] >= tp1:
                    half = (tp1 - entry) / entry * 100
                    rest = (c_[nf] - entry) / entry * 100
                    return 0.5 * half + 0.5 * rest
        else:
            tp1 = entry - 2 * a
            for j in range(i + 1, min(i + H + 1, n)):
                if j == nf and l_[j] > tp1:
                    return (entry - c_[nf]) / entry * 100
                if l_[j] <= tp1:
                    half = (entry - tp1) / entry * 100
                    rest = (entry - c_[nf]) / entry * 100
                    return 0.5 * half + 0.5 * rest
        ex = c_[min(i + H, n - 1)]; return (ex - entry) / entry * 100 if sig == 1 else (entry - ex) / entry * 100


def stat(pnls, name):
    if not pnls:
        print(f"  {name}: 无样本"); return
    m = sum(pnls) / len(pnls)
    sd = math.sqrt(sum((x - m) ** 2 for x in pnls) / (len(pnls) - 1)) if len(pnls) > 1 else 0
    se = sd / math.sqrt(len(pnls)); t = m / se if se else 0
    wr = sum(1 for x in pnls if x > 0) / len(pnls) * 100
    print(f"  {name:<26} n={len(pnls):<4} 均盈 {m:+.3f}%  胜率 {wr:4.1f}%  "
          f"合计 {sum(pnls):+7.1f}%  t={t:+.2f}  扣费0.1%:{m-0.10:+.3f}%  扣费0.2%:{m-0.20:+.3f}%")


bars = load("1h")
ts = [b[0] for b in bars]; O = [b[1] for b in bars]; H_ = [b[2] for b in bars]
L = [b[3] for b in bars]; C = [b[4] for b in bars]; n = len(C)
trend, atr = supertrend(H_, L, C, ATR_LEN, MULT)
flips = [i for i in range(1, n) if not math.isnan(trend[i]) and trend[i] != trend[i - 1]]
flipset = set(flips)

# 读 CSV: 按 epoch ms -> (score, align, signal)
ts_to_i = {ts[i]: i for i in range(n)}
scoremap = {}
for r in csv.DictReader(open(CSV, encoding="utf-8-sig")):
    try:
        dtobj = dt.datetime.strptime(r["time"], "%Y/%m/%d %H:%M").replace(tzinfo=UTC)
    except Exception:
        continue
    ms = int(dtobj.timestamp() * 1000)
    scoremap[ms] = (float(r["score"]), int(float(r["align"])), int(r["signal"]))

sig_list = []  # (i, sig, a, entry, nf, score, align)
for i in range(30, n):
    if i in flipset:
        if ts[i] not in scoremap:
            continue
        sc, al, sg = scoremap[ts[i]]
        p = bisect.bisect_right(flips, i)
        nf = flips[p] if p < len(flips) else i + H
        sig_list.append((i, sg, atr[i], C[i], nf, sc, al))

print(f"\n1h 信号 {len(sig_list)} 笔 (匹配CSV)")
# 分桶
base = [s for s in sig_list]
g65 = [s for s in sig_list if s[5] >= 65]
g4h = [s for s in sig_list if s[5] + (-10 if s[6] == -1 else 0) >= 65]

print("\n=== 基线: 全部808, 反向信号出场 ===")
stat([exits_for(s[0], s[1], s[2], s[3], s[4], "rev") for s in base], "全部/rev")
print("\n=== 全部808, 各出场 ===")
for k in ["fixed", "trail", "tp1trail"]:
    stat([exits_for(s[0], s[1], s[2], s[3], s[4], k) for s in base], f"全部/{k}")
print("\n=== Score>=65 (n={}) ===".format(len(g65)))
for k in ["rev", "fixed", "trail", "tp1trail"]:
    stat([exits_for(s[0], s[1], s[2], s[3], s[4], k) for s in g65], f"S/A>=65/{k}")
print("\n=== 方案4: 4h加权分>=65 (n={}) ===".format(len(g4h)))
stat([exits_for(s[0], s[1], s[2], s[3], s[4], "tp1trail") for s in g4h], "4h加权/tp1trail")
