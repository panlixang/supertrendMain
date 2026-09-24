# -*- coding: utf-8 -*-
"""Risk Score (风险评分) 写入 808 笔 + 验证"暂停高风险"是否提升期望
趋势衰减40: MA30_slope下降20 + ER下降20
ST失效反馈40: 前3笔亏损数/3*40
市场结构20: range扩大10 + vol异常10
规则: risk_score>60 -> pause_ST=1 (暂停)
验证: 对比 全808 / 交易子集(risk<=60) / 暂停子集(risk>60) 在 v4-exit 下的期望
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


def f(x):
    try:
        return float(x)
    except Exception:
        return float("nan")


def risk_of(r, recent):
    ma30 = f(r["MA30_slope"]); er = f(r["ER20"]); ra = f(r["range_atr"]); ap = f(r["ATR_pct"])
    a_ma30 = 20 if ma30 < 0 else (10 if ma30 < 0.2 else 0)
    a_er = 20 if er < 0.1 else (10 if er < 0.2 else 0)
    losses = sum(1 for x in recent[-3:] if x < 0)
    cnt = min(3, len(recent))
    b = (losses / cnt * 40) if cnt else 0
    c_range = 10 if ra > 2.0 else (5 if ra > 1.5 else 0)
    c_vol = 10 if ap > 2.5 else (5 if ap > 1.8 else 0)
    return a_ma30 + a_er + b + c_range + c_vol


# ---- 算 risk_score + pause_ST, 写入CSV ----
rows = list(csv.DictReader(open(CSV, encoding="utf-8-sig")))
recent = []
out_cols = list(rows[0].keys()) + ["risk_score", "pause_ST"]
new = []
for r in rows:
    risk = risk_of(r, recent)
    sig_norm = "1" if r["signal"] in ("1", "buy") else ("-1" if r["signal"] in ("-1", "sell") else r["signal"])
    row = dict(r); row["signal"] = sig_norm
    row.update({"risk_score": round(risk, 1), "pause_ST": 1 if risk > 60 else 0})
    new.append(row)
    recent.append(f(r["pnl_pct"]))

with open(CSV, "w", newline="", encoding="utf-8-sig") as fp:
    w = csv.DictWriter(fp, fieldnames=out_cols); w.writeheader(); w.writerows(new)

rs = [x["risk_score"] for x in new]
pause = [x for x in new if x["pause_ST"] == 1]
trade = [x for x in new if x["pause_ST"] == 0]
print(f"已写入 risk_score + pause_ST")
print(f"risk_score 区间 {min(rs)}~{max(rs)} 均值 {sum(rs)/len(rs):.1f}")
print(f"暂停(pause_ST=1, >60): {len(pause)} 笔   交易(risk<=60): {len(trade)} 笔")

# ---- 验证: 原始K线算 v4-exit 期望, 分组 ----
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


def grp(rows_sub):
    out = []
    for r in rows_sub:
        i = ts_to_i.get(int(dt.datetime.strptime(r["time"], "%Y/%m/%d %H:%M").replace(tzinfo=UTC).timestamp() * 1000))
        if i is None:
            continue
        sg = int(r["signal"]); p = bisect.bisect_right(flips, i); nf = flips[p] if p < len(flips) else i + H
        out.append(exit_v4(i, sg, atr[i], C[i], nf))
    return out


def stat(pnls, name):
    if not pnls:
        print(f"  {name}: 无样本"); return
    m = sum(pnls) / len(pnls); sd = math.sqrt(sum((x - m) ** 2 for x in pnls) / (len(pnls) - 1))
    t = m / (sd / math.sqrt(len(pnls))); wr = sum(1 for x in pnls if x > 0) / len(pnls) * 100
    print(f"  {name:<22} n={len(pnls):<4} 均盈 {m:+.3f}%  胜率 {wr:4.1f}%  t={t:+.2f}  扣费0.2%:{m-0.20:+.3f}%")


allp = grp(new); trp = grp(trade); pap = grp(pause)
print("\n=== v4-exit 期望对比 ===")
stat(allp, "全808")
stat(trp, "交易子集(risk<=60)")
stat(pap, "暂停子集(risk>60)")
