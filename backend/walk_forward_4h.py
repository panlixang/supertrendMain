"""4h 策略 walk-forward 样本外验证（防过拟合）。
切 5 个连续时间窗；每窗用「之前所有窗」训练选参(TP1×RATIO, SL固定2.0)，在「该窗」测试。
对比：训练选出的参数 vs 全局推荐参数(TP1=2.0,RATIO=0.5,SL=2.0) 的 OOS 表现。
"""
from __future__ import annotations
import asyncio
import numpy as np
from history import fetch_candles
from indicators import super_trend

SYM, BASE_TF, LIMIT = "BTC-USDT", "4h", 40000
NOTIONAL = 10_000.0
FEE = 0.05 / 100
GLOBAL = (2.0, 0.5, 2.0)  # TP1, RATIO, SL
TP1S = [1.0, 1.5, 2.0, 2.5, 3.0]
RATIOS = [0.5, 0.6, 0.7, 0.8]
K = 5


def backtest(bc, up_plot, dn_plot, flip_idx, signals, tp1_pct, tp1_ratio, sl_pct):
    closes = [x["c"] for x in bc]; highs = [x["h"] for x in bc]; lows = [x["l"] for x in bc]
    trades = []
    for f in signals:
        i = f["i"]; long = f["type"] == "buy"; entry = closes[i]
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
        tp1_done = False; coins = NOTIONAL / entry; realized = -entry * coins * FEE; closed = False
        for j in range(i + 1, len(bc)):
            if long:
                nl = dn_plot[j]
                if nl is not None and nl > stop: stop = nl
                if lows[j] <= stop:
                    px = stop
                    realized += (px - entry) * coins * (1 - tp1_ratio) if tp1_done else (px - entry) * coins
                    realized -= px * coins * (1 - tp1_ratio) * FEE if tp1_done else px * coins * FEE
                    closed = True; break
                if not tp1_done and highs[j] >= tp1_price:
                    realized += (tp1_price - entry) * coins * tp1_ratio
                    realized -= tp1_price * coins * tp1_ratio * FEE
                    tp1_done = True; stop = entry
            else:
                nl = up_plot[j]
                if nl is not None and nl < stop: stop = nl
                if highs[j] >= stop:
                    px = stop
                    realized += (entry - px) * coins * (1 - tp1_ratio) if tp1_done else (entry - px) * coins
                    realized -= px * coins * (1 - tp1_ratio) * FEE if tp1_done else px * coins * FEE
                    closed = True; break
                if not tp1_done and lows[j] <= tp1_price:
                    realized += (entry - tp1_price) * coins * tp1_ratio
                    realized -= tp1_price * coins * tp1_ratio * FEE
                    tp1_done = True; stop = entry
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
        trades.append(realized)
    return np.array(trades)


def met(t):
    if len(t) == 0:
        return (0, 0, 0, 0)
    wins = t[t > 0]; losses = t[t <= 0]
    eq = np.cumsum(t); peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    pf = wins.sum() / (-losses.sum()) if len(losses) else 99
    return (t.sum() / NOTIONAL * 100, len(wins) / len(t) * 100, pf, dd.min() * 100)


async def main():
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(None, fetch_candles, BASE_TF, LIMIT, SYM)
    bc = [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c} for c in raw]
    st = super_trend([x["o"] for x in bc], [x["h"] for x in bc], [x["l"] for x in bc],
                     [x["c"] for x in bc], periods=10, multiplier=3.0, change_atr=True)
    up, dn = st["up_plot"], st["dn_plot"]
    flip_idx = {f["i"] for f in st["flips"] if f["i"] < len(bc)}
    signals = [f for f in st["flips"] if f["i"] < len(bc)]
    # 按时间分 K 窗
    idx_sorted = sorted(range(len(signals)), key=lambda k: bc[signals[k]["i"]]["ts"])
    folds = np.array_split(idx_sorted, K)

    print(f"{'折':<4}{'训练选参':<14}{'OOS(选参)收益%':>16}{'胜率':>7}{'pf':>7}{'回撤%':>8} | {'OOS(全局2.0/0.5)收益%':>22}{'胜率':>7}{'pf':>7}{'回撤%':>8}")
    all_sel = []; all_glb = []
    for k in range(1, K):
        train_sig = [signals[i] for i in np.concatenate(folds[:k])]
        test_sig = [signals[i] for i in folds[k]]
        # 训练选参（SL固定2.0）
        best = None; best_ret = -1e9
        for tp in TP1S:
            for tr in RATIOS:
                r = backtest(bc, up, dn, flip_idx, train_sig, tp, tr, 2.0)
                ret = r.sum() / NOTIONAL * 100
                if ret > best_ret:
                    best_ret = ret; best = (tp, tr)
        sel = backtest(bc, up, dn, flip_idx, test_sig, best[0], best[1], 2.0)
        glb = backtest(bc, up, dn, flip_idx, test_sig, *GLOBAL)
        sm = met(sel); gm = met(glb)
        all_sel.append(sel); all_glb.append(glb)
        print(f"{k:<4}{(f'TP1={best[0]} R={best[1]}')[:14]:<14}{sm[0]:>16.1f}{sm[1]:>6.1f}%{sm[2]:>7.2f}{sm[3]:>8.1f} | {gm[0]:>22.1f}{gm[1]:>6.1f}%{gm[2]:>7.2f}{gm[3]:>8.1f}"
              f"   (n_test={len(test_sig)})")

    fs = met(np.concatenate(all_sel)); fg = met(np.concatenate(all_glb))
    print(f"\n=== OOS 累计（{K-1} 个测试窗合并）===")
    print(f"训练选参 : 收益={fs[0]:.1f}%  胜率={fs[1]:.1f}%  pf={fs[2]:.2f}  回撤={fs[3]:.1f}%")
    print(f"全局推荐 : 收益={fg[0]:.1f}%  胜率={fg[1]:.1f}%  pf={fg[2]:.2f}  回撤={fg[3]:.1f}%")


asyncio.run(main())
