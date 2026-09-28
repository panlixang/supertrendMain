"""4h 完整策略回测：SuperTrend(10,3.0) 翻转开仓，复用 position.py 出场规则
（TP1 1.5% 平 70% + 保本 + 跟踪止损 + 剩余 30% 下一根反向翻转平仓）。
数据：OKX 长历史 4h（与 verify_4h 同一来源）。
"""
from __future__ import annotations
import asyncio
import datetime as dt
from collections import Counter

from history import fetch_candles
from indicators import super_trend

SYM = "BTC-USDT"
BASE_TF = "4h"
LIMIT = 40000
NOTIONAL = 10_000.0
TP1_PCT = 1.5
TP1_RATIO = 0.70
SL_PCT_FALLBACK = 2.0
FEE = 0.05 / 100  # 单边 taker


def ts(ms):
    return dt.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d")


async def main():
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(None, fetch_candles, BASE_TF, LIMIT, SYM)
    if not raw:
        print("no data"); return
    bc = [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol} for c in raw]
    opens = [x["o"] for x in bc]
    highs = [x["h"] for x in bc]
    lows = [x["l"] for x in bc]
    closes = [x["c"] for x in bc]
    tss = [x["ts"] for x in bc]

    st = super_trend(opens, highs, lows, closes, periods=10, multiplier=3.0, change_atr=True)
    up_plot, dn_plot = st["up_plot"], st["dn_plot"]
    flip_idx = {f["i"] for f in st["flips"] if f["i"] < len(bc)}
    signals = [f for f in st["flips"] if f["i"] < len(bc)]

    print(f"数据范围: {ts(tss[0])} ~ {ts(tss[-1])}  4h根数={len(bc)}  信号数={len(signals)}")

    trades = []
    for f in signals:
        i = f["i"]
        long = f["type"] == "buy"
        entry = closes[i]
        if long:
            stp0 = up_plot[i]
            if stp0 is None or stp0 >= entry:
                stp0 = entry * (1 - SL_PCT_FALLBACK / 100)
        else:
            stp0 = dn_plot[i]
            if stp0 is None or stp0 <= entry:
                stp0 = entry * (1 + SL_PCT_FALLBACK / 100)
        stop = stp0
        tp1_price = entry * (1 + TP1_PCT / 100) if long else entry * (1 - TP1_PCT / 100)
        tp1_done = False
        coins = NOTIONAL / entry
        realized = -entry * coins * FEE
        exit_reason = "末根平仓"
        closed = False

        for j in range(i + 1, len(bc)):
            if long:
                nl = dn_plot[j]
                if nl is not None and nl > stop:
                    stop = nl
                if lows[j] <= stop:
                    px = stop
                    if tp1_done:
                        realized += (px - entry) * coins * (1 - TP1_RATIO)
                        realized -= px * coins * (1 - TP1_RATIO) * FEE
                    else:
                        realized += (px - entry) * coins
                        realized -= px * coins * FEE
                    exit_reason = "止损"; closed = True; break
                if not tp1_done and highs[j] >= tp1_price:
                    realized += (tp1_price - entry) * coins * TP1_RATIO
                    realized -= tp1_price * coins * TP1_RATIO * FEE
                    tp1_done = True
                    stop = entry
            else:
                nl = up_plot[j]
                if nl is not None and nl < stop:
                    stop = nl
                if highs[j] >= stop:
                    px = stop
                    if tp1_done:
                        realized += (entry - px) * coins * (1 - TP1_RATIO)
                        realized -= px * coins * (1 - TP1_RATIO) * FEE
                    else:
                        realized += (entry - px) * coins
                        realized -= px * coins * FEE
                    exit_reason = "止损"; closed = True; break
                if not tp1_done and lows[j] <= tp1_price:
                    realized += (entry - tp1_price) * coins * TP1_RATIO
                    realized -= tp1_price * coins * TP1_RATIO * FEE
                    tp1_done = True
                    stop = entry
            if j in flip_idx:
                if tp1_done:
                    realized += (closes[j] - entry) * coins * (1 - TP1_RATIO)
                    realized -= closes[j] * coins * (1 - TP1_RATIO) * FEE
                else:
                    realized += (closes[j] - entry) * coins
                    realized -= closes[j] * coins * FEE
                exit_reason = "反向信号"; closed = True; break

        if not closed:
            if tp1_done:
                realized += (closes[-1] - entry) * coins * (1 - TP1_RATIO)
                realized -= closes[-1] * coins * (1 - TP1_RATIO) * FEE
            else:
                realized += (closes[-1] - entry) * coins
                realized -= closes[-1] * coins * FEE

        trades.append({"long": long, "realized": realized, "reason": exit_reason})

    n = len(trades)
    wins = [t["realized"] for t in trades if t["realized"] > 0]
    losses = [t["realized"] for t in trades if t["realized"] <= 0]
    tot = sum(t["realized"] for t in trades)
    wr = len(wins) / n * 100
    pf = sum(wins) / (-sum(losses)) if losses else float("inf")
    avg = tot / n

    # 权益曲线与最大回撤（按固定 NOTIONAL 累加油漆）
    eq = [NOTIONAL]
    for t in trades:
        eq.append(eq[-1] + t["realized"])
    eq = eq[1:]
    peak = [eq[0]]
    for v in eq[1:]:
        peak.append(max(peak[-1], v))
    dd = [(e - p) / p for e, p in zip(eq, peak)]
    max_dd = min(dd) * 100

    print(f"\n=== 4h 完整策略 (TP1+保本+跟踪+下一翻转平仓) ===")
    print(f"笔数={n}  胜率={wr:.1f}%  总收益={tot:,.0f} USDT ({tot/NOTIONAL*100:.1f}%)")
    print(f"均笔={avg:,.1f} USDT  盈利笔和={sum(wins):,.0f}  亏损笔和={sum(losses):,.0f}  pf={pf:.2f}")
    print(f"最大回撤={max_dd:.1f}%  期末权益={eq[-1]:,.0f} USDT")
    print("\n出场原因分布:")
    for k, v in Counter(t["reason"] for t in trades).most_common():
        print(f"  {k:<10}{v}")
    tp1hits = sum(1 for t in trades if t["reason"] in ("反向信号", "止损") and t["realized"] > 0)
    print(f"\nTP1 触发率(粗略)={sum(1 for _ in trades)} 笔全统计完成")


if __name__ == "__main__":
    asyncio.run(main())
