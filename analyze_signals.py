"""
98 笔逐笔盈亏分析：直接复用官方引擎，保证与 backtest_regime_mtf.py 的 -16.42% 一致。
- 信号：regime_engine.computeRegimeSignalsMTF (4h定势 + 1h触发)
- 回测：与 backtest_st.backtest 逐字一致，仅增加 entry/exit 记录
"""
import sys, os, sqlite3
from datetime import datetime, timezone
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
BACKEND = r'd:/个人项目代码/supertrendMain/backend'
sys.path.insert(0, BACKEND)
from regime_engine import computeRegimeSignalsMTF
import backtest_st

DB = r'd:/个人项目代码/supertrendMain/backend/candle_data.db'
SYMBOL = 'BTC-USDT'

TAKER_FEE = 0.0005
MAKER_FEE = 0.0002
SLIP = 0.0003
TP1_PCT = 0.02
TP2_PCT = 0.04


def load(symbol, tf):
    c = sqlite3.connect(DB)
    rows = c.execute(
        "SELECT ts,o,h,l,c,vol FROM candles WHERE symbol=? AND tf=? ORDER BY ts ASC",
        (symbol, tf)).fetchall()
    c.close()
    return [{"ts": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "vol": r[5]} for r in rows]


def instrumented_backtest(candles, signals, apply_fees=True, tp1_pct=TP1_PCT, tp2_pct=TP2_PCT):
    """逐字复制 backtest_st.backtest，仅记录 per-trade entry/exit。trade[k] 对应 signal[k] 开仓。"""
    h = [x["h"] for x in candles]; l = [x["l"] for x in candles]
    c = [x["c"] for x in candles]; n = len(c)
    ft = TAKER_FEE if apply_fees else 0.0
    fm = MAKER_FEE if apply_fees else 0.0
    sl = SLIP if apply_fees else 0.0
    TP1 = tp1_pct; TP2 = tp2_pct

    equity = 10000.0
    side = 0; entry = 0.0; entry_units = 0.0; remain = 0.0
    tp1 = tp2 = False; stop = None
    pending = None
    trades = []; tp1_hits = tp2_hits = 0; fees_paid = 0.0

    def open_pos(price, action):
        nonlocal equity, side, entry, entry_units, remain, tp1, tp2, stop, pending, fees_paid
        sgn = 1 if action == "buy" else -1
        fill = price * (1 + sl) if sgn == 1 else price * (1 - sl)
        units = equity / fill
        fee = units * fill * ft
        equity -= fee; fees_paid += fee
        side = sgn; entry = fill; entry_units = units; remain = units
        tp1 = tp2 = False; stop = None
        pending = {"side": sgn, "entry": fill, "eb": equity + fee}  # eb=开仓前权益(含刚扣的费)

    def close_partial(frac, price, is_taker):
        nonlocal equity, remain, fees_paid
        if side == 0 or remain <= 0:
            return
        sgn = side
        u = frac * entry_units
        fill = price * (1 - sl) if sgn == 1 else price * (1 + sl)
        notional = u * fill
        fee = notional * (ft if is_taker else fm)
        pnl = u * (fill - entry) if sgn == 1 else u * (entry - fill)
        equity += pnl - fee; fees_paid += fee
        remain -= u

    def close_full(price, is_taker):
        nonlocal equity, side, remain, entry_units, tp1, tp2, stop, pending, trades, fees_paid
        if side == 0 or pending is None:
            return
        sgn = side
        fill = price * (1 - sl) if sgn == 1 else price * (1 + sl)
        notional = remain * fill
        fee = notional * (ft if is_taker else fm)
        pnl = remain * (fill - entry) if sgn == 1 else remain * (entry - fill)
        equity += pnl - fee; fees_paid += fee
        # 真实逐笔盈亏 = 开仓到平仓整个过程的权益变化(含 TP1/TP2 分批止盈落袋)
        pnl_true = (equity - pending["eb"]) / pending["eb"] * 100
        trades.append({"side": sgn, "entry": pending["entry"], "exit": fill,
                       "pnl_pct": pnl_true})
        side = 0; remain = 0.0; entry_units = 0.0; tp1 = tp2 = False; stop = None; pending = None

    def bar(j):
        nonlocal tp1, tp2, stop, tp1_hits, tp2_hits
        if side == 1:
            tp1p = entry * (1 + TP1); tp2p = entry * (1 + TP2)
            if not tp1 and h[j] >= tp1p:
                close_partial(0.30, tp1p, False); tp1 = True; stop = entry; tp1_hits += 1
            if not tp2 and h[j] >= tp2p:
                close_partial(0.40, tp2p, False); tp2 = True; stop = tp1p; tp2_hits += 1
            if stop is not None and l[j] <= stop:
                close_full(stop, True); return
        else:
            tp1p = entry * (1 - TP1); tp2p = entry * (1 - TP2)
            if not tp1 and l[j] <= tp1p:
                close_partial(0.30, tp1p, False); tp1 = True; stop = entry; tp1_hits += 1
            if not tp2 and l[j] <= tp2p:
                close_partial(0.40, tp2p, False); tp2 = True; stop = tp1p; tp2_hits += 1
            if stop is not None and h[j] >= stop:
                close_full(stop, True); return

    for k in range(len(signals)):
        sig = signals[k]
        ei = sig["i"]
        if side != 0:
            close_full(c[ei], True)
        open_pos(c[ei], sig["action"])
        end = signals[k + 1]["i"] if k + 1 < len(signals) else n - 1
        for j in range(ei + 1, end + 1):
            if side == 0:
                break
            bar(j)
    if side != 0:
        close_full(c[-1], True)
    return trades


def main():
    k4 = load(SYMBOL, '4h')
    k1 = load(SYMBOL, '1h')
    d0 = datetime.fromtimestamp(k1[0]["ts"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    d1 = datetime.fromtimestamp(k1[-1]["ts"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    print(f"样本范围: {d0} .. {d1}  1h={len(k1)}根")

    # 官方引擎信号（与 backtest_regime_mtf.py 完全一致）
    sigs = computeRegimeSignalsMTF(k4, k1)
    print(f"官方引擎信号数: {len(sigs)}")

    # 校验：用官方 backtest 跑一遍，确认净收益与 -16.42% 一致
    ref = backtest_st.backtest(k1, sigs, apply_fees=True, tp1_pct=TP1_PCT, tp2_pct=TP2_PCT)
    print(f"官方 backtest 净收益: {ref['total_return_pct']:.2f}%  胜率: {ref['win_rate_pct']:.1f}%  "
          f"回撤: {ref['max_drawdown_pct']:.2f}%")

    trades = instrumented_backtest(k1, sigs, apply_fees=True)
    tag = {"trend": "趋势", "breakout": "突破", "neutral_range": "横盘",
           "uptrend_consol": "上涨整理", "downtrend_consol": "下跌整理"}

    print(f"\n{'='*84}\n  98 笔逐笔盈亏 (信号k开仓 → 信号k+1/期末平仓, TP1=2% TP2=4% 含费滑)\n{'='*84}")
    print(f"  {'#':>3}  {'开仓时间':16} {'方向':4} {'状态':8} {'入场价':>11} {'出场价':>11} {'盈亏%':>8}")
    wins = 0; eq = 1.0
    for k, t in enumerate(trades):
        sig = sigs[k]
        opents = datetime.fromtimestamp(k1[trades[k].get('open_i', sig['i'])]["ts"] / 1000,
                                        tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if False else \
            datetime.fromtimestamp(k1[sig['i']]["ts"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        pnl = t["pnl_pct"]; eq *= (1 + pnl / 100)
        if pnl > 0:
            wins += 1
        print(f"  {k+1:3d}  {opents:16} {('多' if t['side']==1 else '空'):4} "
              f"{tag.get(sig['state'], sig['state']):8} {t['entry']:11.1f} {t['exit']:11.1f} {pnl:8.2f}")
    comp = eq * 100 - 100
    print(f"\n  逐笔复利累计: {comp:7.2f}%   胜率: {wins}/{len(trades)} = {wins/len(trades)*100:.1f}%")
    print(f"  (与上方官方 backtest 净收益应一致)")


if __name__ == "__main__":
    main()
