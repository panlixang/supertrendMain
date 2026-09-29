# -*- coding: utf-8 -*-
"""
ST 信号「真实盈亏 / 波动风控」离线学习
=====================================
把之前验证过的两条结论固化成静态 JSON，供前端策略学习页 ⑤ 区块读取：

  1) 分桶特征：按真实出场净收益(net, flip-to-flip, 往返费 0.05%) 分档
     (净>=5% / 3~5% / 2~3% / 0~2% / -2~0% / -5~-2% / <=-5%)，
     看盈利档与亏损档的入场特征均值差异。
  2) 相关性：每个入场特征与净收益的 Pearson 相关（判断有没有“盈利特征 alpha”）。
  3) 大赢 vs 大亏：net>=5 与 net<=-5 的特征均值差（标准化）。
  4) A/B 缩放对比：固定仓位 vs ATR 倒数缩放(1/ATR%, [0.25,2.0]) 的
     累计 / 夏普 / 最大回撤 / Calmar。

数据源：
  - backtest/btc_1h_full.json            （BTC 1h K线）
  - backtest/st_signals_full.csv         （每笔信号入场特征，按 ts 对齐）
  - indicators.super_trend / ta_atr      （与实盘一致的 ST 出场）

产物：backtest/st_pnl_risk.json
用法：cd backend && python backtest/learn_st_pnl.py
"""
from __future__ import annotations
import csv
import json
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # backend/
from indicators import super_trend, ta_atr

BASE = Path(__file__).resolve().parent
candles = json.loads((BASE / "btc_1h_full.json").read_text(encoding="utf-8"))["base"]
o = [c["o"] for c in candles]; h = [c["h"] for c in candles]
l = [c["l"] for c in candles]; cl = [c["c"] for c in candles]
N = len(candles); BY = 24 * 365
FEE = 0.05

# ── ST 翻向出场交易（与实盘口径一致）──────────────────────────
st = super_trend(o, h, l, cl, periods=10, multiplier=3.0, change_atr=True)
flips = sorted(st["flips"], key=lambda f: f["i"])
atr = ta_atr(h, l, cl, 14)

net_by_ts = {}
atr_by_i = {}
for k in range(len(flips) - 1):
    i, j = flips[k]["i"], flips[k + 1]["i"]
    if j <= i:
        continue
    side = 1 if flips[k]["type"] == "buy" else -1
    gross = (cl[j] - cl[i]) / cl[i] * 100 * side
    ts = candles[i]["ts"]
    net_by_ts[ts] = gross - 2 * FEE
    atr_by_i[i] = atr[i] / cl[i] * 100 if atr[i] and cl[i] else None

# ── 入场特征（CSV）按 ts 对齐 ────────────────────────────────
FEATS = ["ATR_percent", "ATR_percentile_100", "ATR_change_5", "range_width_20", "range_width_50",
         "return_5", "return_10", "return_20", "distance_low_20", "distance_high_20", "mom5",
         "momentum_change", "st_distance_ATR", "st_same_direction_count", "flip_20", "flip_50",
         "flip_100", "ADX14", "ER20", "break_high_20", "break_low_20", "break_strength",
         "volume_ratio", "volume_change_5"]
BUCKET_FEATS = ["ATR_percent", "range_width_20", "range_width_50", "return_5", "return_10",
                "distance_high_20", "distance_low_20", "mom5", "momentum_change",
                "st_distance_ATR", "flip_20", "ADX14"]

rows = list(csv.DictReader(open(BASE / "st_signals_full.csv", encoding="utf-8-sig")))
data = []
for r in rows:
    ts = int(r["ts"])
    if ts not in net_by_ts:
        continue
    rec = {"net": net_by_ts[ts]}
    ok = True
    for f in FEATS:
        try:
            rec[f] = float(r[f])
        except Exception:
            ok = False
            break
    if ok:
        data.append(rec)
data = [d for d in data if not any(np.isnan(v) for v in d.values())]
nets = np.array([d["net"] for d in data])
print(f"对齐交易 {len(data)} 笔 · 均净 {nets.mean():+.3f}% · 胜率 {(nets > 0).mean() * 100:.1f}%")

# ── 分桶 ────────────────────────────────────────────────────
def bucket(n):
    if n >= 5:  return "净≥5% (大赢)"
    if n >= 3:  return "3~5%"
    if n >= 2:  return "2~3%"
    if n > 0:   return "0~2%"
    if n > -2:  return "-2~0%"
    if n > -5:  return "-5~-2%"
    return "≤-5% (大亏)"
ORDER = ["净≥5% (大赢)", "3~5%", "2~3%", "0~2%", "-2~0%", "-5~-2%", "≤-5% (大亏)"]
buckets = []
for b in ORDER:
    g = [d for d in data if bucket(d["net"]) == b]
    if not g:
        continue
    gn = np.array([d["net"] for d in g])
    means = {f: round(float(np.mean([d[f] for d in g])), 3) for f in BUCKET_FEATS}
    buckets.append({"label": b, "n": len(g), "mean_net": round(float(gn.mean()), 2), "means": means})

# ── 相关性（与净收益）────────────────────────────────────────
corr = []
for f in FEATS:
    xs = np.array([d[f] for d in data])
    if xs.std() > 0:
        corr.append({"feature": f, "corr": round(float(np.corrcoef(xs, nets)[0, 1]), 3)})
corr.sort(key=lambda x: -abs(x["corr"]))

# ── 大赢 vs 大亏 ────────────────────────────────────────────
win = [d for d in data if d["net"] >= 5]
los = [d for d in data if d["net"] <= -5]
wl = []
if win and los:
    for f in FEATS:
        mw = np.mean([d[f] for d in win]); ml = np.mean([d[f] for d in los])
        sw = np.std([d[f] for d in win]); sl = np.std([d[f] for d in los])
        sep = (mw - ml) / ((sw + sl) / 2 + 1e-9)
        wl.append({"feature": f, "win": round(float(mw), 3), "lose": round(float(ml), 3),
                   "sep": round(float(sep), 2)})
wl.sort(key=lambda x: -abs(x["sep"]))

# ── A/B 缩放对比 ────────────────────────────────────────────
trades = []
for k in range(len(flips) - 1):
    i, j = flips[k]["i"], flips[k + 1]["i"]
    if j <= i:
        continue
    ap = atr_by_i.get(i)
    if ap is None:
        continue
    side = 1 if flips[k]["type"] == "buy" else -1
    gross = (cl[j] - cl[i]) / cl[i] * 100 * side
    trades.append({"net": gross - 2 * FEE, "atr": ap, "bars": j - i})
print(f"缩放样本 {len(trades)} 笔")

def scaling_stats(sizes):
    eq = 1.0; peak = 1.0; dds = [0.0]; per = np.zeros(len(trades))
    for k, t in enumerate(trades):
        r = sizes[k] * t["net"] / 100.0
        per[k] = r
        eq *= (1 + r); peak = max(peak, eq); dds.append(eq / peak - 1)
    curve = np.array(dds)
    n = len(trades); held = sum(t["bars"] for t in trades); span = max(held, 1) / BY
    total = (eq - 1) * 100
    cagr = ((eq) ** (1 / span) - 1) * 100 if span > 0 and eq > 0 else -100
    tr_per_year = n / span
    sharpe = (per.mean() / per.std() * np.sqrt(tr_per_year)) if per.std() > 0 else 0.0
    mdd = curve.min() * 100
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0
    return {"avg_size": round(float(sizes.mean()), 2), "total": round(float(total), 1),
            "cagr": round(float(cagr), 1), "sharpe": round(float(sharpe), 2),
            "mdd": round(float(mdd), 1), "calmar": round(float(calmar), 2), "n": n}

med = np.median([t["atr"] for t in trades])
sizes_A = np.ones(len(trades))
sizes_B = np.clip([med / t["atr"] for t in trades], 0.25, 2.0)
A = scaling_stats(sizes_A); B = scaling_stats(sizes_B)

out = {
    "meta": {
        "n_trades": len(data), "mean_net": round(float(nets.mean()), 3),
        "win_rate": round(float((nets > 0).mean()), 3), "fee": FEE,
        "atr_median": round(float(med), 2),
        "data_from": min(net_by_ts), "data_to": max(net_by_ts),
        "desc": "ST(10,3) 翻向出场(flip-to-flip)，往返费 0.05%；特征来自入场 bar；"
                "B 方案 size∝1/ATR% 中位数归一，截断[0.25,2.0]。",
    },
    "bucket_features": BUCKET_FEATS,
    "buckets": buckets,
    "corr_with_net": corr,
    "winner_vs_loser": wl,
    "scaling": {
        "A": {**A, "tag": "固定仓位 size=1"},
        "B": {**B, "tag": "ATR倒数缩放 1/ATR%[0.25,2.0]"},
        "improve": {"sharpe": round(B["sharpe"] - A["sharpe"], 2),
                    "mdd_abs": round(abs(A["mdd"]) - abs(B["mdd"]), 1),
                    "calmar": round(B["calmar"] - A["calmar"], 2)},
    },
    "conclusions": [
        "特征能预测“会不会有大摆动”，但几乎预测不了“这笔赚不赚钱”：所有 |corr(特征,净收益)| < 0.13。",
        "盈利档集中在中等波动(ATR≈0.8~1.0)；最大亏损档 ATR 最高(≈1.87)、区间宽度翻倍以上——极端波动是亏损温床。",
        "按 ATR 过滤信号会越滤越亏（丢 79% 大赢家、单笔期望恶化）；但按 1/ATR 缩放仓位明显改善风险调整后收益（夏普 -0.43→+0.18、回撤 -90%→-66%）。",
        "结论落地：这条线不能做信号过滤器，只能做波动仓位管理 / 风控规则（避开极端波动段）。",
    ],
}
(BASE / "st_pnl_risk.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print("写出 ->", BASE / "st_pnl_risk.json")
print(f"缩放 A 累计{out['scaling']['A']['total']}% 夏普{out['scaling']['A']['sharpe']} 回撤{out['scaling']['A']['mdd']}%")
print(f"缩放 B 累计{out['scaling']['B']['total']}% 夏普{out['scaling']['B']['sharpe']} 回撤{out['scaling']['B']['mdd']}%")
