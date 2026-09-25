# -*- coding: utf-8 -*-
"""2026 BTC 1h (149笔): 100U*10x 收益/盈亏比/胜率 + 统计性过滤(signal_filter B/C/D)后胜率
出场: v4-exit (SL1.5ATR/TP2ATR半仓/ST尾随)
100U*10x => 名义=1000U, 每笔盈亏(U)=pnl%*10 (毛); 净= (pnl%-0.1)*10 (0.1%往返费)
"""
import sqlite3, math, bisect, csv, datetime as dt, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from signal_filter import SignalFilter

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


def tstat(pnls):
    n = len(pnls)
    if n < 2: return 0.0
    m = sum(pnls) / n
    var = sum((x - m) ** 2 for x in pnls) / (n - 1)
    if var <= 0: return 0.0
    return m / math.sqrt(var / n)


def stats(pnls, label):
    n = len(pnls)
    if n == 0:
        print(f"  {label}: 无样本"); return
    wins = [p for p in pnls if p > 0]; losses = [p for p in pnls if p <= 0]
    wr = len(wins) / n * 100
    aw = (sum(wins) / len(wins) * 10) if wins else 0
    al = (sum(losses) / len(losses) * 10) if losses else 0
    plr = aw / abs(al) if al != 0 else float("inf")
    gp = sum(wins) * 10; gl = abs(sum(losses)) * 10
    pf = gp / gl if gl > 0 else float("inf")
    grossU = sum(p * 10 for p in pnls)
    netU = sum((p - 0.1) * 10 for p in pnls)
    # max DD on cumulative net P&L (U), start 0
    run = 0.0; peak = 0.0; mdd = 0.0
    for p in pnls:
        run += (p - 0.1) * 10
        peak = max(peak, run); mdd = max(mdd, peak - run)
    eq = math.prod(1 + p / 100 for p in pnls)
    print(f"  {label} (n={n}):")
    print(f"    胜率 {wr:5.1f}%  盈亏比(均盈/均亏) {plr:5.2f}  盈利因子PF {pf:5.2f}")
    print(f"    均盈 +{aw:6.2f}U  均亏 {al:6.2f}U  t值 {tstat(pnls):+5.2f}")
    print(f"    毛收益 {grossU:+9.1f}U  净(扣0.1%费) {netU:+9.1f}U  满仓复利 {eq*100-100:+.1f}%")
    print(f"    最大回撤(净值曲线) {mdd:7.1f}U  最终权益(净) {100+netU:8.1f}U")


bars = load("1h", 1661990400000)
ts = [b[0] for b in bars]; HI = [b[2] for b in bars]; LO = [b[3] for b in bars]; C = [b[4] for b in bars]; nB = len(bars)
trend, atr = supertrend(HI, LO, C, ATR_LEN, MULT)
flips = [i for i in range(1, nB) if not math.isnan(trend[i]) and trend[i] != trend[i - 1]]
ts_to_i = {ts[i]: i for i in range(nB)}

rows = list(csv.DictReader(open(CSV, encoding="utf-8-sig")))
recs = []  # (pnl, risk_score, bars_since_flip, ADX14)
for r in rows:
    dtobj = dt.datetime.strptime(r["time"], "%Y/%m/%d %H:%M").replace(tzinfo=UTC)
    if dtobj < Y2026:
        continue
    i = ts_to_i.get(int(dtobj.timestamp() * 1000))
    if i is None:
        continue
    sg = int(r["signal"]); p = bisect.bisect_right(flips, i); nf = flips[p] if p < len(flips) else i + H
    pnl = exit_v4(i, sg, atr[i], C[i], nf)
    recs.append((pnl, float(r["risk_score"]), float(r["bars_since_flip"]), float(r["ADX14"])))

print(f"2026 共 {len(recs)} 笔, 出场=v4-exit\n")
stats([p for p, *_ in recs], "① 不过滤(全部149笔)")

for strat in ["B", "C", "D"]:
    sf = SignalFilter(strat)
    kept = [p for p, rs, bs, adx in recs if not sf.should_filter({"risk_score": rs, "bars_since_flip": bs, "ADX14": adx})]
    stats(kept, f"② 统计过滤策略{strat} (保留 {len(kept)} 笔)")
