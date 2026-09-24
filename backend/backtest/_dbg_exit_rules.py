# -*- coding: utf-8 -*-
"""
出场规则对比: 用原始K线逐笔模拟不同出场, 验证能否得到扣费后正期望
规则: rev(反向信号出场,基线) / fixedK(固定K*ATR止盈止损) / trailK(移动止损K*ATR)
     + 叠加 4h 同向过滤(align4==1)
"""
import sqlite3, math, bisect, sys, datetime as dt

DB = r"d:\个人项目代码\supertrendMain\backend\candle_data.db"
SYM = "BTC-USDT"
UTC = dt.timezone.utc
ATR_LEN, MULT = 10, 3
HORIZON = 300  # 最多模拟后续300根


def load(tf):
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT ts,o,h,l,c,vol FROM candles WHERE symbol=? AND tf=? ORDER BY ts", (SYM, tf)).fetchall()
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
    n = len(c)
    atr = wilder_atr(h, l, c, p)
    hl2 = [(h[i] + l[i]) / 2 for i in range(n)]
    up = [hl2[i] - mult * atr[i] if not math.isnan(atr[i]) else float("nan") for i in range(n)]
    dn = [hl2[i] + mult * atr[i] if not math.isnan(atr[i]) else float("nan") for i in range(n)]
    upT = [float("nan")] * n; dnT = [float("nan")] * n; trend = [float("nan")] * n
    for i in range(n):
        if math.isnan(up[i]):
            continue
        if i == 0:
            upT[i], dnT[i] = up[i], dn[i]; trend[i] = 1
        else:
            upT[i] = max(up[i], upT[i - 1]) if c[i - 1] > upT[i - 1] else up[i]
            dnT[i] = min(dn[i], dnT[i - 1]) if c[i - 1] < dnT[i - 1] else dn[i]
            if c[i - 1] > dnT[i - 1]:
                trend[i] = 1
            elif c[i - 1] < upT[i - 1]:
                trend[i] = -1
            else:
                trend[i] = trend[i - 1]
    return trend, atr


def build(tf):
    bars = load(tf)
    ts = [b[0] for b in bars]; c = [b[4] for b in bars]
    h = [b[2] for b in bars]; l = [b[3] for b in bars]
    trend, atr = supertrend(h, l, c, ATR_LEN, MULT)
    flips = [i for i in range(1, len(c)) if not math.isnan(trend[i]) and trend[i] != trend[i - 1]]
    sigs = [(i, int(trend[i])) for i in range(1, len(c))
            if not math.isnan(trend[i]) and trend[i] != trend[i - 1] and i >= 30]
    return ts, c, h, l, atr, sigs, flips


def align4_per_signal(tf_ts, sigs):
    """4h 方向对齐(用于4h过滤检验)"""
    b4 = load("4h")
    t4 = [x[0] for x in b4]; trend4, _ = supertrend([x[2] for x in b4], [x[3] for x in b4],
                                                     [x[4] for x in b4], ATR_LEN, MULT)
    out = {}
    for i, sig in sigs:
        k = bisect.bisect_right(t4, tf_ts[i]) - 1
        if k >= 0 and not math.isnan(trend4[k]):
            out[i] = 1 if int(trend4[k]) == sig else 0
        else:
            out[i] = -1  # 4h 未知
    return out


def sim(bars_c, bars_h, bars_l, sigs, atr, kind, m, flips):
    res = {}
    for idx, (i, sig) in enumerate(sigs):
        a = atr[i]
        if math.isnan(a) or a <= 0:
            continue
        entry = bars_c[i]
        if kind == "rev":
            p = bisect.bisect_right(flips, i)
            if p >= len(flips):
                continue
            ex = bars_c[flips[p]]
            pnl = (ex - entry) / entry * 100 if sig == 1 else (entry - ex) / entry * 100
            res[i] = pnl
            continue
        if kind == "fixed":
            tp = entry + m * a if sig == 1 else entry - m * a
            sl = entry - m * a if sig == 1 else entry + m * a
            ex = None
            for j in range(i + 1, min(i + HORIZON + 1, len(bars_c))):
                if sig == 1:
                    if bars_l[j] <= sl:
                        ex = sl; break
                    if bars_h[j] >= tp:
                        ex = tp; break
                else:
                    if bars_h[j] >= sl:
                        ex = sl; break
                    if bars_l[j] <= tp:
                        ex = tp; break
            if ex is None:
                ex = bars_c[min(i + HORIZON, len(bars_c) - 1)]
            pnl = (ex - entry) / entry * 100 if sig == 1 else (entry - ex) / entry * 100
            res[i] = pnl
        else:  # trail
            ex = None
            if sig == 1:
                best = bars_h[i]; trail = entry - m * a
                for j in range(i + 1, min(i + HORIZON + 1, len(bars_c))):
                    best = max(best, bars_h[j]); trail = max(trail, best - m * a)
                    if bars_l[j] <= trail:
                        ex = trail; break
            else:
                worst = bars_l[i]; trail = entry + m * a
                for j in range(i + 1, min(i + HORIZON + 1, len(bars_c))):
                    worst = min(worst, bars_l[j]); trail = min(trail, worst + m * a)
                    if bars_h[j] >= trail:
                        ex = trail; break
            if ex is None:
                ex = bars_c[min(i + HORIZON, len(bars_c) - 1)]
            pnl = (ex - entry) / entry * 100 if sig == 1 else (entry - ex) / entry * 100
            res[i] = pnl
    return res


def stat(pnls, name):
    if not pnls:
        print(f"  {name}: 无样本"); return
    v = pnls; m = sum(v) / len(v)
    sd = math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1)) if len(v) > 1 else 0.0
    se = sd / math.sqrt(len(v)); t = m / se if se else 0.0
    wr = sum(1 for x in v if x > 0) / len(v) * 100
    print(f"  {name:<22} n={len(v):<5} 均盈 {m:+.3f}%  胜率 {wr:4.1f}%  合计 {sum(v):+7.1f}%  "
          f"t={t:+.2f}  扣费0.1%:{m-0.10:+.3f}%  扣费0.2%:{m-0.20:+.3f}%")


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "15m"
    ts, c, h, l, atr, sigs, flips = build(tf)
    a4 = align4_per_signal(ts, sigs)
    print(f"\n===== {tf} 出场规则对比 (信号 {len(sigs)} 笔, K线 {len(c)}) =====")
    print(f"区间 {dt.datetime.fromtimestamp(ts[0]/1000,UTC).date()} ~ "
          f"{dt.datetime.fromtimestamp(ts[-1]/1000,UTC).date()}")

    rules = [("rev", 0), ("fixed2", 2), ("fixed3", 3), ("trail2", 2), ("trail3", 3)]
    pnldict = {}
    for kind, m in rules:
        pnldict[(kind, m)] = sim(c, h, l, sigs, atr, kind, m, flips)

    print("\n--- 全部信号 ---")
    for kind, m in rules:
        stat(list(pnldict[(kind, m)].values()), f"{kind}{m if m else ''}")

    print("\n--- 仅 4h 同向过滤(align4==1) ---")
    for kind, m in rules:
        filtered = [pnl for i, pnl in pnldict[(kind, m)].items() if a4.get(i) == 1]
        stat(filtered, f"{kind}{m if m else ''}|4h")


if __name__ == "__main__":
    main()
