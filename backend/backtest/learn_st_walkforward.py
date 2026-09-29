# -*- coding: utf-8 -*-
"""
「避开趋势 + 波动缩放」策略 · 前向验证 (walk-forward, 按年切折)
================================================================
防过拟合：每年 t 只使用 t 之前年份的数据来
  1) 选定要砍掉的"最差 regime"（按训练窗内 mean_net 最低，n>=10）
  2) 计算 ATR 缩放中枢（训练窗 ATR 中位数）
然后把这些**只来自过去的决策**应用到年份 t 的样本外交易上。

对比四种 OOS 口径（均按年份内顺序复利，跨年顺序拼接）：
  A  全样本固定仓位
  B  全样本波动缩放(1/ATR%, [0.25,2.0])
  C  regime过滤(砍训练窗最差) + 固定
  D  regime过滤 + 波动缩放   <- 我们要验证的主策略
产物：backtest/st_walkforward.json
用法：cd backend && python backtest/learn_st_walkforward.py
"""
from __future__ import annotations
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from indicators import super_trend, ta_atr

BASE = Path(__file__).resolve().parent
candles = json.loads(Path(BASE / "btc_1h_full.json").read_text(encoding="utf-8"))["base"]
o = [c["o"] for c in candles]; h = [c["h"] for c in candles]
l = [c["l"] for c in candles]; cl = [c["c"] for c in candles]
BY = 24 * 365; FEE = 0.05

st = super_trend(o, h, l, cl, periods=10, multiplier=3.0, change_atr=True)
flips = sorted(st["flips"], key=lambda f: f["i"])
atr = ta_atr(h, l, cl, 14)

ms_by_ts = {}
for r in csv.DictReader(open(BASE / "st_signals_full.csv", encoding="utf-8-sig")):
    try:
        ms_by_ts[int(r["ts"])] = int(r["market_state"])
    except Exception:
        pass
STATE_LABEL = {0: "震荡", 1: "趋势", 2: "启动", 3: "过热"}

trades = []
for k in range(len(flips) - 1):
    i, j = flips[k]["i"], flips[k + 1]["i"]
    if j <= i:
        continue
    side = 1 if flips[k]["type"] == "buy" else -1
    net = (cl[j] - cl[i]) / cl[i] * 100 * side - 2 * FEE
    ap = atr[i] / cl[i] * 100 if atr[i] and cl[i] else None
    dt = datetime.fromtimestamp(candles[i]["ts"] / 1000, tz=timezone.utc)
    trades.append({"net": net, "atr": ap, "bars": j - i,
                   "year": dt.year, "ms": ms_by_ts.get(candles[i]["ts"], -1)})
years = sorted(set(t["year"] for t in trades))


def sim(items, size_of):
    n = len(items)
    if n < 3:
        return {"n": n, "total": None, "sharpe": None, "mdd": None}
    eq = 1.0; peak = 1.0; dds = [0.0]; per = np.zeros(n)
    for k, t in enumerate(items):
        r = size_of(t) * t["net"] / 100.0
        per[k] = r; eq *= (1 + r); peak = max(peak, eq); dds.append(eq / peak - 1)
    curve = np.array(dds)
    total = (eq - 1) * 100
    held = sum(t["bars"] for t in items); span = max(held, 1) / BY
    sharpe = (per.mean() / per.std() * np.sqrt(n / span)) if per.std() > 0 else 0.0
    return {"n": n, "total": round(float(total), 1), "sharpe": round(float(sharpe), 2),
            "mdd": round(float(curve.min() * 100), 1)}


folds = []
oos_D = []   # 主策略 OOS 交易（按年拼接）
oos_B = []   # 全样本缩放 OOS
oos_A = []   # 全样本固定 OOS
oos_C = []   # regime过滤+固定 OOS

for y in years[1:]:                      # 第一年无训练数据，跳过
    train = [t for t in trades if t["year"] < y]
    test = [t for t in trades if t["year"] == y]
    # 1) 训练窗内选最差 regime（mean_net 最低，n>=10）
    by_state = {}
    for t in train:
        by_state.setdefault(t["ms"], []).append(t["net"])
    cand = {s: v for s, v in by_state.items() if len(v) >= 10}
    dropped = min(cand, key=lambda s: np.mean(cand[s])) if cand else -1
    # 2) 训练窗 ATR 中位
    med = np.median([t["atr"] for t in train if t["atr"]])

    def sz_scaled(t):
        return np.clip(med / t["atr"], 0.25, 2.0) if t["atr"] else 1.0

    kept = [t for t in test if t["ms"] != dropped]
    d = sim(kept, sz_scaled)                 # 主策略 D (过滤+缩放)
    c = sim(kept, lambda t: 1.0)             # C (过滤, 固定)
    b = sim(test, sz_scaled)                 # B (全缩放)
    a = sim(test, lambda t: 1.0)             # A (全固定)
    folds.append({"year": y, "dropped_state": dropped,
                  "dropped_label": STATE_LABEL.get(dropped, str(dropped)),
                  "n_test": len(test), "n_kept": len(kept),
                  "A": a["total"], "B": b["total"], "B_sharpe": b["sharpe"],
                  "C": c["total"], "D": d["total"], "D_sharpe": d["sharpe"], "D_mdd": d["mdd"]})

    oos_D += kept
    oos_C += kept
    oos_B += test
    oos_A += test

def scaled_size(items):
    m = np.median([t["atr"] for t in items if t["atr"]])
    def f(t):
        return np.clip(m / t["atr"], 0.25, 2.0) if t["atr"] else 1.0
    return f

agg = {
    "A_all_fixed": sim(oos_A, lambda t: 1.0),
    "B_all_scaled": sim(oos_B, scaled_size(oos_B)),
    "C_filter_fixed": sim(oos_C, lambda t: 1.0),
    "D_filter_scaled": sim(oos_D, scaled_size(oos_D)),
}

# 样本内参照（全数据、砍 state1、缩放）
med_in = np.median([t["atr"] for t in trades if t["atr"]])
insample_D = sim([t for t in trades if t["ms"] != 1],
                 lambda t: np.clip(med_in / t["atr"], 0.25, 2.0) if t["atr"] else 1.0)

out = {
    "meta": {
        "oos_years": [f["year"] for f in folds],
        "desc": "每年仅用此前年份决定砍哪个regime与缩放中枢；D=砍最差regime+1/ATR缩放[0.25,2.0]",
        "insample_D_total": insample_D["total"], "insample_D_sharpe": insample_D["sharpe"],
    },
    "folds": folds,
    "aggregate_oos": {
        "A_all_fixed": agg["A_all_fixed"],
        "B_all_scaled": agg["B_all_scaled"],
        "C_filter_fixed": agg["C_filter_fixed"],
        "D_filter_scaled": agg["D_filter_scaled"],
    },
}
(BASE / "st_walkforward.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print("写出 ->", BASE / "st_walkforward.json")
print("样本内 D(砍趋势+缩放): 累计 %s%% 夏普 %s" % (insample_D["total"], insample_D["sharpe"]))
print("\n年份  砍regime  n  A固定  B缩放  C过滤固  D过滤缩")
for f in folds:
    print("%d  %s(%d)  %d  %s  %s  %s  %s" % (
        f["year"], f["dropped_label"], f["dropped_state"], f["n_test"],
        f["A"], f["B"], f["C"], f["D"]))
print("\n=== OOS 跨年聚合 ===")
print("A 全固定:    ", agg["A_all_fixed"])
print("B 全缩放:    ", agg["B_all_scaled"])
print("C 过滤固定:  ", agg["C_filter_fixed"])
print("D 过滤+缩放: ", agg["D_filter_scaled"])
