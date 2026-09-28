"""逐笔对拍：grid_search_4h.backtest  vs  bt_4h_byyear(st_notrail) 逻辑。
找出第一笔分歧，定位 1109% 与 165% 的差异根源。
"""
import asyncio
from history import fetch_candles
from indicators import super_trend
import grid_search_4h as gs

NOTIONAL = 10_000.0
FEE = 0.05 / 100
TP1_PCT, TP1_RATIO, SL_PCT = 2.0, 0.5, 2.0


def mine(bc, up_plot, dn_plot, flip_idx, signals):
    """复制 bt_4h_byyear.py 的 st_notrail 逻辑（不跟踪，初始止损=超趋线，fallback 固定%）。"""
    closes = [x["c"] for x in bc]
    highs = [x["h"] for x in bc]
    lows = [x["l"] for x in bc]
    out = []
    for f in signals:
        i = f["i"]
        long = f["type"] == "buy"
        entry = closes[i]
        fallback = entry * (1 - SL_PCT / 100) if long else entry * (1 + SL_PCT / 100)
        line0 = up_plot[i] if long else dn_plot[i]
        if line0 is None or (long and line0 >= entry) or (not long and line0 <= entry):
            stop = fallback
        else:
            stop = line0
        tp1_price = entry * (1 + TP1_PCT / 100) if long else entry * (1 - TP1_PCT / 100)
        coins = NOTIONAL / entry
        realized = -entry * coins * FEE
        tp1_done = False
        closed = False
        for j in range(i + 1, len(bc)):
            # st_notrail：不跟踪
            if (lows[j] <= stop) if long else (highs[j] >= stop):
                px = stop
                left = (1 - TP1_RATIO) if tp1_done else 1.0
                realized += ((px - entry) * coins * left) if long else ((entry - px) * coins * left)
                realized -= px * coins * left * FEE
                closed = True
                break
            if not tp1_done and ((highs[j] >= tp1_price) if long else (lows[j] <= tp1_price)):
                realized += ((tp1_price - entry) * coins * TP1_RATIO) if long \
                    else ((entry - tp1_price) * coins * TP1_RATIO)
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
        out.append(realized)
    return out


def main():
    raw = fetch_candles("4h", 40000, "BTC-USDT")
    bc = [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol} for c in raw]
    st = super_trend([x["o"] for x in bc], [x["h"] for x in bc], [x["l"] for x in bc],
                     [x["c"] for x in bc], periods=10, multiplier=3.0, change_atr=True)
    up, dn = st["up_plot"], st["dn_plot"]
    flip_idx = {f["i"] for f in st["flips"] if f["i"] < len(bc)}
    signals = [f for f in st["flips"] if f["i"] < len(bc)]

    g = [t["realized"] for t in gs.backtest(bc, up, dn, flip_idx, signals, TP1_PCT, TP1_RATIO, SL_PCT)]
    m = mine(bc, up, dn, flip_idx, signals)

    print(f"笔数 grid={len(g)}  mine={len(m)}")
    print(f"grid 总收益 = {sum(g)/NOTIONAL*100:.1f}%")
    print(f"mine 总收益 = {sum(m)/NOTIONAL*100:.1f}%")

    diff = [k for k in range(min(len(g), len(m))) if abs(g[k] - m[k]) > 1e-6]
    print(f"分歧笔数 = {len(diff)} / {len(g)}")
    if diff:
        k = diff[0]
        f = signals[k]
        i = f["i"]
        print(f"\n第一处分歧: 第 {k} 笔  i={i}  type={f['type']}  entry={bc[i]['c']}")
        print(f"  up_plot[i]={up[i]}  dn_plot[i]={dn[i]}")
        print(f"  grid realized={g[k]:.2f}   mine realized={m[k]:.2f}")
        print("\n前 8 笔对比:")
        for k in range(min(8, len(g))):
            flag = "  <-- 分歧" if abs(g[k] - m[k]) > 1e-6 else ""
            print(f"  #{k:<3} grid={g[k]:>10.2f}  mine={m[k]:>10.2f}{flag}")

    # 看 grid 里最大的几笔
    top = sorted(range(len(g)), key=lambda k: -g[k])[:5]
    print("\ngrid 盈利最大的 5 笔:")
    for k in top:
        f = signals[k]
        print(f"  #{k:<3} i={f['i']:<6} {f['type']:<5} grid={g[k]:>10.2f}  mine={m[k]:>10.2f}")


main()
