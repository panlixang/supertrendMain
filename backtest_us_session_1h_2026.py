# -*- coding: utf-8 -*-
"""
BTC 1h  SuperTrend(10,3)  + 仅美盘开盘时间(美东 09:30-16:00)开单  + 固定%TP/SL 寻优
出场逻辑(用户选定版):
  - 固定百分比止盈(TP) / 止损(SL)
  - 反向 SuperTrend 翻转 -> 平仓, 不反手(平后空仓, 等下一个 flip)
与之前 15m 版同一口径: 信号=indicators.super_trend(10,3,hl2,changeATR);
  费率=TAKER 0.0005 + 滑点 0.0003/边; 满仓单仓(equity/entry).
同时给出 2026 子集结果, 以及与"无时段过滤"对照。
"""
import sys, sqlite3, datetime as dt
sys.path.insert(0, r"d:/个人项目代码/supertrendMain/backend")
from indicators import super_trend
import numpy as np
import pandas as pd

# ============== 参数 ==============
SYMBOL   = "BTC-USDT"
TF       = "1h"            # DB 里 2022+ 的那份 (41451 根)
DB       = r"d:/个人项目代码/supertrendMain/backend/candle_data.db"
PERIODS  = 10
MULT     = 3.0
SRC      = "hl2"
CHANGE_ATR = True
TAKER_FEE = 0.0005
SLIP      = 0.0003
INIT_CAP  = 10000.0
# 美东 09:30 - 16:00
SESSION_START_MIN = 9 * 60 + 30
SESSION_END_MIN   = 16 * 60
UTC = dt.timezone.utc

# ============== 美东时间(自带 DST 规则, 不依赖 tzdata) ==============
def nth_weekday(y, m, wd, n):
    d = dt.date(y, m, 1)
    off = (wd - d.weekday()) % 7
    return dt.date(y, m, 1 + off + (n - 1) * 7)

def is_dst(t_utc):
    y = t_utc.year
    ds = dt.datetime.combine(nth_weekday(y, 3, 6, 2), dt.time(7, 0), tzinfo=UTC)   # 美东 02:00 -> 07:00 UTC
    de = dt.datetime.combine(nth_weekday(y, 11, 6, 1), dt.time(6, 0), tzinfo=UTC)  # 美东 02:00 -> 06:00 UTC
    return ds <= t_utc < de

def et_minute(open_ms):
    t_utc = dt.datetime.fromtimestamp(open_ms / 1000, tz=UTC)
    off = -4 * 60 if is_dst(t_utc) else -5 * 60   # ET 相对 UTC 分钟
    t_et = t_utc + dt.timedelta(minutes=off)
    return t_et.hour * 60 + t_et.minute

def in_session(open_ms):
    m = et_minute(open_ms)
    return SESSION_START_MIN <= m < SESSION_END_MIN

def load(symbol, tf):
    c = sqlite3.connect(DB)
    rows = c.execute(
        "SELECT ts,o,h,l,c,vol FROM candles WHERE symbol=? AND tf=? ORDER BY ts",
        (symbol, tf)).fetchall()
    c.close()
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "vol"])
    return df

# ============== 回测引擎 ==============
def backtest(df, flips, session_only, tp, sl, return_trades=False):
    o = df["open"].values.astype(float)
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    cl = df["close"].values.astype(float)
    ts = df["ts"].values.astype("int64")
    n = len(cl)
    # 标记每个 flip 所在的 bar
    flip_dir = {}      # i -> +1(buy) / -1(sell)
    flip_in_sess = {}
    for f in flips:
        i = f["i"]; d = 1 if f["type"] == "buy" else -1
        flip_dir[i] = d
        flip_in_sess[i] = in_session(ts[i])

    cash = INIT_CAP
    pos = 0; units = 0.0; entry = 0.0; stop = 0.0; limit = 0.0; entry_i = -1
    equity = [INIT_CAP]
    trades = []

    def open_pos(price, d, i):
        nonlocal cash, pos, units, entry, stop, limit, entry_i
        fill = price * (1 + SLIP) if d == 1 else price * (1 - SLIP)
        u = cash / fill
        fee = u * fill * TAKER_FEE
        cash -= fee
        pos = d; units = u; entry = fill; entry_i = i
        if d == 1:
            stop = entry * (1 - sl); limit = entry * (1 + tp)
        else:
            stop = entry * (1 + sl); limit = entry * (1 - tp)

    def close_pos(price, i, is_taker):
        nonlocal cash, pos, units, entry_i
        fill = price * (1 - SLIP) if pos == 1 else price * (1 + SLIP)
        notional = units * fill
        fee = notional * TAKER_FEE
        pnl = units * (fill - entry) if pos == 1 else units * (entry - fill)
        cash += pnl - fee
        trades.append({"side": pos, "pnl": pnl - fee,
                       "entry_i": entry_i, "exit_i": i,
                       "entry": entry, "exit": fill,
                       "reason": "taker" if is_taker else "tp/sl"})
        pos = 0; units = 0.0; entry_i = -1

    for i in range(n):
        # 1) TP/SL 检查(本根 high/low, 不与开仓同根)
        if pos != 0 and i > entry_i:
            if pos == 1:
                if l[i] <= stop:
                    close_pos(stop, i, False); 
                elif h[i] >= limit:
                    close_pos(limit, i, False)
            else:
                if h[i] >= stop:
                    close_pos(stop, i, False)
                elif l[i] <= limit:
                    close_pos(limit, i, False)
        # 2) 信号(flip 在收盘产生)
        if i in flip_dir:
            d = flip_dir[i]
            if pos == 0:
                if (not session_only) or flip_in_sess[i]:
                    open_pos(cl[i], d, i)
            elif pos == -d:
                # 反向翻转 -> 平, 不反手
                close_pos(cl[i], i, True)
            # pos == d 不会出现(flip 交替)
        # 3) 权益
        if pos == 1:
            eq = cash + units * (cl[i] - entry)
        elif pos == -1:
            eq = cash + units * (entry - cl[i])
        else:
            eq = cash
        equity.append(eq)

    if pos != 0:
        close_pos(cl[-1], n - 1, True)

    equity = np.array(equity)
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak * 100
    max_dd = dd.min()
    done = [t for t in trades if t["pnl"] is not None]
    nt = len(done)
    wins = [t for t in done if t["pnl"] > 0]
    losses = [t for t in done if t["pnl"] < 0]
    win_rate = len(wins) / nt * 100 if nt else 0.0
    gw = sum(t["pnl"] for t in wins)
    gl = -sum(t["pnl"] for t in losses)
    pf = gw / gl if gl > 0 else float("inf")
    total_ret = equity[-1] / INIT_CAP * 100 - 100
    summary = {"total_ret": total_ret, "max_dd": max_dd, "n": nt,
               "win_rate": win_rate, "pf": pf, "final": equity[-1]}
    if return_trades:
        for t in done:
            t["entry_ts"] = int(df.ts.iloc[t["entry_i"]])
            t["exit_ts"]  = int(df.ts.iloc[t["exit_i"]])
        summary["trades"] = done
    return summary

# ============== 网格 ==============
TP_LIST = [0.02, 0.03, 0.04, 0.05, 0.06, 0.08]
SL_LIST = [0.01, 0.015, 0.02, 0.025, 0.03, 0.04]

def run_grid(df, flips, session_only, label):
    rows = []
    for tp in TP_LIST:
        for sl in SL_LIST:
            if sl >= tp:
                continue
            r = backtest(df, flips, session_only, tp, sl)
            rows.append((tp, sl, r))
    rows.sort(key=lambda x: x[2]["total_ret"], reverse=True)
    print("\n" + "=" * 78 + f"\n  {label}  共 {len(rows)} 组 (TP/SL 固定%)\n" + "=" * 78)
    print(f"  {'TP':>6} {'SL':>6} {'净收益':>9} {'回撤':>8} {'胜率':>7} {'笔数':>6} {'盈亏比':>8}")
    for tp, sl, r in rows[:12]:
        pf = f"{r['pf']:.2f}" if r['pf'] != float('inf') else "inf"
        print(f"  {tp*100:5.2f}% {sl*100:5.2f}% {r['total_ret']:8.2f}% "
              f"{r['max_dd']:7.2f}% {r['win_rate']:6.1f}% {r['n']:6d} {pf:>8}")
    return rows

# ============== 子集切分 ==============
def make_subset(df, flips, start_ts, end_ts):
    mask = (df.ts >= start_ts) & (df.ts < end_ts)
    df_sub = df[mask].reset_index(drop=True)
    start_i = int(np.argmax(mask.values))
    flips_sub = [{"i": f["i"] - start_i, "type": f["type"]}
                 for f in flips if start_ts <= df.ts.iloc[f["i"]] < end_ts]
    return df_sub, flips_sub

# ============== 滚动 walk-forward ==============
def walk_forward(df, flips):
    data_start = int(df.ts.iloc[0])
    end_ts = int(df.ts.iloc[-1]) + 1
    folds = [
        ("Train 2022-2023 / Test 2024", "2024-01-01", "2025-01-01"),
        ("Train 2022-2024 / Test 2025", "2025-01-01", "2026-01-01"),
        ("Train 2022-2025 / Test 2026", "2026-01-01", None),
    ]
    print("\n" + "=" * 78 +
          "\n  滚动 WALK-FORWARD (仅美盘, 训练段寻参 -> 下一年样本外, 含费率滑点)\n" + "=" * 78)
    print(f"  {'窗口':<32} {'寻参TP/SL':>10} {'训练净':>9} {'样本外净':>10} {'外回撤':>9} {'外胜率':>7}")
    for name, te_train, te_test in folds:
        train_end = int(pd.Timestamp(te_train, tz="UTC").timestamp() * 1000)
        test_end = end_ts if te_test is None else int(pd.Timestamp(te_test, tz="UTC").timestamp() * 1000)
        df_tr, fl_tr = make_subset(df, flips, data_start, train_end)
        df_te, fl_te = make_subset(df, flips, train_end, test_end)
        best = None
        for tp in TP_LIST:
            for sl in SL_LIST:
                if sl >= tp:
                    continue
                r = backtest(df_tr, fl_tr, True, tp, sl)
                if best is None or r["total_ret"] > best[2]["total_ret"]:
                    best = (tp, sl, r)
        bt, bs, rtr = best
        rte = backtest(df_te, fl_te, True, bt, bs)
        print(f"  {name:<32} {bt*100:4.1f}/{bs*100:4.1f}% {rtr['total_ret']:8.2f}% "
              f"{rte['total_ret']:9.2f}% {rte['max_dd']:8.2f}% {rte['win_rate']:6.1f}%")
    # 固定参数跨年(样本外稳健性): 两个窗口都夺冠的 TP6/SL1.5, 及 TP8/SL1.5
    print("  固定参数跨年样本外(训练段从未参与寻参):")
    for ftp, fsl in [(0.06, 0.015), (0.08, 0.015)]:
        line = f"    TP{ftp*100:.0f}%/SL{fsl*100:.1f}%: "
        for name, te_train, te_test in folds:
            train_end = int(pd.Timestamp(te_train, tz="UTC").timestamp() * 1000)
            test_end = end_ts if te_test is None else int(pd.Timestamp(te_test, tz="UTC").timestamp() * 1000)
            df_te2, fl_te2 = make_subset(df, flips, train_end, test_end)
            r = backtest(df_te2, fl_te2, True, ftp, fsl)
            line += f"{name.split('/')[-1].strip():>7} {r['total_ret']:7.2f}%  "
        print(line)

# ============== 成交明细导出 ==============
def export_trades(df, flips, tp, sl, fname, label):
    r = backtest(df, flips, True, tp, sl, return_trades=True)
    trades = r["trades"]
    rows = []
    for t in trades:
        side = t["side"]
        ret = (t["exit"] - t["entry"]) / t["entry"] * 100 if side == 1 \
              else (t["entry"] - t["exit"]) / t["entry"] * 100
        hold_bars = t["exit_i"] - t["entry_i"]
        rows.append({
            "side": "LONG" if side == 1 else "SHORT",
            "entry_time": pd.to_datetime(t["entry_ts"], unit="ms"),
            "exit_time":  pd.to_datetime(t["exit_ts"], unit="ms"),
            "entry": t["entry"], "exit": t["exit"],
            "pnl_usdt": round(t["pnl"], 2),
            "ret_pct": round(ret, 3),
            "hold_bars": hold_bars, "hold_hours": hold_bars,
            "reason": t["reason"],
        })
    out = pd.DataFrame(rows)
    out.to_csv(fname, index=False)
    print(f"\n--- {label} (TP{tp*100:.0f}%/SL{sl*100:.1f}%) 成交 {len(out)} 笔 -> {fname} ---")
    print(f"    净收益 {r['total_ret']:.2f}%  回撤 {r['max_dd']:.2f}%  "
          f"胜率 {r['win_rate']:.1f}%  盈亏比 {r['pf']:.2f}")
    if len(out):
        print(f"    平均持仓 {out.hold_bars.mean():.1f} 根(= {out.hold_bars.mean():.1f}h)  "
              f"中位 {out.hold_bars.median():.0f} 根")
        by_reason = out.groupby("reason")["pnl_usdt"].agg(["count", "sum"])
        print("    按出场原因:")
        print(by_reason.to_string())
        wins = out[out.pnl_usdt > 0]; losses = out[out.pnl_usdt < 0]
        print(f"    盈利笔均 {wins.pnl_usdt.mean():.1f}  亏损笔均 {losses.pnl_usdt.mean():.1f} USDT")
        print(f"    最大单笔盈 {out.pnl_usdt.max():.1f}  最大单笔亏 {out.pnl_usdt.min():.1f} USDT")
    return out

def main():
    df = load(SYMBOL, TF)
    n = len(df)
    t0 = pd.to_datetime(df.ts.iloc[0], unit="ms")
    t1 = pd.to_datetime(df.ts.iloc[-1], unit="ms")
    print(f"标的 {SYMBOL} {TF}  根数 {n}  区间 {t0} -> {t1}")
    bh_all = df.close.iloc[-1] / df.close.iloc[0] * 100 - 100
    print(f"买入持有(全段): {bh_all:.2f}%")

    st = super_trend(df.open.tolist(), df.high.tolist(), df.low.tolist(),
                     df.close.tolist(), PERIODS, MULT, SRC, CHANGE_ATR)
    flips = st["flips"]
    print(f"SuperTrend(10,3) flip 总数: {len(flips)}")

    # 全段
    run_grid(df, flips, True,  "仅美盘开盘(美东09:30-16:00) · 全段 2022-2026")
    run_grid(df, flips, False, "无时段过滤(全部flip) · 全段 2022-2026")

    # 2026 子集
    cutoff = int(pd.Timestamp("2026-01-01", tz="UTC").timestamp() * 1000)
    mask = df.ts >= cutoff
    df26 = df[mask].reset_index(drop=True)
    start_i = int(np.argmax(mask.values))  # 子集首根在原序列中的索引
    flips26 = [{"i": f["i"] - start_i, "type": f["type"]}
               for f in flips if f["i"] >= start_i]
    print(f"\n--- 2026 子集: 根数 {len(df26)}  flip {len(flips26)}  区间 "
          f"{pd.to_datetime(df26.ts.iloc[0],unit='ms')} -> {pd.to_datetime(df26.ts.iloc[-1],unit='ms')} ---")
    bh26 = df26.close.iloc[-1] / df26.close.iloc[0] * 100 - 100
    print(f"买入持有(2026): {bh26:.2f}%")
    us26 = run_grid(df26, flips26, True,  "仅美盘开盘 · 2026")
    no26 = run_grid(df26, flips26, False, "无时段过滤 · 2026")

    # 重点对比: 之前 15m 2026 最优 TP4%/SL2%
    print(f"\n{'='*78}\n  对照: 15m 2026 最优 TP4%/SL2% = -16.68%  →  1h 同参数(2026):")
    for sess, lab in [(True, "仅美盘"), (False, "无过滤")]:
        r = backtest(df26, flips26, sess, 0.04, 0.02)
        print(f"    [{lab}] 净收益 {r['total_ret']:.2f}%  回撤 {r['max_dd']:.2f}%  "
              f"胜率 {r['win_rate']:.1f}%  笔数 {r['n']}  盈亏比 {r['pf']:.2f}")

    # 滚动 walk-forward 验证(样本外)
    walk_forward(df, flips)

    # 最优参数逐笔成交明细导出
    export_trades(df, flips, 0.06, 0.015,
                  "btc_1h_trades_us_session.csv",
                  "仅美盘 · 全段 2022-2026 (TP6%/SL1.5%)")
    export_trades(df26, flips26, 0.06, 0.015,
                  "btc_1h_trades_2026.csv",
                  "仅美盘 · 2026 (TP6%/SL1.5%)")

if __name__ == "__main__":
    main()
