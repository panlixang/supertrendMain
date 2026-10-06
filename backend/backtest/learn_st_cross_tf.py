# -*- coding: utf-8 -*-
"""跨周期稳健性：1h 验证出的「只交易 震荡(state0) + 1/ATR缩放」规则，套到 15m / 4h 是否仍有效。
注意：market_state 阈值(adx<20, range_width_20<3.0 等) 沿用 1h 原值，未为其他周期重新调参——
若仍有效说明 edge 鲁棒；若失效可能是阈值漂移而非 edge 不存在。OOS 按年(缩放中枢用更早年份中位)。
产物：backtest/st_cross_tf.json
"""
from __future__ import annotations
import csv, json, sys
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from indicators import super_trend, ta_atr, ta_adx, ta_sma

BASE = Path(__file__).resolve().parent
FEE = 0.05
CANDLES_SRC = {"15m": "btc_15m_full.json", "4h": "db"}


def load_candles(path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    return doc.get("base") or doc.get("candles") or []


def load_candles_4h():
    import sqlite3
    con = sqlite3.connect(str(BASE.parent / "candle_data.db"))
    rows = con.execute(
        "select ts,o,h,l,c from candles where symbol='BTC-USDT' and tf='4h' order by ts"
    ).fetchall()
    return [{"ts": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4]} for r in rows]


def get_candles(tf):
    if tf == "4h":
        return load_candles_4h()
    return load_candles(BASE / CANDLES_SRC[tf])

def market_state_at(c, h, l, cl, atr, adx, ma10, ma30, ma60, i, flips_sorted, prev_flip):
    price = cl[i]; ai = atr[i] or 0.0
    if ai <= 0 or price <= 0:
        return 1 if (adx[i] or 0) >= 22 else 0
    adx_i = adx[i] or 0.0
    lo = max(0, i - 19); hi20 = max(h[lo:i + 1]); lo20 = min(l[lo:i + 1])
    range_width_20 = (hi20 - lo20) / price * 100
    m10, m30, m60 = ma10[i], ma30[i], ma60[i]
    ma10_above_ma30 = 1 if (m10 and m30 and m10 > m30) else 0
    ma30_above_ma60 = 1 if (m30 and m60 and m30 > m60) else 0
    close_ma30_distance_ATR = (price - m30) / ai if m30 else 0.0
    ap_i = ai / price * 100
    ap_10 = atr[i - 10] / price * 100 if i >= 10 and atr[i - 10] else ap_i
    flip_20 = sum(1 for f in flips_sorted if i - 20 < f <= i)
    same_dir = (i - 1 - prev_flip) if prev_flip is not None else i
    if adx_i < 20 and range_width_20 < 3.0:
        return 0
    elif adx_i >= 25 and ma10_above_ma30 and ma30_above_ma60 and close_ma30_distance_ATR < 2.5:
        return 1
    elif adx_i >= 20 and (ap_i - ap_10) > 0 and flip_20 <= 2 and same_dir <= 8:
        return 2
    elif close_ma30_distance_ATR > 3.0 and adx_i > 30:
        return 3
    else:
        return 1 if adx_i >= 22 else 0

def sim(items, size_of):
    n = len(items)
    if n < 3:
        return {"n": n, "total": None, "sharpe": None, "mdd": None}
    eq = 1.0; peak = 1.0; dds = [0.0]; per = np.zeros(n); held = 0
    for k, t in enumerate(items):
        r = size_of(t) * t["net"] / 100.0; per[k] = r; eq *= (1 + r); held += t["bars"]
        peak = max(peak, eq); dds.append(eq / peak - 1)
    curve = np.array(dds); total = (eq - 1) * 100
    span = max(held, 1) / (24 * 365); sharpe = per.mean() / per.std() * np.sqrt(n / span) if per.std() > 0 else 0.0
    return {"n": n, "total": round(float(total), 1), "sharpe": round(float(sharpe), 2), "mdd": round(float(curve.min() * 100), 1)}

def run_tf(tf, candles):
    if len(candles) < 50:
        return {"tf": tf, "empty": True, "n_total": 0, "per_year": {}, "agg": {}}
    o = [c["o"] for c in candles]; h = [c["h"] for c in candles]
    l = [c["l"] for c in candles]; cl = [c["c"] for c in candles]
    atr = ta_atr(h, l, cl, 14); adx = ta_adx(h, l, cl, 14)
    ma10 = ta_sma(cl, 10); ma30 = ta_sma(cl, 30); ma60 = ta_sma(cl, 60)
    st = super_trend(o, h, l, cl, periods=10, multiplier=3.0, change_atr=True)
    flips = sorted(st["flips"], key=lambda f: f["i"])
    fidx = [f["i"] for f in flips]
    # 每个 flip 的前一个 flip 索引（用于 same_dir）
    prev_of = {}
    for q, f in enumerate(flips):
        prev_of[f["i"]] = fidx[q - 1] if q > 0 else None
    trades = []
    for k in range(len(flips) - 1):
        i, j = flips[k]["i"], flips[k + 1]["i"]
        if j <= i:
            continue
        side = 1 if flips[k]["type"] == "buy" else -1
        net = (cl[j] - cl[i]) / cl[i] * 100 * side - 2 * FEE
        ms = market_state_at(candles, h, l, cl, atr, adx, ma10, ma30, ma60, i, fidx, prev_of.get(i))
        ap = atr[i] / cl[i] * 100 if atr[i] and cl[i] else None
        y = datetime.fromtimestamp(candles[i]["ts"] / 1000, tz=timezone.utc).year
        trades.append({"net": net, "atr": ap, "bars": j - i, "year": y, "ms": ms})
    years = sorted(set(t["year"] for t in trades))
    out = {"tf": tf, "n_total": len(trades), "years": years, "per_year": {}, "agg": {}}
    oos_choppy = []; oos_all = []
    for y in years[1:]:
        train = [t for t in trades if t["year"] < y]; yr = [t for t in trades if t["year"] == y]
        med = np.median([t["atr"] for t in train if t["atr"]])
        sz = lambda t: np.clip(med / t["atr"], 0.25, 2.0) if t["atr"] else 1.0
        chk = [t for t in yr if t["ms"] == 0]
        a_all = sim(yr, lambda t: 1.0); a_choppy = sim(chk, sz)
        out["per_year"][y] = {"n": len(yr), "n_choppy": len(chk),
                              "all_fixed": a_all["total"], "choppy_total": a_choppy["total"],
                              "choppy_sharpe": a_choppy["sharpe"], "choppy_mdd": a_choppy["mdd"]}
        oos_choppy += chk; oos_all += yr
    m = np.median([t["atr"] for t in oos_choppy if t["atr"]])
    out["agg"]["choppy"] = sim(oos_choppy, lambda t: np.clip(m / t["atr"], 0.25, 2.0) if t["atr"] else 1.0)
    out["agg"]["all_fixed"] = sim(oos_all, lambda t: 1.0)
    return out

result = {tf: run_tf(tf, get_candles(tf)) for tf in CANDLES_SRC}
BASE = Path(__file__).resolve().parent
(result and (BASE / "st_cross_tf.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"))
print("写出 st_cross_tf.json\n")
for tf, R in result.items():
    if R.get("empty"):
        print(f"=== {tf} : 数据为空，跳过 ===\n"); continue
    print(f"=== {tf} (总交易 {R['n_total']}, 年 {R['years']}) ===")
    for y, v in R["per_year"].items():
        print(f"  {y}  n={v['n']:>4}(震荡{v['n_choppy']:>3})  全固定{v['all_fixed']:+}%  只震荡{v['choppy_total']:+}% 夏普{v['choppy_sharpe']} 回撤{v['choppy_mdd']}%")
    c = R["agg"]["choppy"]; a = R["agg"]["all_fixed"]
    print(f"  OOS聚合  只震荡: {c['total']:+}% 夏普{c['sharpe']} 回撤{c['mdd']}% n={c['n']}  | 全固定: {a['total']:+}% 夏普{a['sharpe']}\n")
