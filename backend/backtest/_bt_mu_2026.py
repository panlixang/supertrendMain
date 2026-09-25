# -*- coding: utf-8 -*-
"""MU-USDT-SWAP(镁光美股代币) 1h 2026 回测: ST翻转 + v4-exit(不过滤).
美股代币只在美股时段有K线(约周一~周五 13:30-20:00 ET), 故1h根数远少于24/7币种.
"""
import sys, math, bisect, datetime as dt
sys.path.insert(0, ".")
from history import fetch_candles

SYM, TF = "MU-USDT-SWAP", "1h"
ATR_LEN, MULT, H = 10, 3, 300
UTC = dt.timezone.utc
Y2026 = int(dt.datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)


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


print(f"拉取 {SYM} {TF} ...")
raw = fetch_candles(TF, limit=8000, symbol=SYM)
raw = [b for b in raw if b.ts >= Y2026]
if raw:
    raw = raw[:-1]  # 丢弃可能未收盘的当前K
print(f"2026 可用K线 {len(raw)} 根")

ts = [b.ts for b in raw]; HI = [b.h for b in raw]; LO = [b.l for b in raw]; C = [b.c for b in raw]; nB = len(raw)
trend, atr = supertrend(HI, LO, C, ATR_LEN, MULT)
flips = [i for i in range(1, nB) if not math.isnan(trend[i]) and trend[i] != trend[i - 1]]

pnls = []
for i in flips:
    sg = int(trend[i])
    p = bisect.bisect_right(flips, i); nf = flips[p] if p < len(flips) else i + H
    pnls.append(exit_pnl(i, sg, atr[i], C[i], nf, 1.5, 2.0))

n = len(pnls)
print(f"=== {SYM} 1h 2026 信号 {n} 笔 (ST翻转+v4-exit, 不过滤) ===")
m = sum(pnls) / n
wins = [x for x in pnls if x > 0]; losses = [x for x in pnls if x <= 0]
wr = len(wins) / n * 100
aw = sum(wins) / len(wins) if wins else 0; al = sum(losses) / len(losses) if losses else 0
plr = aw / abs(al) if al else 0
comp = math.prod(1 + x / 100 for x in pnls) - 1
comp_f = math.prod(1 + (x - 0.2) / 100 for x in pnls) - 1
grossU = sum(x * 10 for x in pnls); netU = sum((x - 0.1) * 10 for x in pnls)
print(f"均盈 {m:+.3f}%  胜率 {wr:.1f}%  盈亏比 {plr:.2f}  PF {pf_of(pnls):.2f}  t {tstat(pnls):+.2f}")
print(f"满仓复利(毛) {comp*100:+.1f}%  扣0.2%费 {comp_f*100:+.1f}%")
print(f"100U*10x: 毛 {grossU:+.1f}U  净(扣0.1%) {netU:+.1f}U  最终权益(净) {100+netU:.1f}U")
