# -*- coding: utf-8 -*-
"""
构建 BTC-USDT 15m 的 SuperTrend 信号数据集 (2022-01-01 ~ 今)
ST 标准版: atrLen=10, mult=3 (TradingView supertrend 内置逻辑)
每根 ST 翻转 = 一条信号; 导出 CSV.
字段与 1h 版本一致(30列), 对齐用的更高周期为 1h (15m->1h = 4x, 同 1h->4h 比例).
"""
import sqlite3
import math
import time
import bisect
import datetime as dt
import csv

DB = r"d:\个人项目代码\supertrendMain\backend\candle_data.db"
SYM = "BTC-USDT"
UTC = dt.timezone.utc
TF = "15m"
HTF = "1h"
ST_ATR = 10
ST_MULT = 3
START = dt.datetime(2022, 1, 1, tzinfo=UTC)


def _get(url, timeout=12):
    import urllib.request, json
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "st-signals/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        print("REST 失败:", e)
        return None


def fetch_tf(tf):
    """从 START 抓取到今, 补齐指定周期; 支持断点续传(从已有最旧数据继续往回补)"""
    start_ms = int(START.timestamp() * 1000)
    end = int(dt.datetime.now(UTC).timestamp() * 1000)
    con = sqlite3.connect(DB)
    have = con.execute(
        "SELECT MIN(ts),MAX(ts),COUNT(*) FROM candles WHERE symbol=? AND tf=?",
        (SYM, tf)).fetchone()
    con.close()
    if have and have[0] is not None and have[0] <= start_ms + 3_600_000:
        print(f"[{tf}] 已有全量(起始 {dt.datetime.fromtimestamp(have[0]/1000, UTC).date()}), 跳过抓取")
        return
    # 断点续传: 已有数据则从最旧一根继续往更早抓, 避免重复拉取近期
    after = have[0] if (have and have[0] is not None) else end
    collected = {}
    pages = 0
    while True:
        url = (f"https://www.okx.com/api/v5/market/history-candles"
               f"?instId={SYM}&bar={tf}&limit=300&after={after}")
        d = None
        for attempt in range(4):
            d = _get(url, timeout=20)
            if d:
                break
            time.sleep(1.5)
        if not d or d.get("code") != "0" or not d.get("data"):
            print(f"  [{tf}] 请求失败/结束, 已累计 {len(collected)} 根, 停止")
            break
        for r in d["data"]:
            ts = int(r[0])
            if ts < start_ms or ts > end:
                continue
            collected[ts] = (ts, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
        mints = min(int(r[0]) for r in d["data"])
        if mints <= start_ms:
            break
        after = mints
        pages += 1
        time.sleep(0.12)
        if pages % 20 == 0:
            print(f"  [{tf}] 已抓 {len(collected)} 根 ... 当前最旧 "
                  f"{dt.datetime.fromtimestamp(after/1000, UTC).date()}")
        if pages > 8000:
            break
    if collected:
        con = sqlite3.connect(DB)
        con.executemany(
            "INSERT INTO candles(symbol,tf,ts,o,h,l,c,vol) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(symbol,tf,ts) DO NOTHING",
            [(SYM, tf, *v) for v in collected.values()],
        )
        con.commit()
        con.close()
        print(f"[{tf}] 写入 {len(collected)} 根 (截至 "
              f"{dt.datetime.fromtimestamp(min(collected)/1000, UTC).date()})")
    else:
        print(f"[{tf}] 无新数据")


def load(tf):
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT ts,o,h,l,c,vol FROM candles WHERE symbol=? AND tf=? ORDER BY ts", (SYM, tf)
    ).fetchall()
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


def sma(v, p):
    out = [float("nan")] * len(v)
    for i in range(p - 1, len(v)):
        out[i] = sum(v[i - p + 1:i + 1]) / p
    return out


def adx(h, l, c, p=14):
    n = len(c); pdm = [0.0] * n; mdm = [0.0] * n; tr = [0.0] * n; tr[0] = h[0] - l[0]
    for i in range(1, n):
        up = h[i] - h[i - 1]; dn = l[i - 1] - l[i]
        pdm[i] = up if (up > dn and up > 0) else 0.0
        mdm[i] = dn if (dn > up and dn > 0) else 0.0
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    ap = [float("nan")] * n; an = [float("nan")] * n; at = [float("nan")] * n
    if n >= p + 1:
        ap[p] = sum(pdm[1:p + 1]) / p; an[p] = sum(mdm[1:p + 1]) / p; at[p] = sum(tr[1:p + 1]) / p
        for i in range(p + 1, n):
            ap[i] = (ap[i - 1] * (p - 1) + pdm[i]) / p
            an[i] = (an[i - 1] * (p - 1) + mdm[i]) / p
            at[i] = (at[i - 1] * (p - 1) + tr[i]) / p
    pdi = [float("nan")] * n; mdi = [float("nan")] * n
    for i in range(p, n):
        pdi[i] = 100 * ap[i] / at[i] if at[i] else 0.0
        mdi[i] = 100 * an[i] / at[i] if at[i] else 0.0
    dx = [float("nan")] * n
    for i in range(p, n):
        s = pdi[i] + mdi[i]; dx[i] = 0.0 if s == 0 else 100 * abs(pdi[i] - mdi[i]) / s
    ad = [float("nan")] * n
    if n >= 2 * p:
        ad[2 * p - 1] = sum(dx[p:2 * p]) / p
        for i in range(2 * p, n):
            ad[i] = (ad[i - 1] * (p - 1) + dx[i]) / p
    return ad


def supertrend(h, l, c, p, mult):
    n = len(c)
    atr = wilder_atr(h, l, c, p)
    hl2 = [(h[i] + l[i]) / 2 for i in range(n)]
    up = [hl2[i] - mult * atr[i] if not math.isnan(atr[i]) else float("nan") for i in range(n)]
    dn = [hl2[i] + mult * atr[i] if not math.isnan(atr[i]) else float("nan") for i in range(n)]
    upT = [float("nan")] * n; dnT = [float("nan")] * n; trend = [float("nan")] * n; st = [float("nan")] * n
    for i in range(n):
        if math.isnan(up[i]):
            continue
        if i == 0:
            upT[i], dnT[i] = up[i], dn[i]; trend[i] = 1; st[i] = dn[i]
        else:
            upT[i] = max(up[i], upT[i - 1]) if c[i - 1] > upT[i - 1] else up[i]
            dnT[i] = min(dn[i], dnT[i - 1]) if c[i - 1] < dnT[i - 1] else dn[i]
            if c[i - 1] > dnT[i - 1]:
                trend[i] = 1
            elif c[i - 1] < upT[i - 1]:
                trend[i] = -1
            else:
                trend[i] = trend[i - 1]
            st[i] = dnT[i] if trend[i] == 1 else upT[i]
    return trend, st, atr


def rsi(c, p=14):
    n = len(c); out = [float("nan")] * n
    if n < p + 1:
        return out
    g = ls = 0.0
    for i in range(1, p + 1):
        d = c[i] - c[i - 1]; g += max(d, 0.0); ls += max(-d, 0.0)
    ag, al = g / p, ls / p
    out[p] = 100.0 if al == 0 else 100.0 - 100.0 / (1 + ag / al)
    for i in range(p + 1, n):
        d = c[i] - c[i - 1]
        ag = (ag * (p - 1) + max(d, 0.0)) / p
        al = (al * (p - 1) + max(-d, 0.0)) / p
        out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1 + ag / al)
    return out


def stdev(v, p):
    out = [float("nan")] * len(v)
    for i in range(p - 1, len(v)):
        w = v[i - p + 1:i + 1]; m = sum(w) / p
        out[i] = (sum((x - m) ** 2 for x in w) / p) ** 0.5
    return out


def pct_rank(src, length):
    n = len(src); out = [float("nan")] * n
    for i in range(length - 1, n):
        w = src[i - length + 1:i + 1]; x = src[i]
        below = sum(1 for t in w if t <= x)
        out[i] = below / length * 100
    return out


def squeeze_state(h, l, c):
    n = len(c)
    basis = sma(c, 20); dev = stdev(c, 20); atr20 = wilder_atr(h, l, c, 20)
    bb_up = [float("nan")] * n; bb_lo = [float("nan")] * n
    kc_up = [float("nan")] * n; kc_lo = [float("nan")] * n; bbw = [float("nan")] * n
    for i in range(n):
        if math.isnan(basis[i]) or math.isnan(dev[i]) or math.isnan(atr20[i]):
            continue
        bb_up[i] = basis[i] + 2 * dev[i]; bb_lo[i] = basis[i] - 2 * dev[i]
        kc_up[i] = basis[i] + 1.5 * atr20[i]; kc_lo[i] = basis[i] - 1.5 * atr20[i]
        bbw[i] = 4 * dev[i] / basis[i] if basis[i] else float("nan")
    sq = [0] * n
    for i in range(n):
        if math.isnan(bb_up[i]) or math.isnan(kc_up[i]):
            continue
        if bb_lo[i] > kc_lo[i] and bb_up[i] < kc_up[i]:
            sq[i] = 1
    rank = pct_rank(bbw, 100)
    return sq, rank


def main():
    fetch_tf(TF)
    fetch_tf(HTF)
    bars = load(TF)
    print(f"[{TF}] 总行数 {len(bars)}  "
          f"{dt.datetime.fromtimestamp(bars[0][0]/1000, UTC)} ~ "
          f"{dt.datetime.fromtimestamp(bars[-1][0]/1000, UTC)}")
    ts = [b[0] for b in bars]; o = [b[1] for b in bars]; h = [b[2] for b in bars]
    l = [b[3] for b in bars]; c = [b[4] for b in bars]; v = [b[5] for b in bars]
    n = len(c)

    trend, st, atr = supertrend(h, l, c, ST_ATR, ST_MULT)
    adx_a = adx(h, l, c, 14)
    ma30 = sma(c, 30)
    rsi14 = rsi(c, 14)
    vol20 = sma(v, 20)
    sq_15m, bbw_rank = squeeze_state(h, l, c)

    # 更高周期方向(1h), 同 ST 参数
    b2 = load(HTF)
    ts2 = [x[0] for x in b2]; o2 = [x[1] for x in b2]; h2 = [x[2] for x in b2]
    l2 = [x[3] for x in b2]; c2 = [x[4] for x in b2]
    trend2, _, _ = supertrend(h2, l2, c2, ST_ATR, ST_MULT)
    print(f"[{HTF}] 总行数 {len(b2)}  用于方向对齐")

    # 所有翻转点索引(反向信号)
    flips = []
    for i in range(1, n):
        if math.isnan(trend[i]) or math.isnan(trend[i - 1]):
            continue
        if trend[i] != trend[i - 1]:
            flips.append(i)

    rows = []
    last_flip = -1
    for i in range(1, n):
        if math.isnan(trend[i]) or math.isnan(trend[i - 1]):
            continue
        if trend[i] == trend[i - 1]:
            continue
        sig = int(trend[i])
        ai = atr[i]
        ma_i = ma30[i]; ma_i1 = ma30[i - 1] if not math.isnan(ma30[i - 1]) else ma_i
        if i + 20 > n - 1 or i < 30:
            continue
        st_dist = (c[i] - st[i]) / ai
        body = abs(c[i] - o[i]) / ai
        rng = (h[i] - l[i]) if (h[i] - l[i]) != 0 else 1e-9
        close_pos = (c[i] - l[i]) / rng
        atr_pct = ai / c[i] * 100
        if i >= 20:
            num = abs(c[i] - c[i - 20])
            den = sum(abs(c[j] - c[j - 1]) for j in range(i - 19, i + 1))
            er20 = num / den if den else 0.0
        else:
            er20 = float("nan")
        hh20 = max(h[i - 20:i])
        break_dist = (c[i] - hh20) / ai
        bs_flip = i - last_flip if last_flip >= 0 else 0
        ma_slope = (ma_i - ma_i1) / ai
        if sig == 1:
            mfe10 = max((h[j] - c[i]) for j in range(i + 1, i + 11)) / ai
            mfe20 = max((h[j] - c[i]) for j in range(i + 1, i + 21)) / ai
            mae20 = max((c[i] - l[j]) for j in range(i + 1, i + 21)) / ai
            fut_ret = (c[i + 20] - c[i]) / c[i] * 100
        else:
            mfe10 = max((c[i] - l[j]) for j in range(i + 1, i + 11)) / ai
            mfe20 = max((c[i] - l[j]) for j in range(i + 1, i + 21)) / ai
            mae20 = max((h[j] - c[i]) for j in range(i + 1, i + 21)) / ai
            fut_ret = (c[i] - c[i + 20]) / c[i] * 100
        success = 1 if fut_ret > 0 else 0

        r14 = rsi14[i]
        vol = v[i]
        vol_r = vol / vol20[i] if not math.isnan(vol20[i]) else float("nan")
        sq = sq_15m[i]
        bw = bbw_rank[i]
        k = bisect.bisect_right(ts2, ts[i]) - 1
        if k >= 0 and not math.isnan(trend2[k]):
            htf = int(trend2[k])
            align = 1 if htf == sig else 0
        else:
            htf = -1
            align = -1
        # 反向信号价格 + 止盈/止损 + 盈亏
        p = bisect.bisect_right(flips, i)
        if p < len(flips):
            rev_price = c[flips[p]]
            rev_price_r = round(rev_price, 2)
            if sig == 1:
                exit_res = "TP" if rev_price > c[i] else "SL"
                pnl = (rev_price - c[i]) / c[i] * 100
            else:
                exit_res = "TP" if rev_price < c[i] else "SL"
                pnl = (c[i] - rev_price) / c[i] * 100
            pnl_r = round(pnl, 3)
        else:
            rev_price_r = ""
            exit_res = ""
            pnl_r = ""
        rows.append([
            dt.datetime.fromtimestamp(ts[i] / 1000, UTC).strftime("%Y-%m-%d %H:%M:%S"),
            sig, round(c[i], 2), round(st_dist, 4), round(body, 4), round(close_pos, 4),
            round(atr_pct, 4), round(er20, 4), round(adx_a[i], 2), round((h[i] - l[i]) / ai, 4),
            round(hh20, 2), round(break_dist, 4), bs_flip, 0, round(ma_slope, 4),
            round(mfe10, 4), round(mfe20, 4), round(mae20, 4), round(fut_ret, 4), success,
            (round(r14, 2) if not math.isnan(r14) else ""),
            round(vol, 2),
            (round(vol_r, 3) if not math.isnan(vol_r) else ""),
            htf, align, sq,
            (round(bw, 2) if not math.isnan(bw) else ""),
            rev_price_r, exit_res, pnl_r
        ])
        last_flip = i

    header = ["time", "signal", "close", "st_distance_atr", "body_atr", "close_position",
              "ATR_pct", "ER20", "ADX14", "range_atr", "break_high20", "break_distance_atr",
              "bars_since_flip", "trend_age", "MA30_slope", "future_MFE_10", "future_MFE_20",
              "future_MAE_20", "future_return_20", "success_label",
              "RSI14", "volume", "vol_ratio", "htf_dir", "align", "squeeze", "bbw_rank",
              "reverse_signal_price", "exit_result", "pnl_pct"]
    out = r"d:\个人项目代码\supertrendMain\backend\backtest\st_signals_15m.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"\n信号条数: {len(rows)}  导出: {out}")
    nl = sum(1 for r in rows if r[1] == 1)
    ns = len(rows) - nl
    succ = sum(r[19] for r in rows)
    print(f"LONG 信号: {nl}  SHORT 信号: {ns}  整体成功率: {succ/len(rows)*100:.1f}%")
    print(f"LONG 成功率: {sum(r[19] for r in rows if r[1]==1)/nl*100:.1f}%   "
          f"SHORT 成功率: {sum(r[19] for r in rows if r[1]==-1)/ns*100:.1f}%")
    aligned = sum(1 for r in rows if r[24] == 1)
    squeezed = sum(1 for r in rows if r[25] == 1)
    print(f"1h方向一致: {aligned}/{len(rows)} ({aligned/len(rows)*100:.1f}%)")
    print(f"处于挤压(低波动): {squeezed}/{len(rows)} ({squeezed/len(rows)*100:.1f}%)")


if __name__ == "__main__":
    main()
