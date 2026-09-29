# -*- coding: utf-8 -*-
"""按真实出场净收益分桶，看盈利/亏损各档是否有可分离的有利特征。"""
from __future__ import annotations
import json, csv
from pathlib import Path
import numpy as np
from indicators import super_trend, ta_atr

BASE = Path(__file__).resolve().parent
candles = json.loads((BASE / "backtest" / "btc_1h_full.json").read_text(encoding="utf-8"))["base"]
o = [c["o"] for c in candles]; h = [c["h"] for c in candles]
l = [c["l"] for c in candles]; cl = [c["c"] for c in candles]
st = super_trend(o, h, l, cl, periods=10, multiplier=3.0, change_atr=True)
flips = sorted(st["flips"], key=lambda f: f["i"])
FEE = 0.05
net_by_ts = {}
for k in range(len(flips) - 1):
    i, j = flips[k]["i"], flips[k + 1]["i"]
    if j <= i: continue
    side = 1 if flips[k]["type"] == "buy" else -1
    gross = (cl[j] - cl[i]) / cl[i] * 100 * side
    net_by_ts[candles[i]["ts"]] = gross - 2 * FEE

# 读 CSV 特征，按 ts 对齐
rows = list(csv.DictReader(open(BASE / "backtest" / "st_signals_full.csv", encoding="utf-8-sig")))
FEATS = ["ATR_percent","ATR_percentile_100","ATR_change_5","range_width_20","range_width_50",
         "return_5","return_10","return_20","distance_low_20","distance_high_20","mom5",
         "momentum_change","st_distance_ATR","st_same_direction_count","flip_20","flip_50",
         "flip_100","ADX14","ER20","break_high_20","break_low_20","break_strength",
         "volume_ratio","volume_change_5"]
data = []
for r in rows:
    ts = int(r["ts"])
    if ts not in net_by_ts: continue
    rec = {"net": net_by_ts[ts]}
    for f in FEATS:
        try: rec[f] = float(r[f])
        except: rec[f] = np.nan
    data.append(rec)
data = [d for d in data if not any(np.isnan(v) for v in d.values())]
nets = np.array([d["net"] for d in data])
print(f"对齐交易 {len(data)} 笔 · 净收益均值 {nets.mean():+.3f}%  胜率 {(nets>0).mean()*100:.1f}%")

def bucket(n):
    if n >= 5: return "净>=5% (大赢)"
    if n >= 3: return "3~5%"
    if n >= 2: return "2~3%"
    if n >  0: return "0~2%"
    if n > -2: return "-2~0%"
    if n > -5: return "-5~-2%"
    return "<=-5% (大亏)"
order = ["净>=5% (大赢)","3~5%","2~3%","0~2%","-2~0%","-5~-2%","<=-5% (大亏)"]
groups = {b: [d for d in data if bucket(d["net"]) == b] for b in order}

print("\n===== 各盈利档 特征均值 =====")
hdr = "档位".ljust(14) + "n".rjust(5) + "均净".rjust(8) + "".join(f.split("_")[0][:5].rjust(8) for f in FEATS[:8])
print(hdr)
for b in order:
    g = groups[b]
    if not g: continue
    ns = [d["net"] for d in g]
    line = b.ljust(14) + f"{len(g):5d}" + f"{np.mean(ns):+8.2f}"
    for f in FEATS[:8]:
        line += f"{np.mean([d[f] for d in g]):8.2f}"
    print(line)

print("\n===== 每个特征 与 净收益 的相关系数(Pearson) =====")
corr = []
for f in FEATS:
    xs = np.array([d[f] for d in data]); ys = nets
    if xs.std() > 0:
        corr.append((f, np.corrcoef(xs, ys)[0,1]))
corr.sort(key=lambda x: -abs(x[1]))
for f, c in corr:
    print(f"  {f:22s} corr={c:+.3f}")

print("\n===== 大赢家(net>=5) vs 大亏家(net<=-5) 特征均值差 =====")
win = groups["净>=5% (大赢)"]; los = groups["<=-5% (大亏)"]
if win and los:
    for f in FEATS:
        mw = np.mean([d[f] for d in win]); ml = np.mean([d[f] for d in los])
        sw, sl = np.std([d[f] for d in win]), np.std([d[f] for d in los])
        sep = (mw - ml) / ((sw + sl)/2 + 1e-9)   # 标准化差
        print(f"  {f:22s} 赢={mw:7.2f}  亏={ml:7.2f}  标准化差={sep:+.2f}")
