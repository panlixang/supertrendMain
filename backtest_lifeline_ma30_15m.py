# -*- coding: utf-8 -*-
"""MA30 生命线四大买卖点 (葛兰威尔简化版) + SuperTrend 方向过滤 · BTC 15m 回测

买点一: 价格向上突破生命线(MA30), 并带动生命线由转上 → 买
买点二: 生命线已形成上升角度后, 价格第一次回调到生命线附近(|C-MA|<=TOL*ATR) → 再买
卖点一: 价格向下跌破生命线, 并带动生命线由转下 → 卖
卖点二: 生命线已形成下降角度后, 价格第一次反弹到生命线附近 → 再卖
"""
import sys, sqlite3, datetime as dt
import numpy as np
import pandas as pd

sys.path.insert(0, r"d:/个人项目代码/supertrendMain/backend")
from indicators import super_trend, ta_atr, ta_ema, ta_sma

DB = r"d:/个人项目代码/supertrendMain/backend/candle_data.db"
SYMBOL, TF = "BTC-USDT", "15m"
INIT_CAP = 10000.0
TAKER_FEE, SLIP = 0.0005, 0.0003
MA_N, ST_P, ST_M = 30, 10, 3.0
ATR_N = 14
# ── 四大买卖点的判定参数 ──
TOL_ATR = 0.5     # "回调到生命线附近": |C-MA30| <= TOL_ATR * ATR
SEP_ATR = 1.0     # 此前必须离开生命线 >= SEP_ATR * ATR, 才算"回调/反弹"(而不是没走开过)
ANGLE_BARS = 5    # 生命线至少连续 ANGLE_BARS 根同向, 才算"形成角度"
W = 6             # "突破"与"扭转"两次事件需落在 W 根之内
MIN_GAP = 6       # 同一买点的最小间隔(根)

TP_LIST = [0.02, 0.03, 0.04, 0.05, 0.06, 0.08]
SL_LIST = [0.01, 0.015, 0.02, 0.025, 0.03]
TF_LIST = ["15m", "1h", "4h"]

# ── 美盘时段 (ET) ──
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


# ── 数据 ──
def load(symbol, tf):
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT ts,o,h,l,c,vol FROM candles WHERE symbol=? AND tf=? ORDER BY ts",
        (symbol, tf)).fetchall()
    con.close()
    return [{"ts": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "vol": r[5]}
            for r in rows]


# ── 信号构建 ──
def build_signals(base, ma_type="EMA"):
    o = [x["o"] for x in base]; h = [x["h"] for x in base]
    l = [x["l"] for x in base]; c = [x["c"] for x in base]; ts = [x["ts"] for x in base]
    ma = ta_ema(c, MA_N) if ma_type == "EMA" else ta_sma(c, MA_N)
    atr = ta_atr(h, l, c, ATR_N)
    st = super_trend(o, h, l, c, periods=ST_P, multiplier=ST_M, change_atr=True)
    trend = st["trend"]
    n = len(c)
    sigs = []
    last_cu = last_cd = last_tu = last_td = -10 ** 9
    last_b1 = last_b2 = last_s1 = last_s2 = -10 ** 9
    up_sep = dn_sep = 0.0
    rise_run = fall_run = 0
    b2_avail = s2_avail = False

    for i in range(2, n):
        if None in (ma[i], ma[i - 1], ma[i - 2], atr[i]) or atr[i] == 0:
            continue
        slp = ma[i] - ma[i - 1]
        pslp = ma[i - 1] - ma[i - 2]
        cross_up = c[i - 1] <= ma[i - 1] and c[i] > ma[i]
        cross_dn = c[i - 1] >= ma[i - 1] and c[i] < ma[i]
        turn_up = slp > 0 and pslp <= 0
        turn_dn = slp < 0 and pslp >= 0
        rise_run = rise_run + 1 if slp > 0 else 0
        fall_run = fall_run + 1 if slp < 0 else 0
        if cross_up:
            last_cu = i
        if cross_dn:
            last_cd = i
        if turn_up:
            last_tu = i; b2_avail = True; up_sep = 0.0
        if turn_dn:
            last_td = i; s2_avail = True; dn_sep = 0.0

        dist = (c[i] - ma[i]) / atr[i]          # >0 价格在生命线上方
        if slp > 0:
            up_sep = max(up_sep, dist)
        if slp < 0:
            dn_sep = max(dn_sep, -dist)

        st_d = trend[i] if trend[i] is not None else 0
        sess = in_session(ts[i])
        base_sig = {"ts": ts[i], "st": st_d, "sess": sess}

        # 买点一: 突破 + 扭转(两事件同处一个短窗口), 且当前站上生命线
        if (i - last_cu <= W and i - last_tu <= W and slp > 0 and c[i] > ma[i]
                and i - last_b1 > MIN_GAP):
            sigs.append({"i": i, "dir": 1, "kind": "B1", **base_sig})
            last_b1 = i; b2_avail = True; up_sep = 0.0
        # 买点二: 生命线已成角度, 曾远离, 现首次回到生命线附近
        elif (b2_avail and rise_run >= ANGLE_BARS and slp > 0
              and up_sep >= SEP_ATR and abs(dist) <= TOL_ATR
              and i - last_b2 > MIN_GAP):
            sigs.append({"i": i, "dir": 1, "kind": "B2", **base_sig})
            last_b2 = i; b2_avail = False

        # 卖点一
        if (i - last_cd <= W and i - last_td <= W and slp < 0 and c[i] < ma[i]
                and i - last_s1 > MIN_GAP):
            sigs.append({"i": i, "dir": -1, "kind": "S1", **base_sig})
            last_s1 = i; s2_avail = True; dn_sep = 0.0
        # 卖点二
        elif (s2_avail and fall_run >= ANGLE_BARS and slp < 0
              and dn_sep >= SEP_ATR and abs(dist) <= TOL_ATR
              and i - last_s2 > MIN_GAP):
            sigs.append({"i": i, "dir": -1, "kind": "S2", **base_sig})
            last_s2 = i; s2_avail = False

    return sigs, o, h, l, c, ts


def filt(sigs, st_mode, sess_mode):
    out = []
    for s in sigs:
        if st_mode == "agree" and s["st"] != s["dir"]:
            continue
        if sess_mode == "us" and not s["sess"]:
            continue
        out.append(s)
    return out


# ── 回测引擎 ──
def backtest(sig_list, c, h, l, tp=None, sl=None, long_only=False,
             reverse_open=True, lo=0, hi=None, exit_list=None):
    """sig_list = 入场信号表; exit_list = 出场(平仓)信号表, 默认与入场同一份。
    两者分离后才能检验: 过滤条件是作用在入场, 还是也作用在出场。"""
    hi = len(c) if hi is None else hi
    sig_map = {}
    for s in sig_list:
        if lo <= s["i"] < hi:
            sig_map[s["i"]] = s
    if exit_list is None:
        exit_map = sig_map
    else:
        exit_map = {}
        for s in exit_list:
            if lo <= s["i"] < hi:
                exit_map[s["i"]] = s
    cash = INIT_CAP; pos = 0; units = 0.0; entry = 0.0; entry_i = -1
    stop = limit = None
    equity = [INIT_CAP]; trades = []
    equity_at_entry = INIT_CAP   # 开仓前的权益, 用于把入场手续费也计入本笔盈亏

    def open_pos(px, d, i):
        nonlocal cash, pos, units, entry, stop, limit, entry_i, equity_at_entry
        equity_at_entry = cash
        fill = px * (1 + SLIP) if d == 1 else px * (1 - SLIP)
        u = cash / fill
        cash -= u * fill * TAKER_FEE
        pos = d; units = u; entry = fill; entry_i = i
        stop = None if sl is None else (entry * (1 - sl) if d == 1 else entry * (1 + sl))
        limit = None if tp is None else (entry * (1 + tp) if d == 1 else entry * (1 - tp))

    def close_pos(px, i, reason):
        nonlocal cash, pos, units, entry_i
        fill = px * (1 - SLIP) if pos == 1 else px * (1 + SLIP)
        fee = units * fill * TAKER_FEE
        pnl = units * (fill - entry) if pos == 1 else units * (entry - fill)
        cash += pnl - fee
        # 真实单笔盈亏 = 平仓后现金 - 开仓前权益 (含双边手续费与滑点)
        trades.append({"side": pos, "pnl": cash - equity_at_entry, "entry_i": entry_i,
                       "exit_i": i, "reason": reason})
        pos = 0; units = 0.0; entry_i = -1

    for i in range(lo, hi):
        closed = False
        if pos != 0 and i > entry_i and stop is not None:
            if pos == 1:
                if l[i] <= stop:
                    close_pos(stop, i, "tp/sl"); closed = True
                elif h[i] >= limit:
                    close_pos(limit, i, "tp/sl"); closed = True
            else:
                if h[i] >= stop:
                    close_pos(stop, i, "tp/sl"); closed = True
                elif l[i] <= limit:
                    close_pos(limit, i, "tp/sl"); closed = True
        # 出场: 用 exit_map (可与入场不同)
        s_ex = exit_map.get(i)
        if s_ex is not None:
            ed = s_ex["dir"]
            if long_only and ed == -1:
                if pos == 1:
                    close_pos(c[i], i, "signal"); closed = True
            elif pos != 0 and ed != pos:
                close_pos(c[i], i, "signal"); closed = True
        # 入场: 只用 sig_map
        s = sig_map.get(i)
        if s is not None and not (long_only and s["dir"] == -1):
            if pos == 0 and (reverse_open or not closed):
                open_pos(c[i], s["dir"], i)
        equity.append(cash + (units * c[i] if pos != 0 else 0.0))

    if pos != 0:
        close_pos(c[hi - 1], hi - 1, "eod")
        equity.append(cash)

    nt = len(trades)
    # 对账: 所有单笔盈亏之和必须等于最终权益变动, 否则记账有漏项
    if nt and abs(sum(t["pnl"] for t in trades) - (equity[-1] - INIT_CAP)) > 1e-6:
        raise AssertionError(
            f"盈亏对账不符: Σtrade={sum(t['pnl'] for t in trades):.6f} "
            f"vs 权益变动={equity[-1] - INIT_CAP:.6f}")
    wins = [t for t in trades if t["pnl"] > 0]
    gross_p = sum(t["pnl"] for t in wins)
    gross_l = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    peak = equity[0]; max_dd = 0.0
    for e in equity:
        peak = max(peak, e)
        max_dd = min(max_dd, e / peak * 100 - 100)
    return {
        "total_ret": equity[-1] / INIT_CAP * 100 - 100,
        "max_dd": max_dd, "n": nt,
        "win_rate": (len(wins) / nt * 100) if nt else 0.0,
        "pf": (gross_p / gross_l) if gross_l > 0 else float("inf"),
        "trades": trades,
    }


def year_ranges(ts):
    yrs = {}
    for i, t in enumerate(ts):
        y = pd.to_datetime(t, unit="ms").year
        if y not in yrs:
            yrs[y] = [i, i + 1]
        yrs[y][1] = i + 1
    return yrs


def pfstr(p):
    return "inf" if p == float("inf") else f"{p:.2f}"


def run_tf(tf):
    base = load(SYMBOL, tf)
    print(f"\n########## 周期 {tf} ##########")
    print(f"数据 {SYMBOL} {tf}: {len(base)} 根  "
          f"{pd.to_datetime(base[0]['ts'], unit='ms')} → {pd.to_datetime(base[-1]['ts'], unit='ms')}")

    cache = {}
    rows = []
    for ma_type in ["EMA", "SMA"]:
        sigs, o, h, l, c, ts = build_signals(base, ma_type)
        cache[ma_type] = (sigs, o, h, l, c, ts)
        kinds = {"B1": 0, "B2": 0, "S1": 0, "S2": 0}
        for s in sigs:
            kinds[s["kind"]] += 1
        print(f"\n[{ma_type}{MA_N}] 信号总数 {len(sigs)}  买1 {kinds['B1']} 买2 {kinds['B2']} "
              f"卖1 {kinds['S1']} 卖2 {kinds['S2']}  (ST一致 {sum(1 for s in sigs if s['st'] == s['dir'])}, "
              f"美盘内 {sum(1 for s in sigs if s['sess'])})")

    yrs = year_ranges(cache["EMA"][5])
    test_years = [y for y in sorted(yrs) if y >= 2024]

    # ── Phase A: 纯规则出场(无TP/SL), 全组合对照 ──
    print("\n" + "=" * 100)
    print("  A) 纯规则出场(只用反向信号平仓, 无止盈止损) · 组合对照")
    print("=" * 100)
    print(f"  {'MA':<5}{'ST':<7}{'时段':<6}{'模式':<16}{'笔数':>6}{'胜率':>8}{'净收益':>10}{'回撤':>9}{'盈亏比':>8}")
    for ma_type in ["EMA", "SMA"]:
        sigs, o, h, l, c, ts = cache[ma_type]
        for st_mode in ["none", "agree"]:
            for sess_mode in ["none", "us"]:
                sfiltered = filt(sigs, st_mode, sess_mode)
                for mode, long_only, rev in [("多空双向", False, True),
                                             ("双向·不反手", False, False),
                                             ("只做多", True, True)]:
                    r = backtest(sfiltered, c, h, l, None, None, long_only, rev)
                    row = {"ma": ma_type, "st": st_mode, "sess": sess_mode, "mode": mode,
                           "long_only": long_only, "rev": rev, "ret": r["total_ret"],
                           "dd": r["max_dd"], "wr": r["win_rate"], "n": r["n"], "pf": r["pf"]}
                    row["years"] = {y: backtest(sfiltered, c, h, l, None, None, long_only, rev,
                                               yrs[y][0], yrs[y][1])["total_ret"]
                                    for y in test_years}
                    rows.append(row)
    rows.sort(key=lambda x: x["ret"], reverse=True)
    for r in rows:
        print(f"  {r['ma']:<5}{r['st']:<7}{r['sess']:<6}{r['mode']:<16}{r['n']:>6}"
              f"{r['wr']:>7.1f}%{r['ret']:>9.2f}%{r['dd']:>8.2f}%{pfstr(r['pf']):>8}")

    print("\n  逐年净收益(最优前 8 组):")
    print("  " + f"{'MA':<5}{'ST':<7}{'时段':<6}{'模式':<16}"
          + "".join(f"{y:>10}" for y in test_years))
    for r in rows[:8]:
        print(f"  {r['ma']:<5}{r['st']:<7}{r['sess']:<6}{r['mode']:<16}"
              + "".join(f"{r['years'][y]:>9.2f}%" for y in test_years))

    # ── Phase B: 最优前 3 组做 TP/SL 寻优 ──
    best_grid = None
    print("\n" + "=" * 100)
    print("  B) 前 3 组各做固定TP/SL寻优")
    print("=" * 100)
    top3 = rows[:1]
    for r in top3:
        sigs, o, h, l, c, ts = cache[r["ma"]]
        sf = filt(sigs, r["st"], r["sess"])
        label = f"{r['ma']}{MA_N} ST={r['st']} 时段={r['sess']} {r['mode']}"
        gr = []
        for tp in TP_LIST:
            for sl in SL_LIST:
                if sl >= tp:
                    continue
                rr = backtest(sf, c, h, l, tp, sl, r["long_only"], r["rev"])
                gr.append((tp, sl, rr))
        gr.sort(key=lambda x: x[2]["total_ret"], reverse=True)
        print(f"\n  --- {label} ---")
        print(f"    {'TP':>6} {'SL':>6} {'净收益':>9} {'回撤':>8} {'胜率':>7} {'笔数':>6} {'盈亏比':>8}")
        for tp, sl, rr in gr[:5]:
            print(f"    {tp*100:5.2f}% {sl*100:5.2f}% {rr['total_ret']:8.2f}% {rr['max_dd']:7.2f}% "
                  f"{rr['win_rate']:6.1f}% {rr['n']:6d} {pfstr(rr['pf']):>8}")
        cand = (gr[0][0], gr[0][1], gr[0][2]["total_ret"], r)
        if best_grid is None or cand[2] > best_grid[2]:
            best_grid = cand

    # ── Phase C: walk-forward(前段寻参→次年样本外) ──
    print("\n" + "=" * 100)
    print("  C) 滚动 WALK-FORWARD (训练段寻参 → 下一年样本外)")
    print("=" * 100)
    btp, bsl, _, br = best_grid
    sigs, o, h, l, c, ts = cache[br["ma"]]
    sf = filt(sigs, br["st"], br["sess"])
    print(f"  配置: {br['ma']}{MA_N} ST={br['st']} 时段={br['sess']} {br['mode']}")
    print(f"  {'测试年':<8}{'寻参TP/SL':>12}{'训练净':>10}{'样本外净':>10}{'外回撤':>9}{'外胜率':>8}{'外笔数':>8}")
    for y in test_years:
        lo, hi = yrs[y]
        best = None
        for tp in TP_LIST:
            for sl in SL_LIST:
                if sl >= tp:
                    continue
                rr = backtest(sf, c, h, l, tp, sl, br["long_only"], br["rev"], 0, lo)
                if best is None or rr["total_ret"] > best[2]["total_ret"]:
                    best = (tp, sl, rr)
        rte = backtest(sf, c, h, l, best[0], best[1], br["long_only"], br["rev"], lo, hi)
        print(f"  {y:<8}{best[0]*100:5.1f}/{best[1]*100:4.1f}%{best[2]['total_ret']:>9.2f}%"
              f"{rte['total_ret']:>9.2f}%{rte['max_dd']:>8.2f}%{rte['win_rate']:>7.1f}%{rte['n']:>8d}")

    # 固定参数跨年
    print(f"\n  固定参数跨年样本外(配置同上):")
    for ftp, fsl in [(0.06, 0.015), (btp, bsl)]:
        parts = []
        for y in test_years:
            lo, hi = yrs[y]
            rr = backtest(sf, c, h, l, ftp, fsl, br["long_only"], br["rev"], lo, hi)
            parts.append(f"{y}: {rr['total_ret']:7.2f}% (回撤{rr['max_dd']:6.2f}% 笔{rr['n']:3d} 胜{rr['win_rate']:4.1f}%)")
        print(f"    TP{ftp*100:.1f}%/SL{fsl*100:.1f}%  " + " | ".join(parts))

    return {"tf": tf, "rows": rows, "yrs": yrs, "test_years": test_years,
            "best_grid": best_grid}


def zero_cost(fn):
    """临时去掉手续费+滑点, 用来隔离'毛利有无正期望'"""
    global TAKER_FEE, SLIP
    tf_, sl_ = TAKER_FEE, SLIP
    TAKER_FEE, SLIP = 0.0, 0.0
    try:
        return fn()
    finally:
        TAKER_FEE, SLIP = tf_, sl_


def main():
    results = []
    for tf in TF_LIST:
        results.append(run_tf(tf))

    # ── 全局横向: 所有周期的所有组合 ──
    print("\n" + "#" * 100)
    print("  总表: 全部周期 × 全部组合 (纯规则出场, 无TP/SL)")
    print("#" * 100)
    g = []
    for res in results:
        for r in res["rows"]:
            g.append((res["tf"], r))
    g.sort(key=lambda x: x[1]["ret"], reverse=True)
    print(f"  {'周期':<6}{'MA':<5}{'ST':<7}{'时段':<6}{'模式':<16}{'笔数':>6}{'胜率':>8}"
          f"{'净收益':>10}{'回撤':>9}{'盈亏比':>8}")
    for tf, r in g:
        print(f"  {tf:<6}{r['ma']:<5}{r['st']:<7}{r['sess']:<6}{r['mode']:<16}{r['n']:>6}"
              f"{r['wr']:>7.1f}%{r['ret']:>9.2f}%{r['dd']:>8.2f}%{pfstr(r['pf']):>8}")

    # ── 手续费隔离: 最优组合在"零成本"下还有没有正期望 ──
    print("\n" + "#" * 100)
    print("  诊断: 手续费/信号密度 是否才是杀手 (最优组合零成本对照)")
    print("#" * 100)
    print(f"  {'周期':<6}{'配置':<40}{'笔数':>6}{'含费净收益':>12}{'零费净收益':>12}{'每年笔数':>10}")
    for res in results:
        for r in res["rows"][:2]:
            tf = res["tf"]
            base = load(SYMBOL, tf)
            sigs, o, h, l, c, ts = build_signals(base, r["ma"])
            sf = filt(sigs, r["st"], r["sess"])
            net = backtest(sf, c, h, l, None, None, r["long_only"], r["rev"])
            gross = zero_cost(lambda: backtest(sf, c, h, l, None, None,
                                               r["long_only"], r["rev"]))
            span_y = (base[-1]["ts"] - base[0]["ts"]) / (365.25 * 24 * 3600 * 1000)
            conf = f"{r['ma']}{MA_N} ST={r['st']} 时段={r['sess']} {r['mode']}"
            print(f"  {tf:<6}{conf:<40}{net['n']:>6}{net['total_ret']:>11.2f}%"
                  f"{gross['total_ret']:>11.2f}%{net['n']/span_y:>10.0f}")
            yr = year_ranges(ts)
            ys = "  ".join(
                f"{y}:{backtest(sf, c, h, l, None, None, r['long_only'], r['rev'], yr[y][0], yr[y][1])['total_ret']:>7.2f}%"
                for y in sorted(yr))
            print(f"        逐年净收益 {ys}")


if __name__ == "__main__":
    main()
