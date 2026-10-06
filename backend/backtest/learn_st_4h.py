# -*- coding: utf-8 -*-
"""学习 4h：把 1h 那套研究（裸信号 / 波动缩放 / regime 过滤 / 前向验证）搬到 BTC 4h。
数据源：candle_data.db 的 BTC-USDT 4h（8905 根, 2022-09→2026-09）。
重点回答：
  1) 4h 裸 ST 信号 OOS 是否真赚（对比 1h 的 -79%）？
  2) 1/ATR 波动缩放在 4h 是否仍改善风险？
  3) regime 过滤（留震荡 / drop 最差 regime）在 4h 是否有用——还是 1h 阈值不适用需重标定？
  4) 前向验证：每年只用此前年份自标定「砍哪个 regime + 缩放中枢」，看 OOS 是否稳。
注：market_state 先用 1h 阈值首过，by_regime 期望会暴露阈值在 4h 是否合理。
产物：backtest/st_4h.json
"""
from __future__ import annotations
import json, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from indicators import super_trend, ta_atr, ta_adx, ta_sma

BASE = Path(__file__).resolve().parent
FEE = 0.05
LABEL = {0: "震荡", 1: "趋势", 2: "启动", 3: "过热"}


def load_4h():
    con = sqlite3.connect(str(BASE.parent / "candle_data.db"))
    rows = con.execute(
        "select ts,o,h,l,c from candles where symbol='BTC-USDT' and tf='4h' order by ts"
    ).fetchall()
    return [{"ts": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4]} for r in rows]


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
    span = max(held, 1) / (6 * 365); sharpe = per.mean() / per.std() * np.sqrt(n / span) if per.std() > 0 else 0.0
    return {"n": n, "total": round(float(total), 1), "sharpe": round(float(sharpe), 2), "mdd": round(float(curve.min() * 100), 1)}


def main():
    candles = load_4h()
    o = [c["o"] for c in candles]; h = [c["h"] for c in candles]
    l = [c["l"] for c in candles]; cl = [c["c"] for c in candles]
    atr = ta_atr(h, l, cl, 14); adx = ta_adx(h, l, cl, 14)
    ma10 = ta_sma(cl, 10); ma30 = ta_sma(cl, 30); ma60 = ta_sma(cl, 60)
    st = super_trend(o, h, l, cl, periods=10, multiplier=3.0, change_atr=True)
    flips = sorted(st["flips"], key=lambda f: f["i"]); fidx = [f["i"] for f in flips]
    prev_of = {f["i"]: (fidx[q - 1] if q > 0 else None) for q, f in enumerate(flips)}
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
    print(f"4h 总交易 {len(trades)}，年 {years}")

    # by_regime（全样本，看 1h 阈值在 4h 是否合理）
    by_regime = {}
    for s in (0, 1, 2, 3):
        grp = [t for t in trades if t["ms"] == s]
        if not grp:
            continue
        fix = sim(grp, lambda t: 1.0)
        m = np.median([t["atr"] for t in grp if t["atr"]])
        sc = sim(grp, lambda t: np.clip(m / t["atr"], 0.25, 2.0) if t["atr"] else 1.0)
        by_regime[s] = {"label": LABEL[s], "n": len(grp),
                        "fixed": fix["total"], "fixed_sharpe": fix["sharpe"],
                        "scaled": sc["total"], "scaled_sharpe": sc["sharpe"]}
        print(f"  regime {LABEL[s]}({s}) n={len(grp)} 固定{fix['total']:+}% 夏普{fix['sharpe']} | 缩放{sc['total']:+}% 夏普{sc['sharpe']}")

    # 前向验证：每年用此前年份自标定
    folds = []; oos_A = []; oos_B = []; oos_C = []; oos_D = []
    for y in years[1:]:
        train = [t for t in trades if t["year"] < y]; yr = [t for t in trades if t["year"] == y]
        med = np.median([t["atr"] for t in train if t["atr"]])
        sz = lambda t: np.clip(med / t["atr"], 0.25, 2.0) if t["atr"] else 1.0
        # 训练窗选最差 regime（mean net 最低, n>=5）
        by_state = {}
        for t in train:
            by_state.setdefault(t["ms"], []).append(t["net"])
        cand = {s: v for s, v in by_state.items() if len(v) >= 5}
        worst = min(cand, key=lambda s: np.mean(cand[s])) if cand else None
        chk = [t for t in yr if t["ms"] == 0]
        drop = [t for t in yr if t["ms"] != worst] if worst is not None else yr
        a = sim(yr, lambda t: 1.0); b = sim(yr, sz); c = sim(chk, sz); d = sim(drop, sz)
        folds.append({"year": y, "worst_state": LABEL.get(worst, "-"),
                      "n": len(yr), "n_choppy": len(chk),
                      "A": a["total"], "B": b["total"], "C_keep_choppy": c["total"],
                      "D_drop_worst": d["total"]})
        oos_A += yr; oos_B += yr; oos_C += chk; oos_D += drop
        print(f"  {y} 最差regime={LABEL.get(worst,'-')}  A{a['total']:+} B{b['total']:+} C(choppy){c['total']:+} D(drop){d['total']:+}")

    def agg(items):
        m = np.median([t["atr"] for t in items if t["atr"]])
        sz = lambda t: np.clip(m / t["atr"], 0.25, 2.0) if t["atr"] else 1.0
        return {"fixed": sim(items, lambda t: 1.0), "scaled": sim(items, sz)}
    out = {"meta": {"n_total": len(trades), "years": years},
           "by_regime": by_regime, "folds": folds,
           "agg": {"A_all_fixed": agg(oos_A)["fixed"],
                   "B_all_scaled": agg(oos_B)["scaled"],
                   "C_keep_choppy": agg(oos_C)["scaled"],
                   "D_drop_worst": agg(oos_D)["scaled"]}}
    (BASE / "st_4h.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    a = out["agg"]
    print("\nOOS 聚合:")
    print(f"  A 全固定 {a['A_all_fixed']['total']:+}% 夏普{a['A_all_fixed']['sharpe']} 回撤{a['A_all_fixed']['mdd']}%")
    print(f"  B 全缩放 {a['B_all_scaled']['total']:+}% 夏普{a['B_all_scaled']['sharpe']} 回撤{a['B_all_scaled']['mdd']}%")
    print(f"  C 留震荡 {a['C_keep_choppy']['total']:+}% 夏普{a['C_keep_choppy']['sharpe']} 回撤{a['C_keep_choppy']['mdd']}%")
    print(f"  D 砍最差 {a['D_drop_worst']['total']:+}% 夏普{a['D_drop_worst']['sharpe']} 回撤{a['D_drop_worst']['mdd']}%")


if __name__ == "__main__":
    main()
