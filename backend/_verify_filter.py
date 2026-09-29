# -*- coding: utf-8 -*-
"""验证：把 ATR_percent 阈值当成 ST 信号过滤器，真实出场(flip-to-flip)下净赚还是净亏。"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
from indicators import super_trend, ta_atr

BASE = Path(__file__).resolve().parent
candles = json.loads((BASE / "backtest" / "btc_1h_full.json").read_text(encoding="utf-8"))["base"]
o = [c["o"] for c in candles]; h = [c["h"] for c in candles]
l = [c["l"] for c in candles]; cl = [c["c"] for c in candles]
ts = [c["ts"] for c in candles]
N = len(candles); BY = 24 * 365
st = super_trend(o, h, l, cl, periods=10, multiplier=3.0, change_atr=True)
flips = sorted(st["flips"], key=lambda f: f["i"])
atr = ta_atr(h, l, cl, 14)

FEE = 0.05  # 单边 0.05%，往返 2*FEE
trades = []
for k in range(len(flips) - 1):
    i, j = flips[k]["i"], flips[k + 1]["i"]
    if j <= i:
        continue
    side = 1 if flips[k]["type"] == "buy" else -1
    e, x = cl[i], cl[j]
    gross = (x - e) / e * 100 * side
    ap = atr[i] / cl[i] * 100 if atr[i] and cl[i] else None
    trades.append({"i": i, "ts": ts[i], "side": side, "gross": gross,
                   "net": gross - 2 * FEE, "atr": ap, "bars": j - i})
print(f"数据 BTC-USDT 1h · {N} 根 ≈ {N/BY:.2f} 年 · 完成交易 {len(trades)} 笔")

def equity(tr):
    eq = 1.0; curve = [eq]; dds = [0.0]
    peak = 1.0
    for t in tr:
        eq *= (1 + t["net"] / 100.0)
        peak = max(peak, eq)
        dds.append(eq / peak - 1)
        curve.append(eq)
    return eq, min(dds) * 100

def report(name, tr):
    if not tr:
        print(f"\n[{name}] 无交易"); return
    eq, mdd = equity(tr)
    nets = np.array([t["net"] for t in tr])
    wins = (nets > 0).mean() * 100
    big = (nets >= 5).sum()          # 净赚>=5% 的大赢家
    print(f"\n[{name}]  n={len(tr):4d}  胜率={wins:5.1f}%  单笔均净={nets.mean():+.3f}%  "
          f"累计净收益={ (eq-1)*100:+.1f}%  MaxDD={mdd:+.1f}%  大赢家(>=5%)={big}")
    return eq, mdd, wins, big

ALL = trades
report("① 全样本(不过滤)", ALL)

# 三套过滤方案
keep138 = [t for t in ALL if t["atr"] is not None and t["atr"] >= 1.38]
keep100 = [t for t in ALL if t["atr"] is not None and t["atr"] >= 1.0]
avoid06 = [t for t in ALL if t["atr"] is not None and t["atr"] >= 0.6]
report("② 只留 ATR%>=1.38 (提议过滤器)", keep138)
report("③ 只留 ATR%>=1.00", keep100)
report("④ 只砍 ATR%<0.60 尾巴", avoid06)

# 关键：大赢家被丢了多少？
big_all = [t for t in ALL if t["net"] >= 5]
big_kept = [t for t in keep138 if t["net"] >= 5]
big_lost = [t for t in big_all if t not in big_kept]
print(f"\n=== 大赢家(净>=5%) 召回分析 ===")
print(f"全样本大赢家 {len(big_all)} 笔；只留ATR>=1.38 接住 {len(big_kept)} 笔，"
      f"丢掉 {len(big_all)-len(big_kept)} 笔 ({100*(len(big_all)-len(big_kept))/max(len(big_all),1):.0f}%)")
print(f"全样本累计净收益 {(equity(ALL)[0]-1)*100:+.1f}%  →  过滤后 {(equity(keep138)[0]-1)*100:+.1f}%")
