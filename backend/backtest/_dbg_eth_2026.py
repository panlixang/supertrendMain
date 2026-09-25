# -*- coding: utf-8 -*-
"""ETH 1h 2026 回测: ST翻转 + v4-exit(不过滤)。先拉OKX数据入库，再算。
v4-exit: SL1.5ATR / TP2ATR半仓 / ST尾随。100U*10x 折算。
"""
import sqlite3, math, bisect, datetime as dt, time, urllib.request, json, os, sys

DB = r"d:\个人项目代码\supertrendMain\backend\candle_data.db"
SYM, TF, ATR_LEN, MULT, H = "ETH-USDT", "1h", 10, 3, 300
UTC = dt.timezone.utc
START_MS = int(dt.datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)
Y2026 = int(dt.datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
Y2027 = int(dt.datetime(2027, 1, 1, tzinfo=UTC).timestamp() * 1000)


def fetch_and_store():
    con = sqlite3.connect(DB)
    con.execute("DELETE FROM candles WHERE symbol=? AND tf=?", (SYM, TF))  # 清掉旧的OKX残留
    print("开始拉取 ETH 1h (Binance ETHUSDT) ...")
    rows = []
    start = START_MS
    while True:
        url = f"https://api.binance.com/api/v3/klines?symbol=ETHUSDT&interval=1h&startTime={start}&limit=1000"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
        if not data:
            break
        for d in data:
            ts = int(d[0]); o, h, l, c, v = map(float, (d[1], d[2], d[3], d[4], d[5]))
            rows.append((SYM, TF, ts, o, h, l, c, v))
        if len(data) < 1000:
            break
        start = int(data[-1][0]) + 3600_000
        time.sleep(0.05)
    rows.sort(key=lambda x: x[2])
    con.executemany("INSERT OR IGNORE INTO candles(symbol,tf,ts,o,h,l,c,vol) VALUES(?,?,?,?,?,?,?,?)", rows)
    con.commit()
    print(f"入库 {len(rows)} 根 ETH 1h (最早 {dt.datetime.utcfromtimestamp(rows[0][2]/1000)})")
    con.close()


def load(start_ms):
    con = sqlite3.connect(DB)
    rs = con.execute("SELECT ts,o,h,l,c,vol FROM candles WHERE symbol=? AND tf=? AND ts>=? ORDER BY ts",
                     (SYM, TF, start_ms)).fetchall()
    con.close()
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rs]


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


def main():
    fetch_and_store()
    global nB, HI, LO, C
    bars = load(0)
    ts = [b[0] for b in bars]; HI = [b[2] for b in bars]; LO = [b[3] for b in bars]; C = [b[4] for b in bars]; nB = len(bars)
    print(f"ETH 1h 载入 {nB} 根, {dt.datetime.utcfromtimestamp(ts[0]/1000)} ~ {dt.datetime.utcfromtimestamp(ts[-1]/1000)}")
    trend, atr = supertrend(HI, LO, C, ATR_LEN, MULT)
    flips = [i for i in range(1, nB) if not math.isnan(trend[i]) and trend[i] != trend[i - 1]]
    print(f"总翻转信号 {len(flips)} 个")

    pnls = []
    for i in flips:
        if ts[i] < Y2026 or ts[i] >= Y2027:
            continue
        sg = int(trend[i])
        p = bisect.bisect_right(flips, i); nf = flips[p] if p < len(flips) else i + H
        pnls.append(exit_pnl(i, sg, atr[i], C[i], nf, 1.5, 2.0))

    n = len(pnls)
    print(f"\n=== ETH 1h 2026 信号 {n} 笔 (策略: ST翻转+v4-exit, 不过滤) ===")
    if n == 0:
        print("无样本"); return
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


if __name__ == "__main__":
    main()
