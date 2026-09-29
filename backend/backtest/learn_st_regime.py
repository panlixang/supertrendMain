# -*- coding: utf-8 -*-
"""
ST 信号「分年份 / regime 稳定性」离线学习
========================================
验证两条结论是否随时段成立：
  1) 方向零 alpha：ST(10,3) 翻向出场，每年 / 每个市场状态下是否都近零或负期望？
  2) 波动缩放有效：固定仓位(A) vs ATR倒数缩放(B)，每年 / 每个状态下 B 是否都更好？

数据源：backtest/btc_1h_full.json + st_signals_full.csv(market_state) + indicators
产物：backtest/st_regime.json
用法：cd backend && python backtest/learn_st_regime.py
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
candles = json.loads((BASE / "btc_1h_full.json").read_text(encoding="utf-8"))["base"]
o = [c["o"] for c in candles]; h = [c["h"] for c in candles]
l = [c["l"] for c in candles]; cl = [c["c"] for c in candles]
N = len(candles); BY = 24 * 365
FEE = 0.05

st = super_trend(o, h, l, cl, periods=10, multiplier=3.0, change_atr=True)
flips = sorted(st["flips"], key=lambda f: f["i"])
atr = ta_atr(h, l, cl, 14)

# 信号 market_state 按 ts 对齐
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
    gross = (cl[j] - cl[i]) / cl[i] * 100 * side
    net = gross - 2 * FEE
    ap = atr[i] / cl[i] * 100 if atr[i] and cl[i] else None
    dt = datetime.fromtimestamp(candles[i]["ts"] / 1000, tz=timezone.utc)
    year = dt.year
    ms = ms_by_ts.get(candles[i]["ts"], -1)
    trades.append({"net": net, "atr": ap, "bars": j - i, "year": year, "ms": ms})

print(f"交易 {len(trades)} 笔 · 年份 {sorted(set(t['year'] for t in trades))}")

def stats(sub):
    n = len(sub)
    if n < 3:
        return {"n": n, "total": None, "sharpe": None, "mdd": None, "calmar": None,
                "win_rate": None, "mean_net": None}
    eq = 1.0; peak = 1.0; dds = [0.0]; per = np.zeros(n)
    for k, t in enumerate(sub):
        r = t["net"] / 100.0
        per[k] = r
        eq *= (1 + r); peak = max(peak, eq); dds.append(eq / peak - 1)
    curve = np.array(dds)
    total = (eq - 1) * 100
    held = sum(t["bars"] for t in sub); span = max(held, 1) / BY
    tr_per_year = n / span
    sharpe = (per.mean() / per.std() * np.sqrt(tr_per_year)) if per.std() > 0 else 0.0
    mdd = curve.min() * 100
    return {"n": n, "total": round(float(total), 1), "sharpe": round(float(sharpe), 2),
            "mdd": round(float(mdd), 1),
            "win_rate": round(float((np.array([t["net"] for t in sub]) > 0).mean()), 3),
            "mean_net": round(float(np.mean([t["net"] for t in sub])), 3)}

med = np.median([t["atr"] for t in trades if t["atr"]])
def make(sizes_for):
    A = stats([{**t} for t in trades])
    B = stats([{**t, "net": t["net"] * sizes_for(t)} for t in trades])
    return A, B

def subset_stats(items):
    A = stats(items)
    sized = []
    for t in items:
        s = np.clip(med / t["atr"], 0.25, 2.0) if t["atr"] else 1.0
        sized.append({**t, "net": t["net"] * s})
    B = stats(sized)
    b_better = (B["sharpe"] is not None and A["sharpe"] is not None and B["sharpe"] > A["sharpe"]) or \
               (B["total"] is not None and A["total"] is not None and B["total"] > A["total"])
    return {**A, "B_total": B["total"], "B_sharpe": B["sharpe"], "B_mdd": B["mdd"],
            "b_better": bool(b_better)}

years = sorted(set(t["year"] for t in trades))
by_year = []
for y in years:
    sub = [t for t in trades if t["year"] == y]
    by_year.append({"year": y, **subset_stats(sub)})

regimes = sorted(set(t["ms"] for t in trades if t["ms"] >= 0))
by_regime = []
for s in regimes:
    sub = [t for t in trades if t["ms"] == s]
    by_regime.append({"state": s, "label": STATE_LABEL.get(s, str(s)), **subset_stats(sub)})

# 全局对照
A_all, B_all = make(lambda t: 1.0)
_, B_all2 = make(lambda t: np.clip(med / t["atr"], 0.25, 2.0) if t["atr"] else 1.0)
zero_alpha_years = [y["year"] for y in by_year if (y["mean_net"] or 0) < 0]
scaling_helps_years = sum(1 for y in by_year if y["b_better"])
scaling_helps_regimes = sum(1 for r in by_regime if r["b_better"])

out = {
    "meta": {
        "n_trades": len(trades), "atr_median": round(float(med), 2),
        "desc": "ST(10,3) 翻向出场；A=固定仓位，B=ATR倒数缩放1/ATR%[0.25,2.0](全局中位归一)；"
                "每年/状态独立复利，不跨年滚动。",
        "global_A": {"total": A_all["total"], "sharpe": A_all["sharpe"], "mdd": A_all["mdd"]},
        "global_B": {"total": B_all2["total"], "sharpe": B_all2["sharpe"], "mdd": B_all2["mdd"]},
    },
    "by_year": by_year,
    "by_regime": by_regime,
    "summary": {
        "zero_alpha_all_years": len(zero_alpha_years) == len(years),
        "years_negative_mean_net": zero_alpha_years,
        "scaling_helps_years": scaling_helps_years,
        "n_years": len(years),
        "scaling_helps_regimes": scaling_helps_regimes,
        "n_regimes": len(regimes),
    },
}
(BASE / "st_regime.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print("写出 ->", BASE / "st_regime.json")
print("全局 A 累计%d%% 夏普%s 回撤%d%% | B 累计%d%% 夏普%s 回撤%d%%" % (
    A_all["total"], A_all["sharpe"], A_all["mdd"], B_all2["total"], B_all2["sharpe"], B_all2["mdd"]))
print("零alpha年份:", zero_alpha_years, " 缩放有益年:", scaling_helps_years, "/", len(years))
