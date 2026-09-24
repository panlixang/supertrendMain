# -*- coding: utf-8 -*-
"""
V4 评分 + 执行规则出场验证 (1h 808笔)
交易: S(>=80) 正常仓 SL1.5/TP2/尾随 ; A(65-80) 0.7仓 SL1.2/TP2/尾随 ; B/C不交易
对照: 方案2(all trail) 与 v4-exit(all 808)
"""
import sqlite3, math, bisect, csv, datetime as dt

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


def exit_v4(i, sig, a, entry, nf, sl_mult, tp_mult=2):
    if sig == 1:
        sl, tp1 = entry - sl_mult * a, entry + tp_mult * a
        for j in range(i + 1, min(i + H + 1, n)):
            if L[j] <= sl: return (sl - entry) / entry * 100
            if H_[j] >= tp1:
                return 0.5 * (tp1 - entry) / entry * 100 + 0.5 * (C[nf] - entry) / entry * 100
            if j == nf: return (C[nf] - entry) / entry * 100
    else:
        sl, tp1 = entry + sl_mult * a, entry - tp_mult * a
        for j in range(i + 1, min(i + H + 1, n)):
            if H_[j] >= sl: return (sl - entry) / entry * 100
            if L[j] <= tp1:
                return 0.5 * (entry - tp1) / entry * 100 + 0.5 * (entry - C[nf]) / entry * 100
            if j == nf: return (entry - C[nf]) / entry * 100
    return (C[min(i + H, n - 1)] - entry) / entry * 100 if sig == 1 else (entry - C[min(i + H, n - 1)]) / entry * 100


def exit_plan2(i, sig, a, entry, nf):
    if sig == 1:
        sl = entry - 1.5 * a; reached = False
        for j in range(i + 1, min(i + H + 1, n)):
            if not reached:
                if L[j] <= sl: return (sl - entry) / entry * 100
                if H_[j] >= entry + a: reached = True; sl = entry
            else:
                if L[j] <= sl: return 0.0
                if j == nf: return (C[nf] - entry) / entry * 100
    else:
        sl = entry + 1.5 * a; reached = False
        for j in range(i + 1, min(i + H + 1, n)):
            if not reached:
                if H_[j] >= sl: return (sl - entry) / entry * 100
                if L[j] <= entry - a: reached = True; sl = entry
            else:
                if H_[j] >= sl: return 0.0
                if j == nf: return (entry - C[nf]) / entry * 100
    return (C[min(i + H, n - 1)] - entry) / entry * 100 if sig == 1 else (entry - C[min(i + H, n - 1)]) / entry * 100


def stat(price, weight, name):
    if not price:
        print(f"  {name}: 无样本"); return
    m = sum(price) / len(price)
    sd = math.sqrt(sum((x - m) ** 2 for x in price) / (len(price) - 1)) if len(price) > 1 else 0
    se = sd / math.sqrt(len(price)); t = m / se if se else 0
    wr = sum(1 for x in price if x > 0) / len(price) * 100
    wm = sum(weight) / len(weight) if weight else 0
    print(f"  {name:<22} n={len(price):<4} 价均盈 {m:+.3f}%  加权均盈 {wm:+.3f}%  "
          f"胜率 {wr:4.1f}%  t={t:+.2f}  扣费0.2%价:{m-0.20:+.3f}%")


bars = load("1h", 1661990400000)
ts = [b[0] for b in bars]; H_ = [b[2] for b in bars]; L = [b[3] for b in bars]; C = [b[4] for b in bars]; n = len(C)
trend, atr = supertrend(H_, L, C, ATR_LEN, MULT)
flips = [i for i in range(1, n) if not math.isnan(trend[i]) and trend[i] != trend[i - 1]]
flipset = set(flips)
ts_to_i = {ts[i]: i for i in range(n)}

sig = {}  # ms -> (grade, signal)
for r in csv.DictReader(open(CSV, encoding="utf-8-sig")):
    try:
        dtobj = dt.datetime.strptime(r["time"], "%Y/%m/%d %H:%M").replace(tzinfo=UTC)
    except Exception:
        continue
    sig[int(dtobj.timestamp() * 1000)] = (r["grade"], int(r["signal"]))

S, A, ALL = [], [], []
for i in range(30, n):
    if i in flipset and ts[i] in sig:
        g, sg = sig[ts[i]]; p = bisect.bisect_right(flips, i); nf = flips[p] if p < len(flips) else i + H
        S.append((i, sg, atr[i], C[i], nf, 1.0, 1.5)) if g == "S" else None
        A.append((i, sg, atr[i], C[i], nf, 0.7, 1.2)) if g == "A" else None
        ALL.append((i, sg, atr[i], C[i], nf, 1.0, 1.5))

print(f"\nV4 可执行信号: S={len(S)}  A={len(A)}  S+A={len(S)+len(A)}  (全部808)")
print("\n=== 执行规则: S级(SL1.5/TP2/尾随,1.0仓) A级(SL1.2/TP2/尾随,0.7仓) ===")
stat([exit_v4(*s[:5], s[5], 2) for s in S], [s[5] * exit_v4(*s[:5], s[5], 2) for s in S], "仅S级")
stat([exit_v4(*s[:5], s[5], 2) for s in A], [s[5] * exit_v4(*s[:5], s[5], 2) for s in A], "仅A级")
sa_p = [exit_v4(*s[:5], s[5], 2) for s in S + A]
sa_w = [s[5] * exit_v4(*s[:5], s[5], 2) for s in S + A]
stat(sa_p, sa_w, "S+A合计(>=65)")

print("\n=== 对照 ===")
stat([exit_plan2(*s[:5]) for s in ALL], [exit_plan2(*s[:5]) for s in ALL], "方案2(all trail,808)")
stat([exit_v4(*s[:5], 1.5, 2) for s in ALL], [exit_v4(*s[:5], 1.5, 2) for s in ALL], "v4-exit(SL1.5/TP2,808)")
