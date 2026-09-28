# -*- coding: utf-8 -*-
"""
Trend Hub 复刻版 回测引擎 (Python 复刻 TradingView Pine)
标的: BTCUSDT  周期: 1h  区间: 2026-01-01 ~ 2026-09-28
逻辑与 TrendHub_signal.pine / TrendHub_strategy_backtest.pine 完全一致。

建模假设(已在下方注明):
  - 信号在 bar i 收盘后产生, 成交价取 bar i 的收盘价 (无未来函数)
  - 仓位: strategy.percent_of_equity=10%, 初始资金 10000 USDT
  - 手续费: commission.percent=0.05%, 每次下单(开/平)各收一次
  - 出场: ATR 固定止损/止盈 (slMult=2.0, tpMult=3.0), 取信号产生 bar 的 close 与 atr 固定
  - 反向信号出现时平掉当前仓位(等价于 Pine 的 oppExit + strategy.entry 翻转)
  - 4h 共振: 用最近一根"已收盘"4h K 线的 rawState(lookahead_off 语义)
"""
import json
import sys
import urllib.request
import datetime as dt
import numpy as np
import pandas as pd

# ============== 参数(与 Pine 输入一致) ==============
ER_PERIOD = 34
FAST_LEN  = 2
SLOW_LEN  = 60
BAND_MULT = 1.6
ATR_LEN   = 14
HTF       = "240"          # 4h 共振
USE_HTF   = (len(sys.argv) < 2) or (sys.argv[1] != "nohtf")
SL_MULT   = 2.0
TP_MULT   = 3.0
OPP_EXIT  = True
INIT_CAP  = 10000.0
QTY_PCT   = 0.10
COMM      = 0.0005
START     = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
END       = dt.datetime(2026, 9, 28, 23, 59, tzinfo=dt.timezone.utc)

# ============== 数据下载 ==============
def fetch_klines(symbol, interval, start_ms, end_ms):
    rows, ts = [], start_ms
    while ts < end_ms:
        url = (f"https://api.binance.com/api/v3/klines?symbol={symbol}"
               f"&interval={interval}&startTime={ts}&endTime={end_ms}&limit=1000")
        data = json.loads(urllib.request.urlopen(url, timeout=30).read())
        if not data:
            break
        for k in data:
            rows.append([k[0], float(k[1]), float(k[2]), float(k[3]),
                         float(k[4]), float(k[5]), k[6]])
        ts = data[-1][6] + 1
        if len(data) < 1000:
            break
    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "vol", "close_time"])
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    return df

def to_df(raw):
    df = pd.DataFrame(raw, columns=["open_time", "open", "high", "low", "close", "vol", "close_time"])
    return df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)

print("下载 BTCUSDT 1h 数据 ...")
h1 = fetch_klines("BTCUSDT", "1h", int(START.timestamp()*1000), int(END.timestamp()*1000))
print(f"  1h 根数: {len(h1)}  区间: {pd.to_datetime(h1.open_time.iloc[0], unit='ms')} ~ {pd.to_datetime(h1.open_time.iloc[-1], unit='ms')}")
print("下载 BTCUSDT 4h 数据 ...")
h4 = fetch_klines("BTCUSDT", "4h", int(START.timestamp()*1000), int(END.timestamp()*1000))
print(f"  4h 根数: {len(h4)}")

# ============== 指标复刻 ==============
def atr_wilder(high, low, close, length):
    n = len(close)
    prev = np.roll(close, 1); prev[0] = close[0]  # 首根 TR 用 high-low
    tr = np.maximum.reduce([high - low,
                            np.abs(high - prev),
                            np.abs(low - prev)])
    atr = np.full(n, np.nan)
    if n < length:
        return atr
    atr[length - 1] = np.mean(tr[:length])
    for i in range(length, n):
        atr[i] = (atr[i-1] * (length - 1) + tr[i]) / length
    return atr

def kama_state(df, er_period, fast_len, slow_len, band_mult, atr_len):
    close = df["close"].values.astype(float)
    high  = df["high"].values.astype(float)
    low   = df["low"].values.astype(float)
    n = len(close)
    # 效率比 ER
    diff = np.abs(np.diff(close, prepend=close[0]))  # |close-close[1]|, diff[0]=0
    path = pd.Series(diff).rolling(er_period).sum().values
    chg = np.abs(close - np.roll(close, er_period))
    chg[:er_period] = np.nan
    er = np.where(path > 0, chg / np.where(path > 0, path, np.nan), 0.0)
    sc = (er * (2.0/(fast_len+1) - 2.0/(slow_len+1)) + 2.0/(slow_len+1)) ** 2
    atr = atr_wilder(high, low, close, atr_len)
    # KAMA
    k = np.full(n, np.nan)
    sma_init = pd.Series(close).rolling(er_period).mean().values
    for i in range(n):
        if np.isnan(k[i-1]) if i > 0 else True:
            k[i] = sma_init[i]
        else:
            k[i] = k[i-1] + sc[i] * (close[i] - k[i-1])
    # raw 状态
    raw = np.zeros(n, dtype=int)
    for i in range(1, n):
        if np.isnan(k[i]) or np.isnan(k[i-1]) or np.isnan(atr[i]):
            raw[i] = 0
        elif k[i] > k[i-1] and close[i] > k[i] + atr[i]*band_mult:
            raw[i] = 1
        elif k[i] < k[i-1] and close[i] < k[i] - atr[i]*band_mult:
            raw[i] = -1
        else:
            raw[i] = 0
    return k, atr, raw

# 1h 状态机
k1, atr1, raw1 = kama_state(h1, ER_PERIOD, FAST_LEN, SLOW_LEN, BAND_MULT, ATR_LEN)
state = np.zeros(len(raw1), dtype=int)
prev = 0
for i in range(len(raw1)):
    if raw1[i] != 0:
        prev = raw1[i]
    state[i] = prev

# 4h rawState -> 映射到 1h (最近已收盘的 4h 的 raw)
k4, atr4, raw4 = kama_state(h4, ER_PERIOD, FAST_LEN, SLOW_LEN, BAND_MULT, ATR_LEN)
h4m = h4[["close_time", "open_time"]].copy()
h4m["raw4"] = raw4
h4m = h4m.sort_values("close_time").reset_index(drop=True)
h1m = h1[["open_time"]].copy()
merged = pd.merge_asof(h1m, h4m, left_on="open_time", right_on="close_time",
                       direction="backward")
htf_state = merged["raw4"].fillna(0).astype(int).values

# 信号
long_sig  = (state == 1) & (np.r_[0, state[:-1]] != 1) & (htf_state == 1 if USE_HTF else np.ones(len(state), dtype=bool))
short_sig = (state == -1) & (np.r_[0, state[:-1]] != -1) & (htf_state == -1 if USE_HTF else np.ones(len(state), dtype=bool))
long_sig = np.asarray(long_sig)
short_sig = np.asarray(short_sig)

print(f"[debug] raw1!=0 次数: {int((raw1!=0).sum())}  state!=0 次数: {int((state!=0).sum())}")
print(f"[debug] htf_state 分布: {dict(zip(*np.unique(htf_state, return_counts=True)))}")
print(f"[debug] long_sig 次数: {int(long_sig.sum())}  short_sig 次数: {int(short_sig.sum())}")

# ============== 回测引擎 ==============
o = h1["open"].values.astype(float)
h = h1["high"].values.astype(float)
l = h1["low"].values.astype(float)
c = h1["close"].values.astype(float)
n = len(c)

cash = INIT_CAP
pos = 0            # 0 空仓, 1 多, -1 空
qty = 0.0
entry = 0.0
stop = 0.0
limit = 0.0
equity = [INIT_CAP]
trades = []

def do_commission(notional):
    global cash
    cash -= notional * COMM

for i in range(n):
    # 1) 已持仓 -> 先检查止损/止盈(用本根 high/low)
    if pos != 0:
        if pos == 1:
            if l[i] <= stop:
                pnl = qty * (stop - entry); do_commission(qty*stop)
                cash += pnl; trades[-1]["exit"] = stop; trades[-1]["exit_i"] = i
                trades[-1]["pnl"] = pnl - trades[-1]["comm"]; pos = 0; qty = 0
            elif h[i] >= limit:
                pnl = qty * (limit - entry); do_commission(qty*limit)
                cash += pnl; trades[-1]["exit"] = limit; trades[-1]["exit_i"] = i
                trades[-1]["pnl"] = pnl - trades[-1]["comm"]; pos = 0; qty = 0
        else:  # short
            if h[i] >= stop:
                pnl = qty * (entry - stop); do_commission(qty*stop)
                cash += pnl; trades[-1]["exit"] = stop; trades[-1]["exit_i"] = i
                trades[-1]["pnl"] = pnl - trades[-1]["comm"]; pos = 0; qty = 0
            elif l[i] <= limit:
                pnl = qty * (entry - limit); do_commission(qty*limit)
                cash += pnl; trades[-1]["exit"] = limit; trades[-1]["exit_i"] = i
                trades[-1]["pnl"] = pnl - trades[-1]["comm"]; pos = 0; qty = 0

    # 2) 信号处理(本根收盘成交)
    if long_sig[i]:
        if pos == -1:  # 平空
            pnl = qty * (entry - c[i]); do_commission(qty*c[i])
            cash += pnl; trades[-1]["exit"] = c[i]; trades[-1]["exit_i"] = i
            trades[-1]["pnl"] = pnl - trades[-1]["comm"]; pos = 0; qty = 0
        if pos <= 0:
            eq = cash  # 此时已平仓, equity≈cash
            qty = (eq * QTY_PCT) / c[i]
            do_commission(qty * c[i])
            entry = c[i]; a = atr1[i]
            stop = entry - a * SL_MULT; limit = entry + a * TP_MULT
            pos = 1
            trades.append({"side": "LONG", "entry": entry, "entry_i": i,
                          "stop": stop, "limit": limit, "comm": qty*c[i]*COMM, "pnl": None})
    elif short_sig[i]:
        if pos == 1:  # 平多
            pnl = qty * (c[i] - entry); do_commission(qty*c[i])
            cash += pnl; trades[-1]["exit"] = c[i]; trades[-1]["exit_i"] = i
            trades[-1]["pnl"] = pnl - trades[-1]["comm"]; pos = 0; qty = 0
        if pos >= 0:
            eq = cash
            qty = (eq * QTY_PCT) / c[i]
            do_commission(qty * c[i])
            entry = c[i]; a = atr1[i]
            stop = entry + a * SL_MULT; limit = entry - a * TP_MULT
            pos = -1
            trades.append({"side": "SHORT", "entry": entry, "entry_i": i,
                          "stop": stop, "limit": limit, "comm": qty*c[i]*COMM, "pnl": None})

    # 3) 记录权益
    if pos == 1:
        eq = cash + qty * (c[i] - entry)
    elif pos == -1:
        eq = cash + qty * (entry - c[i])
    else:
        eq = cash
    equity.append(eq)

equity = np.array(equity)
trades_df = pd.DataFrame(trades)

# ============== 统计 ==============
if trades_df.empty:
    print("\n[结果] 全程无成交 (无任何信号触发)。请检查参数/信号逻辑。")
    h1.to_csv("btc_1h_2026.csv", index=False)
    h4.to_csv("btc_4h_2026.csv", index=False)
    raise SystemExit

final = equity[-1]
total_ret = (final / INIT_CAP - 1) * 100
done = trades_df[trades_df["pnl"].notna()].copy()
n_trades = len(done)
wins = done[done["pnl"] > 0]
losses = done[done["pnl"] < 0]
win_rate = len(wins) / n_trades * 100 if n_trades else 0
gross_win = wins["pnl"].sum()
gross_loss = -losses["pnl"].sum()
profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")
# 最大回撤
peak = np.maximum.accumulate(equity)
dd = (equity - peak) / peak * 100
max_dd = dd.min()
# 买入持有
bh_ret = (c[-1] / c[0] - 1) * 100
# 持仓时长
if n_trades:
    done["hold"] = done["exit_i"] - done["entry_i"]
    avg_hold = done["hold"].mean()
else:
    avg_hold = 0

print("\n================ 2026 BTCUSDT 1h 回测结果 ================")
print(f"区间            : {pd.to_datetime(h1.open_time.iloc[0], unit='ms')} -> {pd.to_datetime(h1.open_time.iloc[-1], unit='ms')}")
print(f"K线根数         : {n}")
print(f"初始资金        : {INIT_CAP:.2f} USDT")
print(f"最终权益        : {final:.2f} USDT")
print(f"策略总收益      : {total_ret:.2f}%")
print(f"买入持有(BTC)   : {bh_ret:.2f}%")
print(f"交易次数        : {n_trades}")
print(f"胜率            : {win_rate:.2f}%")
print(f"总盈利/总亏损   : {gross_win:.2f} / {gross_loss:.2f}")
print(f"盈亏比(PF)      : {profit_factor:.2f}")
print(f"最大回撤        : {max_dd:.2f}%")
print(f"平均持仓        : {avg_hold:.1f} 根(1h)")
print("=========================================================")

# 成交明细(前/后若干条)
if n_trades:
    done2 = done.copy()
    done2["entry_t"] = pd.to_datetime(h1.open_time.iloc[done2["entry_i"]].values, unit="ms")
    done2["exit_t"]  = pd.to_datetime(h1.open_time.iloc[done2["exit_i"]].values, unit="ms")
    cols = ["side", "entry_t", "entry", "exit_t", "exit", "pnl"]
    print("\n最近 15 笔成交:")
    with pd.option_context("display.max_rows", 15, "display.width", 200):
        print(done2[cols].tail(15).to_string(index=False))

# 保存
h1.to_csv("btc_1h_2026.csv", index=False)
h4.to_csv("btc_4h_2026.csv", index=False)
if n_trades:
    done2.to_csv("trades_2026.csv", index=False)
print("\n数据已保存: btc_1h_2026.csv, btc_4h_2026.csv, trades_2026.csv")
