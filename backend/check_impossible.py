"""铁证检查：grid_search_4h / walk_forward_4h 的回测里，
有多少笔是以「不可能成交的价格」平仓的？

多头止损成交价 = stop；若 stop > 该根最高价 high[j]，说明市场根本没到过这个价，
却按这个价成交了 —— 虚假成交，会系统性虚增收益。
空头同理：若 stop < 该根最低价 low[j] 即不可能。
"""
from history import fetch_candles
from indicators import super_trend

NOTIONAL = 10_000.0
FEE = 0.05 / 100
TP1_PCT, TP1_RATIO, SL_PCT = 2.0, 0.5, 2.0


def main():
    raw = fetch_candles("4h", 40000, "BTC-USDT")
    bc = [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol} for c in raw]
    st = super_trend([x["o"] for x in bc], [x["h"] for x in bc], [x["l"] for x in bc],
                     [x["c"] for x in bc], periods=10, multiplier=3.0, change_atr=True)
    up, dn = st["up_plot"], st["dn_plot"]
    flip_idx = {f["i"] for f in st["flips"] if f["i"] < len(bc)}
    signals = [f for f in st["flips"] if f["i"] < len(bc)]
    closes = [x["c"] for x in bc]
    highs = [x["h"] for x in bc]
    lows = [x["l"] for x in bc]

    bad = 0            # 不可能成交的笔数
    bad_pnl = 0.0      # 这些笔记下的盈亏
    total = 0.0
    worst = []

    for f in signals:
        i = f["i"]
        long = f["type"] == "buy"
        entry = closes[i]
        if long:
            stp0 = up[i]
            if stp0 is None or stp0 >= entry:
                stp0 = entry * (1 - SL_PCT / 100)
        else:
            stp0 = dn[i]
            if stp0 is None or stp0 <= entry:
                stp0 = entry * (1 + SL_PCT / 100)
        stop = stp0
        tp1_price = entry * (1 + TP1_PCT / 100) if long else entry * (1 - TP1_PCT / 100)
        tp1_done = False
        coins = NOTIONAL / entry
        realized = -entry * coins * FEE
        closed = False

        for j in range(i + 1, len(bc)):
            if long:
                nl = dn[j]
                if nl is not None and nl > stop:
                    stop = nl
                if lows[j] <= stop:
                    px = stop
                    # 多头止损：成交价必须 <= 该根最高价，否则市场从未到过该价
                    if px > highs[j]:
                        bad += 1
                        worst.append((len(worst), i, j, "long", px, highs[j], lows[j]))
                    left = (1 - TP1_RATIO) if tp1_done else 1.0
                    realized += (px - entry) * coins * left
                    realized -= px * coins * left * FEE
                    closed = True
                    break
                if not tp1_done and highs[j] >= tp1_price:
                    realized += (tp1_price - entry) * coins * TP1_RATIO
                    realized -= tp1_price * coins * TP1_RATIO * FEE
                    tp1_done = True
                    stop = entry
            else:
                nl = up[j]
                if nl is not None and nl < stop:
                    stop = nl
                if highs[j] >= stop:
                    px = stop
                    if px < lows[j]:
                        bad += 1
                        worst.append((len(worst), i, j, "short", px, highs[j], lows[j]))
                    left = (1 - TP1_RATIO) if tp1_done else 1.0
                    realized += (entry - px) * coins * left
                    realized -= px * coins * left * FEE
                    closed = True
                    break
                if not tp1_done and lows[j] <= tp1_price:
                    realized += (entry - tp1_price) * coins * TP1_RATIO
                    realized -= tp1_price * coins * TP1_RATIO * FEE
                    tp1_done = True
                    stop = entry
            if j in flip_idx:
                px = closes[j]
                left = (1 - TP1_RATIO) if tp1_done else 1.0
                realized += ((px - entry) * coins * left) if long else ((entry - px) * coins * left)
                realized -= px * coins * left * FEE
                closed = True
                break
        if not closed:
            px = closes[-1]
            left = (1 - TP1_RATIO) if tp1_done else 1.0
            realized += ((px - entry) * coins * left) if long else ((entry - px) * coins * left)
            realized -= px * coins * left * FEE

        total += realized
        if worst and worst[-1][1] == i:
            bad_pnl += realized

    print(f"总笔数 = {len(signals)}")
    print(f"【不可能成交】的止损平仓笔数 = {bad}  （占比 {bad/len(signals)*100:.1f}%）")
    print(f"按 grid 逻辑算出的总收益 = {total/NOTIONAL*100:.1f}%")
    print("\n样例（成交价 vs 该根真实价格区间）：")
    print(f"{'side':<6}{'成交价':>12}{'该根high':>12}{'该根low':>12}   判定")
    for _, i, j, side, px, hj, lj in worst[:8]:
        note = "成交价>最高价(不可能)" if (side == "long" and px > hj) else "成交价<最低价(不可能)"
        print(f"{side:<6}{px:>12.2f}{hj:>12.2f}{lj:>12.2f}   {note}")


main()
