# -*- coding: utf-8 -*-
"""BTC 1h walk-forward (扩展窗口) 验证 v4-exit 策略
训练=某年之前全部信号; 测试=该年(纯OOS)
口径:
  A 固定 v4-exit (SL1.5/TP2/ST尾随)
  B 每年在训练集重寻优 (sl,tp) 后测OOS
  C 固定 v4-exit + 统计过滤D(阈值训练集拟合)
"""
import sqlite3, math, bisect, csv, datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from signal_filter import SignalFilter

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


def exit_pnl(i, sig, a, entry, nf, sl_mult, tp_mult):
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


def tstat(pnls):
    n = len(pnls)
    if n < 2: return 0.0
    m = sum(pnls) / n
    var = sum((x - m) ** 2 for x in pnls) / (n - 1)
    return m / math.sqrt(var / n) if var > 0 else 0.0


def pf_of(pnls):
    w = sum(x for x in pnls if x > 0); l = -sum(x for x in pnls if x <= 0)
    return w / l if l > 0 else float("inf")


def metrics(pnls):
    n = len(pnls)
    if n == 0: return (0, 0, 0, 0, 0, 0, 0)
    m = sum(pnls) / n
    wins = [x for x in pnls if x > 0]; losses = [x for x in pnls if x <= 0]
    wr = len(wins) / n * 100
    aw = sum(wins) / len(wins) if wins else 0; al = sum(losses) / len(losses) if losses else 0
    plr = aw / abs(al) if al else 0
    comp = math.prod(1 + x / 100 for x in pnls) - 1
    comp_f = math.prod(1 + (x - 0.2) / 100 for x in pnls) - 1
    return (n, m, wr, plr, pf_of(pnls), tstat(pnls), comp_f)


bars = load("1h", 1661990400000)
ts = [b[0] for b in bars]; HI = [b[2] for b in bars]; LO = [b[3] for b in bars]; C = [b[4] for b in bars]; nB = len(bars)
trend, atr = supertrend(HI, LO, C, ATR_LEN, MULT)
flips = [i for i in range(1, nB) if not math.isnan(trend[i]) and trend[i] != trend[i - 1]]
ts_to_i = {ts[i]: i for i in range(nB)}

# 读全部808信号
recs = []  # dict: year, pnl(固定1.5/2), sig, risk_score, bars_since_flip, ADX14, filter_score
sfD = SignalFilter("D")
for r in csv.DictReader(open(CSV, encoding="utf-8-sig")):
    dtobj = dt.datetime.strptime(r["time"], "%Y/%m/%d %H:%M").replace(tzinfo=UTC)
    i = ts_to_i.get(int(dtobj.timestamp() * 1000))
    if i is None:
        continue
    sg = int(r["signal"]); p = bisect.bisect_right(flips, i); nf = flips[p] if p < len(flips) else i + H
    pnl = exit_pnl(i, sg, atr[i], C[i], nf, 1.5, 2.0)
    fscore = sfD.calculate_filter_score({"risk_score": float(r["risk_score"]),
                                          "bars_since_flip": float(r["bars_since_flip"]),
                                          "ADX14": float(r["ADX14"])})
    recs.append({"year": dtobj.year, "pnl": pnl, "fscore": fscore,
                 "i": i, "sig": sg, "a": atr[i], "entry": C[i], "nf": nf})

recs.sort(key=lambda x: x["year"])
years = sorted(set(x["year"] for x in recs))
print(f"全样本 {len(recs)} 笔, 年份 {years}\n")


def line(tag, pnls):
    n, m, wr, plr, pf, t, cf = metrics(pnls)
    if n == 0:
        print(f"  {tag}: 无样本"); return
    print(f"  {tag:18s} n={n:>3d} 均盈 {m:+6.3f}% 胜率 {wr:5.1f}% 盈亏比 {plr:4.2f} PF {pf:5.2f} t {t:+5.2f} 扣0.2%复利 {cf*100:+.1f}%")


SL_GRID = [1.0, 1.2, 1.5, 2.0]
TP_GRID = [1.5, 2.0, 2.5, 3.0]

print("="*100)
print("A) 固定 v4-exit(1.5/2) —— 扩展窗口 OOS")
print("="*100)
pooled_oos = []
for Y in years:
    if Y == years[0]:
        continue
    train = [x for x in recs if x["year"] < Y]
    test = [x for x in recs if x["year"] == Y]
    print(f"\n[{Y}年 OOS] 训练={min(y for y in years if y<Y)}~{Y-1} ({len(train)}笔) | 测试 {len(test)}笔")
    line("  训练(已知)", [x["pnl"] for x in train])
    line("  OOS测试", [x["pnl"] for x in test])
    pooled_oos += [x["pnl"] for x in test]
line(">> 合并OOS(2023-2026)", pooled_oos)

print("\n" + "="*100)
print("B) 每年训练集重寻优(sl,tp) —— 真 walk-forward OOS")
print("="*100)
pooled_oos_b = []
for Y in years:
    if Y == years[0]:
        continue
    train = [x for x in recs if x["year"] < Y]
    test = [x for x in recs if x["year"] == Y]
    # 在训练集上选 (sl,tp) 最大化 PF
    best = None; bestpf = -1
    for sl in SL_GRID:
        for tp in TP_GRID:
            pn = [exit_pnl(x["i"], x["sig"], x["a"], x["entry"], x["nf"], sl, tp) for x in train]
            pf = pf_of(pn)
            if pf != float("inf") and pf > bestpf:
                bestpf = pf; best = (sl, tp)
    sl, tp = best
    oos = [exit_pnl(x["i"], x["sig"], x["a"], x["entry"], x["nf"], sl, tp) for x in test]
    print(f"\n[{Y}年 OOS] 训练最优(sl,tp)=({sl},{tp}) PF={bestpf:.2f} | 测试 {len(test)}笔")
    line("  OOS测试", oos)
    pooled_oos_b += oos
line(">> 合并OOS(2023-2026)", pooled_oos_b)

print("\n" + "="*100)
print("C) 固定 v4-exit + 统计过滤D(阈值每年训练集拟合) —— OOS")
print("="*100)
pooled_oos_c = []
for Y in years:
    if Y == years[0]:
        continue
    train = [x for x in recs if x["year"] < Y]
    test = [x for x in recs if x["year"] == Y]
    # 训练集上选阈值(保留 fscore<=thr) 最大化 PF
    best = 60; bestpf = -1
    for thr in range(30, 81):
        kept = [x["pnl"] for x in train if x["fscore"] <= thr]
        if len(kept) < 10:
            continue
        pf = pf_of(kept)
        if pf != float("inf") and pf > bestpf:
            bestpf = pf; best = thr
    oos = [x["pnl"] for x in test if x["fscore"] <= best]
    print(f"\n[{Y}年 OOS] 训练最优阈值={best} PF={bestpf:.2f} | 测试保留 {len(oos)}/{len(test)}笔")
    line("  OOS测试", oos)
    pooled_oos_c += oos
line(">> 合并OOS(2023-2026)", pooled_oos_c)
