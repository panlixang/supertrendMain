# -*- coding: utf-8 -*-
"""高周期定向 + 低周期执行 ST 翻转 + 移动保本/ATR 跟踪

目的: 解决"4h ST信号太迟钝, 出现时已涨很多" 且要保住正期望
关键设计(避免上次的"出场也被过滤"偏差):
  - 4h 定向 / 美盘时段 只作用于【入场】
  - 【出场】一律用未过滤的反向翻转 + 保本跟踪止损
执行方式: 翻转在 bar i 收盘确认, bar i+1 开盘成交(无未来函数)
"""
import sys, sqlite3, datetime as dt
import numpy as np
import pandas as pd

sys.path.insert(0, r"d:/个人项目代码/supertrendMain/backend")
from indicators import super_trend, ta_atr, ta_ema

DB = r"d:/个人项目代码/supertrendMain/backend/candle_data.db"
INIT_CAP = 10000.0
TAKER_FEE, SLIP = 0.0005, 0.0003
ST_P, ST_M, ATR_N = 10, 3.0, 14
BE_BUF = 0.001          # 保本位的手续费缓冲
K_LIST = [1.5, 2.0, 2.5, 3.0]
START_MS = int(pd.Timestamp("2022-01-01", tz="UTC").timestamp() * 1000)

UTC = dt.timezone.utc
SESSION_START_MIN, SESSION_END_MIN = 9 * 60 + 30, 16 * 60


def nth_weekday(y, m, wd, n):
    d = dt.date(y, m, 1)
    off = (wd - d.weekday()) % 7
    return dt.date(y, m, 1 + off + (n - 1) * 7)


def is_dst(t):
    y = t.year
    ds = dt.datetime.combine(nth_weekday(y, 3, 6, 2), dt.time(7, 0), tzinfo=UTC)
    de = dt.datetime.combine(nth_weekday(y, 11, 6, 1), dt.time(6, 0), tzinfo=UTC)
    return ds <= t < de


def et_minute(open_ms):
    t = dt.datetime.fromtimestamp(open_ms / 1000, tz=UTC)
    off = -4 * 60 if is_dst(t) else -5 * 60
    te = t + dt.timedelta(minutes=off)
    return te.hour * 60 + te.minute


def in_session(open_ms):
    m = et_minute(open_ms)
    return SESSION_START_MIN <= m < SESSION_END_MIN


def load(symbol, tf):
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT ts,o,h,l,c,vol FROM candles WHERE symbol=? AND tf=? AND ts>=? ORDER BY ts",
        (symbol, tf, START_MS)).fetchall()
    con.close()
    return [{"ts": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "vol": r[5]}
            for r in rows]


def resample(base, minutes):
    """把 15m 合成 30m (ts 为开盘时间, 两根合一)"""
    step = minutes * 60 * 1000
    buck = {}
    for b in base:
        k = b["ts"] // step
        if k not in buck:
            buck[k] = {"ts": k * step, "o": b["o"], "h": b["h"], "l": b["l"],
                       "c": b["c"], "vol": b["vol"]}
        else:
            x = buck[k]
            x["h"] = max(x["h"], b["h"]); x["l"] = min(x["l"], b["l"])
            x["c"] = b["c"]; x["vol"] += b["vol"]
    return [buck[k] for k in sorted(buck)]


def series(base):
    return ([x["o"] for x in base], [x["h"] for x in base], [x["l"] for x in base],
            [x["c"] for x in base], [x["ts"] for x in base])


def build_dir(exe_ts, htf_bars, hours):
    """把高周期 ST 方向对齐到执行周期(只用已收完的高周期bar, 无未来函数)"""
    o, h, l, c, ts = series(htf_bars)
    st = super_trend(o, h, l, c, periods=ST_P, multiplier=ST_M, change_atr=True)
    trend = st["trend"]
    close_ts = np.array(ts) + hours * 3600 * 1000
    exe = np.array(exe_ts)
    idx = np.searchsorted(close_ts, exe, side="right") - 1
    out = []
    for j in idx:
        if j < 0 or j >= len(trend) or trend[j] is None:
            out.append(0)
        else:
            out.append(trend[j])
    return np.array(out)


def pfstr(p):
    return "inf" if p == float("inf") else f"{p:.2f}"


def backtest(sig_list, flip_all, o, h, l, c, atr, *, long_only=False,
             k=2.0, be_mult=1.0, use_stop=True, trail=True, exit_flip=True,
             lo=0, hi=None):
    hi = len(c) if hi is None else hi
    enter_map = {}
    for s in sig_list:
        j = s["i"] + 1                     # 下一根开盘成交
        if lo <= j < hi:
            enter_map[j] = s
    cash = INIT_CAP; pos = 0; units = 0.0; entry = 0.0; entry_i = -1
    stop = None; extreme = 0.0; be_done = False; eq_entry = INIT_CAP
    equity = [INIT_CAP]; trades = []

    def open_pos(px, d, i):
        nonlocal cash, pos, units, entry, stop, limit, entry_i, extreme, be_done, eq_entry
        eq_entry = cash
        fill = px * (1 + SLIP) if d == 1 else px * (1 - SLIP)
        u = cash / fill
        cash -= u * fill * TAKER_FEE
        pos = d; units = u; entry = fill; entry_i = i
        a = atr[i] if atr[i] else atr[max(0, i - 1)]
        a = a if a else 0.0
        stop = (entry - k * a if d == 1 else entry + k * a) if use_stop else None
        extreme = fill; be_done = False

    def close_pos(px, i, reason):
        nonlocal cash, pos, units, entry_i
        fill = px * (1 - SLIP) if pos == 1 else px * (1 + SLIP)
        pnl = units * (fill - entry) if pos == 1 else units * (entry - fill)
        cash += pnl - units * fill * TAKER_FEE
        trades.append({"side": pos, "pnl": cash - eq_entry, "entry_i": entry_i,
                       "exit_i": i, "reason": reason})
        pos = 0; units = 0.0; entry_i = -1

    limit = None
    for i in range(lo, hi):
        if pos != 0:
            if use_stop and trail and i - 1 >= entry_i:
                a = atr[i - 1] or atr[i] or 0.0
                if pos == 1:
                    extreme = max(extreme, h[i - 1])
                    stop = max(stop, extreme - k * a)
                    if not be_done and extreme - entry >= be_mult * a:
                        stop = max(stop, entry * (1 + BE_BUF)); be_done = True
                else:
                    extreme = min(extreme, l[i - 1])
                    stop = min(stop, extreme + k * a)
                    if not be_done and entry - extreme >= be_mult * a:
                        stop = min(stop, entry * (1 - BE_BUF)); be_done = True
            if use_stop and stop is not None:
                if pos == 1 and l[i] <= stop:
                    close_pos(stop, i, "stop")
                elif pos == -1 and h[i] >= stop:
                    close_pos(stop, i, "stop")
        if pos != 0 and exit_flip:
            d = flip_all.get(i)
            if d is not None and d != pos:
                close_pos(c[i], i, "flip")
        if pos == 0:
            s = enter_map.get(i)
            if s is not None and not (long_only and s["dir"] == -1):
                open_pos(o[i], s["dir"], i)
        equity.append(cash + (units * c[i] if pos != 0 else 0.0))

    if pos != 0:
        close_pos(c[hi - 1], hi - 1, "eod")
        equity.append(cash)

    nt = len(trades)
    if nt and abs(sum(t["pnl"] for t in trades) - (equity[-1] - INIT_CAP)) > 1e-6:
        raise AssertionError("盈亏对账不符")
    wins = [t for t in trades if t["pnl"] > 0]
    gp = sum(t["pnl"] for t in wins)
    gl = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    peak = equity[0]; mdd = 0.0
    for e in equity:
        peak = max(peak, e); mdd = min(mdd, e / peak * 100 - 100)
    return {"total_ret": equity[-1] / INIT_CAP * 100 - 100, "max_dd": mdd, "n": nt,
            "win_rate": (len(wins) / nt * 100) if nt else 0.0,
            "pf": (gp / gl) if gl > 0 else float("inf")}


def year_ranges(ts):
    yrs = {}
    for i, t in enumerate(ts):
        y = pd.to_datetime(t, unit="ms").year
        if y not in yrs:
            yrs[y] = [i, i + 1]
        yrs[y][1] = i + 1
    return yrs


def run_dataset(name, base, htf_bars=None, htf_hours=None, session_ok=True):
    o, h, l, c, ts = series(base)
    atr = ta_atr(h, l, c, ATR_N)
    atr = [a if a else 0.0 for a in atr]
    st = super_trend(o, h, l, c, periods=ST_P, multiplier=ST_M, change_atr=True)
    flips = st["flips"]
    flip_all = {f["i"]: (1 if f["type"] == "buy" else -1) for f in flips}
    dir4 = build_dir(ts, htf_bars, htf_hours) if htf_bars else None

    def mk(use_dir, use_sess):
        out = []
        for f in flips:
            d = 1 if f["type"] == "buy" else -1
            if use_dir and dir4[f["i"]] != d:
                continue
            if use_sess and not in_session(ts[f["i"]]):
                continue
            out.append({"i": f["i"], "dir": d})
        return out

    yr = year_ranges(ts)
    years = [y for y in sorted(yr) if y >= 2023]
    span = (ts[-1] - ts[0]) / (365.25 * 24 * 3600 * 1000)
    print("\n" + "#" * 108)
    print(f"  {name}   区间 {pd.to_datetime(ts[0], unit='ms').date()} → "
          f"{pd.to_datetime(ts[-1], unit='ms').date()}  翻转总数 {len(flips)}")
    print("#" * 108)
    print(f"  {'变体':<26}{'k':>5}{'笔数':>6}{'笔/年':>7}{'胜率':>7}{'盈亏比':>7}"
          f"{'净收益':>10}{'回撤':>9}  " + "".join(f"{y:>9}" for y in years))
    print("-" * 108)

    def show(label, kk, use_dir, use_sess, use_stop, trail):
        ss = mk(use_dir, use_sess)
        kw = dict(k=kk, be_mult=1.0, use_stop=use_stop, trail=trail, exit_flip=True)
        r = backtest(ss, flip_all, o, h, l, c, atr, **kw)
        ys = [backtest(ss, flip_all, o, h, l, c, atr,
                       lo=yr[y][0], hi=yr[y][1], **kw)["total_ret"] for y in years]
        print(f"  {label:<26}{kk:>5.1f}{r['n']:>6}{r['n']/span:>7.0f}"
              f"{r['win_rate']:>6.1f}%{pfstr(r['pf']):>7}{r['total_ret']:>9.2f}%"
              f"{r['max_dd']:>8.2f}%  " + "".join(f"{v:>8.2f}%" for v in ys))
        return r

    results = {}
    results["base"] = show("①纯翻转(出场=反向翻转)", 0, False, False, False, False)
    if dir4 is not None:
        results["dir"] = show("②4h定向(出场=反向翻转)", 0, True, False, False, False)
        if session_ok:
            results["dir_sess"] = show("③4h定向+美盘(反向翻转)", 0, True, True, False, False)
        for kk in K_LIST:
            results[f"d_s_{kk}"] = show("⑥4h定向+ATR止损(不跟踪)", kk, True, False, True, False)
        for kk in K_LIST:
            results[f"d_t_{kk}"] = show("④4h定向+保本跟踪", kk, True, False, True, True)
        if session_ok:
            for kk in K_LIST:
                results[f"ds_t_{kk}"] = show("⑤4h定向+美盘+保本跟踪", kk, True, True, True, True)
    return results


def main():
    print("高周期定向 + 低周期执行 ST 翻转 · 所有过滤只作用于入场, 出场一律不过滤 · 右侧为逐年净收益")

    # ETH: 4H 定向 + 1H 执行
    eth1 = load("ETH-USDT", "1H")
    eth4 = load("ETH-USDT", "4H")
    run_dataset("ETH-USDT  定向=4H  执行=1H", eth1, eth4, 4)

    # BTC 阶梯: 执行周期从长到短
    btc4 = load("BTC-USDT", "4h")
    run_dataset("BTC-USDT  无定向(基线)  执行=4h", btc4, None, None)

    btc1 = load("BTC-USDT", "1h")
    btc4full = load("BTC-USDT", "4h")
    run_dataset("BTC-USDT  定向=4h  执行=1h", btc1, btc4full, 4)

    btc15 = load("BTC-USDT", "15m")
    run_dataset("BTC-USDT  定向=4h  执行=30m", resample(btc15, 30), btc4full, 4)
    run_dataset("BTC-USDT  定向=4h  执行=15m", btc15, btc4full, 4)


if __name__ == "__main__":
    main()
