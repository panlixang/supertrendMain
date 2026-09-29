# -*- coding: utf-8 -*-
"""波动率缩放验证：固定仓位(A) vs ATR倒数缩放(B)，看风险调整后收益。"""
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
FEE = 0.05
trades = []
for k in range(len(flips) - 1):
    i, j = flips[k]["i"], flips[k + 1]["i"]
    if j <= i: continue
    side = 1 if flips[k]["type"] == "buy" else -1
    gross = (cl[j] - cl[i]) / cl[i] * 100 * side
    ap = atr[i] / cl[i] * 100 if atr[i] and cl[i] else None
    trades.append({"net": gross - 2 * FEE, "atr": ap, "bars": j - i})
trades = [t for t in trades if t["atr"] is not None]
print(f"交易 {len(trades)} 笔 · ATR% 中位={np.median([t['atr'] for t in trades]):.2f}%")

# 缩放方案
FLOOR, CAP = 0.25, 2.0
med = np.median([t["atr"] for t in trades])
sizes_A = np.ones(len(trades))
sizes_B = np.clip([med / t["atr"] for t in trades], FLOOR, CAP)

def stats(sizes, tag):
    eq = 1.0; curve = [eq]; peak = 1.0; dds = [0.0]
    per = np.zeros(len(trades))
    for k, t in enumerate(trades):
        r = sizes[k] * t["net"] / 100.0
        per[k] = r
        eq *= (1 + r); peak = max(peak, eq)
        dds.append(eq / peak - 1); curve.append(eq)
    curve = np.array(curve); dds = np.array(dds)
    n = len(trades); held = sum(t["bars"] for t in trades); span = max(held, 1) / BY
    total = (eq - 1) * 100
    cagr = ((eq) ** (1 / span) - 1) * 100 if span > 0 and eq > 0 else -100
    tr_per_year = n / span
    sharpe = (per.mean() / per.std() * np.sqrt(tr_per_year)) if per.std() > 0 else 0.0
    mdd = dds.min() * 100
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0
    avg_size = sizes.mean()
    print(f"\n[{tag}] 均仓位={avg_size:.2f}  累计={total:+.1f}%  CAGR={cagr:+.1f}%  "
          f"夏普={sharpe:.2f}  MaxDD={mdd:+.1f}%  Calmar={calmar:.2f}")
    return dict(total=total, cagr=cagr, sharpe=sharpe, mdd=mdd, calmar=calmar, avg_size=avg_size)

print("=" * 60)
a = stats(sizes_A, "A 固定仓位 size=1")
b = stats(sizes_B, "B ATR倒数缩放 1/ATR%[0.25,2.0]")
print("=" * 60)
print(f"夏普改善: {b['sharpe']-a['sharpe']:+.2f}   回撤改善(MaxDD绝对值减小): "
      f"{abs(a['mdd'])-abs(b['mdd']):+.1f}pt   Calmar: {a['calmar']:.2f} -> {b['calmar']:.2f}")
print("注：基准策略方向零alpha，缩放不改变期望正负，只看风险控制是否更好。")
