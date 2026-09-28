"""4h 策略止盈止损参数寻优（样本内网格 + 前后半段稳定性检查）。
出场结构同 bt_4h_full：TP1 分部 + 保本 + ST 轨道跟踪 + 剩余下一翻转平仓。
寻优维度：TP1_PCT(触发幅度) / TP1_RATIO(平掉比例) / SL_PCT(初始固定止损%)。
"""
from __future__ import annotations
import asyncio
from history import fetch_candles
from indicators import super_trend

SYM, BASE_TF, LIMIT = "BTC-USDT", "4h", 40000
NOTIONAL = 10_000.0
FEE = 0.05 / 100

TP1_PCTS = [1.0, 1.5, 2.0, 2.5, 3.0]
TP1_RATIOS = [0.5, 0.6, 0.7, 0.8]
SL_PCTS = [1.0, 1.5, 2.0, 2.5, 3.0]


def backtest(bc, up_plot, dn_plot, flip_idx, signals, tp1_pct, tp1_ratio, sl_pct):
    closes = [x["c"] for x in bc]
    highs = [x["h"] for x in bc]
    lows = [x["l"] for x in bc]
    trades = []
    for f in signals:
        i = f["i"]
        long = f["type"] == "buy"
        entry = closes[i]
        if long:
            stp0 = up_plot[i]
            if stp0 is None or stp0 >= entry:
                stp0 = entry * (1 - sl_pct / 100)
        else:
            stp0 = dn_plot[i]
            if stp0 is None or stp0 <= entry:
                stp0 = entry * (1 + sl_pct / 100)
        stop = stp0
        tp1_price = entry * (1 + tp1_pct / 100) if long else entry * (1 - tp1_pct / 100)
        tp1_done = False
        coins = NOTIONAL / entry
        realized = -entry * coins * FEE
        closed = False
        for j in range(i + 1, len(bc)):
            if long:
                nl = dn_plot[j]
                if nl is not None and nl > stop:
                    stop = nl
                if lows[j] <= stop:
                    px = stop
                    if tp1_done:
                        realized += (px - entry) * coins * (1 - tp1_ratio)
                        realized -= px * coins * (1 - tp1_ratio) * FEE
                    else:
                        realized += (px - entry) * coins
                        realized -= px * coins * FEE
                    closed = True; break
                if not tp1_done and highs[j] >= tp1_price:
                    realized += (tp1_price - entry) * coins * tp1_ratio
                    realized -= tp1_price * coins * tp1_ratio * FEE
                    tp1_done = True
                    stop = entry
            else:
                nl = up_plot[j]
                if nl is not None and nl < stop:
                    stop = nl
                if highs[j] >= stop:
                    px = stop
                    if tp1_done:
                        realized += (entry - px) * coins * (1 - tp1_ratio)
                        realized -= px * coins * (1 - tp1_ratio) * FEE
                    else:
                        realized += (entry - px) * coins
                        realized -= px * coins * FEE
                    closed = True; break
                if not tp1_done and lows[j] <= tp1_price:
                    realized += (entry - tp1_price) * coins * tp1_ratio
                    realized -= tp1_price * coins * tp1_ratio * FEE
                    tp1_done = True
                    stop = entry
            if j in flip_idx:
                if tp1_done:
                    realized += (closes[j] - entry) * coins * (1 - tp1_ratio)
                    realized -= closes[j] * coins * (1 - tp1_ratio) * FEE
                else:
                    realized += (closes[j] - entry) * coins
                    realized -= closes[j] * coins * FEE
                closed = True; break
        if not closed:
            if tp1_done:
                realized += (closes[-1] - entry) * coins * (1 - tp1_ratio)
                realized -= closes[-1] * coins * (1 - tp1_ratio) * FEE
            else:
                realized += (closes[-1] - entry) * coins
                realized -= closes[-1] * coins * FEE
        trades.append({"realized": realized, "ts": bc[i]["ts"]})
    return trades


def metrics(trades):
    n = len(trades)
    wins = [t["realized"] for t in trades if t["realized"] > 0]
    losses = [t["realized"] for t in trades if t["realized"] <= 0]
    tot = sum(t["realized"] for t in trades)
    eq = [NOTIONAL]
    for t in trades:
        eq.append(eq[-1] + t["realized"])
    eq = eq[1:]
    peak = [eq[0]]
    for v in eq[1:]:
        peak.append(max(peak[-1], v))
    dd = min((e - p) / p for e, p in zip(eq, peak)) * 100
    pf = sum(wins) / (-sum(losses)) if losses else 99.0
    return {
        "n": n,
        "wr": len(wins) / n * 100 if n else 0,
        "tot_pct": tot / NOTIONAL * 100,
        "pf": pf,
        "maxdd": dd,
        "ret_dd": (tot / NOTIONAL * 100) / (-dd) if dd < 0 else 0,
        "trades": trades,
    }


async def main():
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(None, fetch_candles, BASE_TF, LIMIT, SYM)
    if not raw:
        print("no data"); return
    bc = [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol} for c in raw]
    opens = [x["o"] for x in bc]; highs = [x["h"] for x in bc]
    lows = [x["l"] for x in bc]; closes = [x["c"] for x in bc]
    st = super_trend(opens, highs, lows, closes, periods=10, multiplier=3.0, change_atr=True)
    flip_idx = {f["i"] for f in st["flips"] if f["i"] < len(bc)}
    signals = [f for f in st["flips"] if f["i"] < len(bc)]
    print(f"4h根数={len(bc)} 信号={len(signals)}  网格={len(TP1_PCTS)*len(TP1_RATIOS)*len(SL_PCTS)}组合")

    rows = []
    for tp in TP1_PCTS:
        for tr in TP1_RATIOS:
            for sl in SL_PCTS:
                m = metrics(backtest(bc, st["up_plot"], st["dn_plot"], flip_idx,
                                     signals, tp, tr, sl))
                # 前后半段稳定性（按收益中位数时间切）
                ts_list = sorted(t["ts"] for t in m["trades"])
                mid = ts_list[len(ts_list) // 2]
                first = [t["realized"] for t in m["trades"] if t["ts"] <= mid]
                second = [t["realized"] for t in m["trades"] if t["ts"] > mid]
                hf = sum(first) / NOTIONAL * 100
                sh = sum(second) / NOTIONAL * 100
                rows.append((tp, tr, sl, m, hf, sh))

    def show(title, key, rev=True):
        print(f"\n=== {title} ===")
        print(f"{'TP1%':>5}{'RATIO':>7}{'SL%':>6}{'笔':>5}{'胜率':>7}{'收益%':>9}{'pf':>7}{'回撤%':>8}{'收益/回撤':>10}{'前半%':>8}{'后半%':>8}")
        for tp, tr, sl, m, hf, sh in sorted(rows, key=lambda r: r[3][key], reverse=rev)[:10]:
            mm = m
            print(f"{tp:>5}{tr:>7}{sl:>6}{mm['n']:>5}{mm['wr']:>6.1f}%{mm['tot_pct']:>9.1f}{mm['pf']:>7.2f}{mm['maxdd']:>8.1f}{mm['ret_dd']:>10.2f}{hf:>8.1f}{sh:>8.1f}")

    show("按 profit_factor 排序 Top10", "pf")
    show("按 总收益% 排序 Top10", "tot_pct")
    show("按 收益/回撤比 排序 Top10", "ret_dd")

    # 稳定性筛选：前后半都 >0 且回撤可接受(<25%) 里收益最高
    stable = [(tp, tr, sl, m, hf, sh) for tp, tr, sl, m, hf, sh in rows
              if hf > 0 and sh > 0 and m["maxdd"] > -25]
    if stable:
        best = max(stable, key=lambda r: r[3]["tot_pct"])
        tp, tr, sl, m, hf, sh = best
        print(f"\n=== 稳健最优（前后半均正 & 回撤<-25% & 收益最高）===")
        print(f"TP1={tp}% RATIO={tr} SL={sl}%  收益={m['tot_pct']:.1f}% 胜率={m['wr']:.1f}% pf={m['pf']:.2f} 回撤={m['maxdd']:.1f}%  前半={hf:.1f}% 后半={sh:.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
