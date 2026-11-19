# -*- coding: utf-8 -*-
"""CL 形态识别页口径 + V3 过滤回测对比。

口径对齐 pattern_trade.py：
- 信号：基础 SuperTrend(10,3.0) 1h 翻转（与形态识别页一致）
- 出场：tp1 1.5% 平 70% + 剩余到反向信号平仓（StrategyLearning 标签）
- V3：signal_v3.v3_decide（与实盘 _v3_allow 同一模块）
对比：全部做（关 v3） vs 只做 v3 通过（开 v3），并列出被 v3 拦掉的笔盈亏。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                      # backend 根：signal_v3 在此
sys.path.insert(0, os.path.join(HERE, "backtest"))  # engine 在此

from engine import _load_tf, super_trend
import signal_v3

ST_PERIODS = 10
ST_MULT = 3.0
TP = 0.015  # tp1 1.5%


def label(cs, flips, pos):
    f = flips[pos]
    i = f["i"]
    typ = f["type"]
    sd = 1 if typ == "buy" else -1
    entry = cs[i]["c"]
    # 下一个反向翻转作为平仓点
    j = len(cs) - 1
    for g in flips[pos + 1:]:
        if g["type"] != typ:
            j = g["i"]
            break
    touched = False
    for k in range(i + 1, j + 1):
        if sd > 0 and cs[k]["h"] >= entry * (1 + TP):
            touched = True
            break
        if sd < 0 and cs[k]["l"] <= entry * (1 - TP):
            touched = True
            break
    rev = ((cs[j]["c"] - entry) / entry * 100 if sd > 0
           else (entry - cs[j]["c"]) / entry * 100)
    pnl = 0.7 * TP * 100 + 0.3 * rev if touched else rev
    return sd, pnl, pnl > 0


def stat(rows):
    n = len(rows)
    if not n:
        return (0, 0.0, 0.0)
    pnl = round(sum(r["pnl"] for r in rows), 2)
    win = sum(1 for r in rows if r["win"])
    return (n, pnl, round(win / n * 100, 1))


def main():
    cs = _load_tf("CL-USDT", "1h")
    cs4 = _load_tf("CL-USDT", "4h")
    print(f"CL 本地数据: 1h={len(cs)}根 4h={len(cs4)}根")

    st = super_trend([c["o"] for c in cs], [c["h"] for c in cs],
                     [c["l"] for c in cs], [c["c"] for c in cs],
                     periods=ST_PERIODS, multiplier=ST_MULT, change_atr=True)
    flips = st.get("flips") or []
    print(f"ST({ST_PERIODS}x{ST_MULT}) 翻转信号: {len(flips)} 笔")

    rows = []
    for p in range(len(flips)):
        f = flips[p]
        i = f["i"]
        if i < 120 or i >= len(cs) - 5:
            continue
        typ = f["type"]
        sd = 1 if typ == "buy" else -1
        _, pnl, win = label(cs, flips, p)
        feats = signal_v3.features_from_candles(
            cs, i, sd, cs4, st_periods=ST_PERIODS, st_mult=ST_MULT)
        if feats:
            dec = signal_v3.v3_decide(sd, feats)
            v3pass = bool(dec.get("execute"))
            path = dec.get("path")
        else:
            v3pass = True
            path = "特征不足放行"
        rows.append({"typ": typ, "pnl": pnl, "win": win,
                     "v3pass": v3pass, "path": path})

    print("\n=== 形态识别页口径：开 V3 vs 关 V3 ===")
    print(f"全部  (关v3): {stat(rows)}  笔数/累计收益%/胜率")
    v3 = [r for r in rows if r["v3pass"]]
    blk = [r for r in rows if not r["v3pass"]]
    print(f"v3通过(开v3): {stat(v3)}")
    print(f"v3拦截     : {stat(blk)}")

    # 被 v3 拦掉的分多空
    for side in ("buy", "sell"):
        sub = [r for r in blk if r["typ"] == side]
        tag = "多" if side == "buy" else "空"
        print(f"  └ 被v3拦掉的{tag}头: {stat(sub)}")

    # v3 各路径放行分布（通过的）
    from collections import Counter
    pc = Counter(r["path"] for r in v3)
    print(f"\nv3通过路径分布: {dict(pc)}")


if __name__ == "__main__":
    main()
