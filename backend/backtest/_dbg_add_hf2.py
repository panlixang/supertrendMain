# -*- coding: utf-8 -*-
"""重写 HF 为 3 个百分位硬过滤 (1=拒绝,0=通过)
HF1: body_atr > P99(极端K线)
HF2: flip_count_20 >= 4(极端震荡, 需原始K线)
HF3: atr_pct < P10(极低波动)
并验证: 通过全部3过滤的子集, v4-exit 期望
"""
import sqlite3, math, bisect, csv, datetime as dt

DB = r"d:\个人项目代码\supertrendMain\backend\candle_data.db"
CSV = r"d:\个人项目代码\supertrendMain\backend\backtest\st_signals_1h.csv"
SYM, ATR_LEN, MULT, H = "BTC-USDT", 10, 3, 300
UTC = dt.timezone.utc
OLD_HF = ["HF1", "HF2", "HF3", "HF4"]


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


rows = list(csv.DictReader(open(CSV, encoding="utf-8-sig")))
body = sorted(f(r["body_atr"]) for r in rows)
atrp = sorted(f(r["ATR_pct"]) for r in rows)
n = len(rows)
P99_body = body[min(n - 1, int(0.99 * (n - 1)))]
P10_atr = atrp[min(n - 1, int(0.10 * (n - 1)))]
print(f"P99 body_atr = {P99_body:.3f}  -> HF1 拒绝 body_atr > 此值")
print(f"P10 atr_pct  = {P10_atr:.3f}  -> HF3 拒绝 atr_pct < 此值")

# 原始K线算翻转历史
bars = load("1h", 1661990400000)
ts = [b[0] for b in bars]; H_ = [b[2] for b in bars]; L = [b[3] for b in bars]; C = [b[4] for b in bars]; nB = len(C)
trend, atr = supertrend(H_, L, C, ATR_LEN, MULT)
flips = [i for i in range(1, nB) if not math.isnan(trend[i]) and trend[i] != trend[i - 1]]
ts_to_i = {ts[i]: i for i in range(nB)}


def flip_count_20(i):
    return bisect.bisect_left(flips, i) - bisect.bisect_left(flips, i - 20)


base_cols = [k for k in rows[0].keys() if k not in OLD_HF]
out_cols = base_cols + ["HF1", "HF2", "HF3"]
new, pass_all = [], []
for r in rows:
    i = ts_to_i.get(int(dt.datetime.strptime(r["time"], "%Y/%m/%d %H:%M").replace(tzinfo=UTC).timestamp() * 1000))
    bo = f(r["body_atr"]); ap = f(r["ATR_pct"])
    hf1 = 1 if bo > P99_body else 0
    hf2 = 1 if (i is not None and flip_count_20(i) >= 4) else 0
    hf3 = 1 if ap < P10_atr else 0
    row = {k: r[k] for k in base_cols}; row.update({"HF1": hf1, "HF2": hf2, "HF3": hf3})
    new.append(row)
    if i is not None and hf1 == hf2 == hf3 == 0:
        sg = int(r["signal"]); p = bisect.bisect_right(flips, i)
        nf = flips[p] if p < len(flips) else i + H
        pass_all.append((i, sg, atr[i], C[i], nf))

with open(CSV, "w", newline="", encoding="utf-8-sig") as fp:
    w = csv.DictWriter(fp, fieldnames=out_cols); w.writeheader(); w.writerows(new)

c1 = sum(x["HF1"] for x in new); c2 = sum(x["HF2"] for x in new); c3 = sum(x["HF3"] for x in new)
anyf = sum(1 for x in new if x["HF1"] or x["HF2"] or x["HF3"])
print(f"\n已写入 808 笔 + HF1/HF2/HF3 (旧HF1-HF4已替换)")
print(f"拒绝数: HF1(极端K线>P99)={c1}  HF2(20根翻转>=4)={c2}  HF3(极低波动<P10)={c3}")
print(f"被任一拒绝: {anyf}  通过全部3过滤: {808-anyf}")


def exit_v4(i, sig, a, entry, nf, sl_mult=1.5, tp_mult=2):
    if sig == 1:
        sl, tp1 = entry - sl_mult * a, entry + tp_mult * a
        for j in range(i + 1, min(i + H + 1, nB)):
            if L[j] <= sl: return (sl - entry) / entry * 100
            if H_[j] >= tp1: return 0.5 * (tp1 - entry) / entry * 100 + 0.5 * (C[nf] - entry) / entry * 100
            if j == nf: return (C[nf] - entry) / entry * 100
    else:
        sl, tp1 = entry + sl_mult * a, entry - tp_mult * a
        for j in range(i + 1, min(i + H + 1, nB)):
            if H_[j] >= sl: return (sl - entry) / entry * 100
            if L[j] <= tp1: return 0.5 * (entry - tp1) / entry * 100 + 0.5 * (entry - C[nf]) / entry * 100
            if j == nf: return (entry - C[nf]) / entry * 100
    return (C[min(i + H, nB - 1)] - entry) / entry * 100 if sig == 1 else (entry - C[min(i + H, nB - 1)]) / entry * 100


if pass_all:
    pnls = [exit_v4(*s) for s in pass_all]
    m = sum(pnls) / len(pnls); sd = math.sqrt(sum((x - m) ** 2 for x in pnls) / (len(pnls) - 1))
    t = m / (sd / math.sqrt(len(pnls))); wr = sum(1 for x in pnls if x > 0) / len(pnls) * 100
    print(f"\n[验证] 通过全部3过滤 n={len(pnls)}: v4-exit 均盈 {m:+.3f}%  胜率 {wr:.1f}%  "
          f"t={t:+.2f}  扣费0.2%:{m-0.20:+.3f}%")
    print(f"        (对照: 全808 v4-exit = +0.623% t=+9.88)")
