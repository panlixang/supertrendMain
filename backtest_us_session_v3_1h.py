# -*- coding: utf-8 -*-
"""
BTC 1h  +  形态识别页 V3 趋势过滤  +  仅美盘开盘(美东09:30-16:00)开单  -> 回测收益

两套出场, 同一组信号(ST翻转 + V3通过 + 美盘):
  A) 固定%TP/SL + 反向SuperTrend翻转平仓(不反手)  —— 与你之前1h版一致, 便于A/B
  B) 形态识别页原生出场(TP1 1.5%平70% + 保本 + 跟ST轨道 + 2%兜底 + 反向翻转平剩余)
均用库数据: BTC-USDT 1h + 4h (不再走 OKX 网络).
"""
import sys, sqlite3, datetime as dt
sys.path.insert(0, r"d:/个人项目代码/supertrendMain/backend")
from indicators import super_trend
from signal_v3 import features_from_candles, v3_decide
import numpy as np
import pandas as pd

SYMBOL   = "BTC-USDT"
TF, HTF  = "1h", "4h"
DB       = r"d:/个人项目代码/supertrendMain/backend/candle_data.db"
ST_P, ST_M = 10, 3.0
TAKER_FEE = 0.0005
SLIP      = 0.0003
INIT_CAP  = 10000.0
SESSION_START_MIN = 9 * 60 + 30
SESSION_END_MIN   = 16 * 60
UTC = dt.timezone.utc

# ── 美东时间(DST自算) ──
def nth_weekday(y, m, wd, n):
    d = dt.date(y, m, 1); off = (wd - d.weekday()) % 7
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
    c = sqlite3.connect(DB)
    rows = c.execute("SELECT ts,o,h,l,c,vol FROM candles WHERE symbol=? AND tf=? ORDER BY ts",
                    (symbol, tf)).fetchall()
    c.close()
    return [{"ts": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "vol": r[5]} for r in rows]

# ── 信号: ST翻转 + V3过滤 + 美盘 ──
def build_signals(base, h4):
    o = [x["o"] for x in base]; h = [x["h"] for x in base]
    l = [x["l"] for x in base]; c = [x["c"] for x in base]; ts = [x["ts"] for x in base]
    st = super_trend(o, h, l, c, periods=ST_P, multiplier=ST_M, change_atr=True)
    h4c = [{"ts": x["ts"], "o": x["o"], "h": x["h"], "l": x["l"], "c": x["c"], "vol": x["vol"]} for x in h4]
    flips = st["flips"]
    flip_dir = {f["i"]: (1 if f["type"] == "buy" else -1) for f in flips}
    sigs = []          # 全部翻转(带V3结果)
    for f in flips:
        i = f["i"]
        if i >= len(base) or i < 50:
            continue
        sd = 1 if f["type"] == "buy" else -1
        feats = features_from_candles(base, i, sd, h4c, st_periods=ST_P, st_mult=ST_M)
        if not feats:
            continue
        v3 = v3_decide(sd, feats)
        sigs.append({"i": i, "ts": ts[i], "dir": sd, "type": f["type"],
                     "v3_execute": v3["execute"], "v3_path": v3["path"],
                     "v3_score": v3["score"], "in_sess": in_session(ts[i])})
    return sigs, o, h, l, c, ts, flip_dir

# ── A) 固定TP/SL + 反向翻转平仓(不反手), 复利满仓 ──
def bt_fixed(sigs, c, h, l, flip_dir, tp, sl):
    n = len(c)
    sig_map = {s["i"]: s["dir"] for s in sigs}
    flip_list = sorted(flip_dir.keys())
    fi = 0
    cash = INIT_CAP; pos = 0; units = 0.0; entry = 0.0; stop = 0.0; limit = 0.0; entry_i = -1
    equity = [INIT_CAP]; trades = []

    def open_pos(price, d, i):
        nonlocal cash, pos, units, entry, stop, limit, entry_i
        fill = price * (1 + SLIP) if d == 1 else price * (1 - SLIP)
        u = cash / fill; fee = u * fill * TAKER_FEE; cash -= fee
        pos = d; units = u; entry = fill; entry_i = i
        stop = entry * (1 - sl) if d == 1 else entry * (1 + sl)
        limit = entry * (1 + tp) if d == 1 else entry * (1 - tp)
    def close_pos(price, i, taker):
        nonlocal cash, pos, units, entry_i
        fill = price * (1 - SLIP) if pos == 1 else price * (1 + SLIP)
        notional = units * fill; fee = notional * TAKER_FEE
        pnl = units * (fill - entry) if pos == 1 else units * (entry - fill)
        cash += pnl - fee
        trades.append({"side": pos, "pnl": pnl - fee, "entry_i": entry_i, "exit_i": i,
                       "reason": "taker" if taker else "tp/sl"})
        pos = 0; units = 0.0; entry_i = -1

    for i in range(n):
        closed_this_bar = False
        if pos != 0 and i > entry_i:
            if pos == 1:
                if l[i] <= stop: close_pos(stop, i, False)
                elif h[i] >= limit: close_pos(limit, i, False)
            else:
                if h[i] >= stop: close_pos(stop, i, False)
                elif l[i] <= limit: close_pos(limit, i, False)
        while fi < len(flip_list) and flip_list[fi] <= i:
            j = flip_list[fi]
            if pos != 0 and flip_dir[j] == -pos:
                close_pos(c[j], j, True); closed_this_bar = True
            fi += 1
        if i in sig_map:
            d = sig_map[i]
            if pos == 0 and not closed_this_bar:
                open_pos(c[i], d, i)
            elif pos != 0 and flip_dir[i] == -pos:
                close_pos(c[i], i, True)
        if pos == 1: eq = cash + units * (c[i] - entry)
        elif pos == -1: eq = cash + units * (entry - c[i])
        else: eq = cash
        equity.append(eq)
    if pos != 0:
        close_pos(c[-1], n - 1, True)
    equity = np.array(equity)
    peak = np.maximum.accumulate(equity); dd = (equity - peak) / peak * 100
    done = [t for t in trades if t["pnl"] is not None]
    nt = len(done); wins = [t for t in done if t["pnl"] > 0]; losses = [t for t in done if t["pnl"] < 0]
    wr = len(wins) / nt * 100 if nt else 0
    gw = sum(t["pnl"] for t in wins); gl = -sum(t["pnl"] for t in losses)
    pf = gw / gl if gl > 0 else float("inf")
    return {"total_ret": equity[-1] / INIT_CAP * 100 - 100, "max_dd": dd.min(),
            "n": nt, "win_rate": wr, "pf": pf}

# ── B) 形态识别页原生出场 (复用 bt_pattern_page.backtest) ──
def bt_page(sigs, o, h, l, c, st):
    from bt_pattern_page import backtest as page_bt, metrics as page_m
    up = st["up_plot"]; dn = st["dn_plot"]
    flip_idx = {f["i"] for f in st["flips"] if f["i"] < len(c)}
    tr = page_bt(sigs, h, l, c, up, dn, flip_idx, reverse_close=False)
    return page_m(tr), tr

# ── 网格 ──
TP_LIST = [0.02, 0.03, 0.04, 0.05, 0.06, 0.08]
SL_LIST = [0.01, 0.015, 0.02, 0.025, 0.03, 0.04]

def grid_fixed(sigs, c, h, l, flip_dir, label):
    rows = []
    for tp in TP_LIST:
        for sl in SL_LIST:
            if sl >= tp: continue
            r = bt_fixed(sigs, c, h, l, flip_dir, tp, sl)
            rows.append((tp, sl, r))
    rows.sort(key=lambda x: x[2]["total_ret"], reverse=True)
    print(f"\n{'='*78}\n  {label}  固定TP/SL 共 {len(rows)} 组\n{'='*78}")
    print(f"  {'TP':>6} {'SL':>6} {'净收益':>9} {'回撤':>8} {'胜率':>7} {'笔数':>6} {'盈亏比':>8}")
    for tp, sl, r in rows[:10]:
        pf = f"{r['pf']:.2f}" if r['pf'] != float("inf") else "inf"
        print(f"  {tp*100:5.2f}% {sl*100:5.2f}% {r['total_ret']:8.2f}% {r['max_dd']:7.2f}% "
              f"{r['win_rate']:6.1f}% {r['n']:6d} {pf:>8}")
    return rows

# ── 子集切分(按 ms 时间窗) ──
def make_window(sigs_all, c, h, l, flip_dir, ts, start_ts, end_ts):
    arr = np.array(ts)
    mask = (arr >= start_ts) & (arr < end_ts)
    if mask.sum() == 0:
        return [], [], c, h, l, flip_dir
    start_i = int(np.argmax(mask))
    c_sub = c[start_i:]; h_sub = h[start_i:]; l_sub = l[start_i:]
    flip_sub = {i - start_i: d for i, d in flip_dir.items()
                if start_ts <= ts[i] < end_ts}
    def remap(ss):
        return [{"i": s["i"] - start_i, "ts": s["ts"], "dir": s["dir"]}
                for s in ss if start_ts <= s["ts"] < end_ts]
    raw = remap([s for s in sigs_all if s["in_sess"]])
    v3 = remap([s for s in sigs_all if s["v3_execute"] and s["in_sess"]])
    return raw, v3, c_sub, h_sub, l_sub, flip_sub

# ── 滚动 walk-forward ──
def walk_forward(sigs_all, c, h, l, flip_dir, ts):
    data_start = int(ts[0]); end_all = int(ts[-1]) + 1
    folds = [
        ("Train 22-23 / Test 2024", "2024-01-01", "2025-01-01"),
        ("Train 22-24 / Test 2025", "2025-01-01", "2026-01-01"),
        ("Train 22-25 / Test 2026", "2026-01-01", None),
    ]
    print("\n" + "=" * 78 +
          "\n  滚动 WALK-FORWARD (仅美盘; 训练段寻参→下一年样本外; 固定TP/SL复利)\n" + "=" * 78)
    print(f"  {'窗口':<30}{'策略':<12}{'寻参TP/SL':>10}{'训练净':>9}{'样本外净':>10}{'外回撤':>9}{'外胜率':>7}")
    for name, te_train, te_test in folds:
        train_end = int(pd.Timestamp(te_train, tz="UTC").timestamp() * 1000)
        test_end = end_all if te_test is None else int(pd.Timestamp(te_test, tz="UTC").timestamp() * 1000)
        raw_tr, v3_tr, c_tr, h_tr, l_tr, fl_tr = make_window(sigs_all, c, h, l, flip_dir, ts, data_start, train_end)
        raw_te, v3_te, c_te, h_te, l_te, fl_te = make_window(sigs_all, c, h, l, flip_dir, ts, train_end, test_end)
        for tag, tr_s, te_s in [("无V3", raw_tr, raw_te), ("V3", v3_tr, v3_te)]:
            best = None
            for tp in TP_LIST:
                for sl in SL_LIST:
                    if sl >= tp:
                        continue
                    r = bt_fixed(tr_s, c_tr, h_tr, l_tr, fl_tr, tp, sl)
                    if best is None or r["total_ret"] > best[2]["total_ret"]:
                        best = (tp, sl, r)
            bt, bs, rtr = best
            rte = bt_fixed(te_s, c_te, h_te, l_te, fl_te, bt, bs)
            print(f"  {name:<30}{tag:<12}{bt*100:4.1f}/{bs*100:4.1f}%{rtr['total_ret']:8.2f}%"
                  f"{rte['total_ret']:9.2f}%{rte['max_dd']:8.2f}%{rte['win_rate']:6.1f}%")
    # 固定参数跨年(稳健性)
    print("  固定参数跨年样本外(稳健性, 训练段不参与寻参):")
    for ftp, fsl in [(0.06, 0.015), (0.08, 0.015)]:
        line = f"    TP{ftp*100:.0f}%/SL{fsl*100:.1f}%: "
        parts = []
        for name, te_train, te_test in folds:
            train_end = int(pd.Timestamp(te_train, tz="UTC").timestamp() * 1000)
            test_end = end_all if te_test is None else int(pd.Timestamp(te_test, tz="UTC").timestamp() * 1000)
            raw_te, v3_te, c_te, h_te, l_te, fl_te = make_window(sigs_all, c, h, l, flip_dir, ts, train_end, test_end)
            r_no = bt_fixed(raw_te, c_te, h_te, l_te, fl_te, ftp, fsl)
            r_v3 = bt_fixed(v3_te, c_te, h_te, l_te, fl_te, ftp, fsl)
            parts.append(f"{name.split('/')[-1].strip():>6} 无V3 {r_no['total_ret']:7.2f}% / V3 {r_v3['total_ret']:7.2f}%")
        print(line + "  ".join(parts))

def main():
    base = load(SYMBOL, TF)
    h4 = load(SYMBOL, HTF)
    print(f"加载 {SYMBOL}: 1h {len(base)} 根 (至 {pd.to_datetime(base[-1]['ts'],unit='ms')}), "
          f"4h {len(h4)} 根")
    sigs, o, h, l, c, ts, flip_dir = build_signals(base, h4)
    st = super_trend(o, h, l, c, periods=ST_P, multiplier=ST_M, change_atr=True)
    print(f"ST翻转总数 {len([f for f in sigs])}  (V3通过 {sum(1 for s in sigs if s['v3_execute'])}  "
          f"美盘内 {sum(1 for s in sigs if s['in_sess'])}  V3∩美盘 {sum(1 for s in sigs if s['v3_execute'] and s['in_sess'])})")

    raw_sess = [s for s in sigs if s["in_sess"]]
    v3_sess = [s for s in sigs if s["v3_execute"] and s["in_sess"]]

    # ── A) 固定TP/SL, 全段 ──
    print("\n########## A) 固定TP/SL + 反向翻转平仓(不反手) ##########")
    gr_raw = grid_fixed(raw_sess, c, h, l, flip_dir, "仅美盘(无V3) · 全段 2022-2026")
    gr_v3 = grid_fixed(v3_sess, c, h, l, flip_dir, "V3∩美盘 · 全段 2022-2026")

    # 2026 子集
    cut = int(pd.Timestamp("2026-01-01", tz="UTC").timestamp() * 1000)
    start_i = int(np.argmax((np.array(ts) >= cut)))
    c26 = c[start_i:]; h26 = h[start_i:]; l26 = l[start_i:]
    flip26 = {i - start_i: d for i, d in flip_dir.items() if i >= start_i}
    def remap(ss):
        return [{"i": s["i"] - start_i, "ts": s["ts"], "dir": s["dir"]}
                for s in ss if s["i"] >= start_i]
    raw26 = remap(raw_sess); v326 = remap(v3_sess)
    grid_fixed(raw26, c26, h26, l26, flip26, "仅美盘(无V3) · 2026")
    grid_fixed(v326, c26, h26, l26, flip26, "V3∩美盘 · 2026")

    # A/B 对照(最优参数 TP6/SL1.5)
    print(f"\n{'='*78}\n  A/B 对照 (TP6%/SL1.5%, 固定TP/SL, 复利满仓)\n{'='*78}")
    for lab, ss, arr, fl in [("仅美盘(无V3)", raw_sess, c, flip_dir),
                             ("V3∩美盘", v3_sess, c, flip_dir),
                             ("仅美盘(无V3)·2026", raw26, c26, flip26),
                             ("V3∩美盘·2026", v326, c26, flip26)]:
        r = bt_fixed(ss, arr, (h26 if "2026" in lab else h),
                     (l26 if "2026" in lab else l), fl, 0.06, 0.015)
        print(f"    [{lab}] 净收益 {r['total_ret']:.2f}%  回撤 {r['max_dd']:.2f}%  "
              f"胜率 {r['win_rate']:.1f}%  笔数 {r['n']}  盈亏比 {r['pf']:.2f}")

    # ── 滚动 walk-forward (确认 V3 提升非过拟合) ──
    walk_forward(sigs, c, h, l, flip_dir, ts)

    # ── B) 形态识别页原生出场 ──
    print(f"\n########## B) 形态识别页原生出场 (TP1 1.5%平70%+保本+跟ST+反向翻转) ##########")
    m_raw, _ = bt_page(raw_sess, o, h, l, c, st)
    m_v3, _ = bt_page(v3_sess, o, h, l, c, st)
    print(f"\n  {'策略':<22}{'笔数':>6}{'胜率':>8}{'收益%':>9}{'盈亏比':>8}{'最大回撤%':>11}{'最长连亏':>9}")
    for lab, m in [("仅美盘(无V3)", m_raw), ("V3∩美盘", m_v3)]:
        po = "inf" if m["payoff"] == float("inf") else f"{m['payoff']:.2f}"
        print(f"  {lab:<22}{m['n']:>6}{m['wr']:>7.1f}%{m['ret']:>9.2f}{po:>8}{m['max_dd']:>11.2f}{m['streak']:>9}")

if __name__ == "__main__":
    main()
