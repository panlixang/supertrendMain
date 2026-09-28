# -*- coding: utf-8 -*-
"""
波动率目标仓位 (volatility targeting) —— 检验波动率预测能否改善风险调整后收益
==========================================================================

背景：前面已证明这套信号在 BTC 1h 上**方向零 alpha**（扣费后期望≈ -手续费）。
      既然方向挖不出东西，唯一还站得住的抓手就是**波动率可预测**（walk-forward R²≈0.18）。
      本脚本回答：把波动率预测接进仓位管理，到底有没有用？

三种仓位方案（控制变量，同一批交易、同一套费率）：
    A 固定仓位        size = 1                         —— 基线
    B ATR 倒数缩放    size ∝ 1/ATR%                    —— 朴素基线（不用 ML）
    C ML 预测波动缩放 size ∝ 1/(预测比 × ATR%)          —— 本项目的模型

**B 是不可跳过的对照**：如果 C 打不过 B，说明 ML 没贡献，用回溯 ATR 就够了。
很多"波动率择时"的收益其实来自 B 那部分，误以为是模型赚的。

关键纪律：
    - 仓位缩放系数只用**开仓当时**可知的信息，绝不看未来。
    - ML 模型本身走**前向验证**：第 k 折只用该折之前的交易训练，之后才预测。
      直接拿 ml_runs/btc_1h_vol50 跑全史会得到样本内结果，是自欺欺人。
    - 一单一仓（开仓到下个反向信号平仓），避免重叠持仓的仓位口径扯皮。

用法：
    cd backend
    python sl2_voltarget.py
    python sl2_voltarget.py --folds 5 --fee 0.05 --cap 2.0
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np

import sl_v2 as S
import sl2_labels as labels
from indicators import super_trend, ta_atr
from sl2_lab import LGB_PARAMS


# ──────────────────────────────────────────────────────────────
# 1. 取出「一笔交易」的清单
# ──────────────────────────────────────────────────────────────
def build_trades(candles, mult=3.0, period=10):
    """SuperTrend 翻向即开仓、下个反向翻向即平仓 —— 与实盘出场口径一致。"""
    o = [c["o"] for c in candles]; h = [c["h"] for c in candles]
    l = [c["l"] for c in candles]; cl = [c["c"] for c in candles]
    st = super_trend(o, h, l, cl, periods=period, multiplier=mult, change_atr=True)
    flips = sorted(st["flips"], key=lambda f: f["i"])
    trades = []
    for k in range(len(flips) - 1):
        i, j = flips[k]["i"], flips[k + 1]["i"]
        side = 1 if flips[k]["type"] == "buy" else -1
        if j <= i:
            continue
        e, x = cl[i], cl[j]
        # 方向化毛收益（%）：多 = (x-e)/e，空 = (e-x)/e
        gross = (x - e) / e * 100 * side
        trades.append({"i": i, "j": j, "side": side, "ts": candles[i]["ts"], "gross": gross,
                       "bars": j - i})
    return trades, st


# ──────────────────────────────────────────────────────────────
# 2. 每个开仓点上可知的波动信息
# ──────────────────────────────────────────────────────────────
def entry_context(candles):
    h = [c["h"] for c in candles]; l = [c["l"] for c in candles]
    cl = [c["c"] for c in candles]
    atr = ta_atr(h, l, cl, 14)
    return {"atr": atr, "cl": cl}


def atr_pct_at(ctx, i):
    a = ctx["atr"][i]; c = ctx["cl"][i]
    if not a or not c:
        return None
    return a / c * 100


# ──────────────────────────────────────────────────────────────
# 3. 模拟：单仓、逐折前进
# ──────────────────────────────────────────────────────────────
def simulate(trades, sizes, fee):
    """按给定仓位序列跑净值曲线。sizes[k] 是第 k 笔交易的仓位倍数。"""
    eq = 1.0
    curve = [eq]
    for t, s in zip(trades, sizes):
        net = t["gross"] - 2 * fee          # 往返手续费
        eq *= (1 + s * net / 100.0)
        curve.append(eq)
    return np.asarray(curve)


def stats(trades, sizes, fee, years, bars_per_year):
    curve = simulate(trades, sizes, fee)
    n = len(trades)
    if n < 5:
        return None
    # 用「每笔」而不是整段净值算波动，避免交易频率污染对比
    per_trade = np.asarray([(t["gross"] - 2 * fee) for t in trades]) * np.asarray(sizes) / 100.0
    total = float(curve[-1] - 1) * 100
    # 年化：按持有 K 线根数折算
    held = sum(t["bars"] for t in trades)
    span_years = max(held, 1) / bars_per_year
    cagr = ((curve[-1]) ** (1 / span_years) - 1) * 100 if span_years > 0 and curve[-1] > 0 else -100.0
    # 交易级夏普（每笔平均 / 每笔波动），再按年化交易数折算
    tr_per_year = n / span_years if span_years > 0 else 0
    sharpe = (per_trade.mean() / per_trade.std() * np.sqrt(tr_per_year)) if per_trade.std() > 0 else 0.0
    peak = np.maximum.accumulate(curve)
    dd = float(np.min(curve / peak - 1)) * 100
    return {"n": n, "total": total, "cagr": cagr, "sharpe": float(sharpe),
            "maxdd": dd, "calmar": (cagr / abs(dd)) if dd < 0 else 0.0,
            "avg_size": float(np.mean(sizes))}


# ──────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(description="波动率目标仓位（前向验证）")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--rounds", type=int, default=200)
    ap.add_argument("--fee", type=float, default=0.05)
    ap.add_argument("--cap", type=float, default=2.0, help="仓位倍数上限")
    ap.add_argument("--floor", type=float, default=0.25, help="仓位倍数下限")
    ap.add_argument("--mult", type=float, default=3.0)
    a = ap.parse_args(argv)

    doc = json.loads(Path("backtest/btc_1h_full_fetched.json").read_text(encoding="utf-8"))
    candles = doc.get("base") or doc.get("candles") or []
    bars_per_year = 24 * 365
    ctx = entry_context(candles)
    trades, _st = build_trades(candles, mult=a.mult)
    print(f"数据：BTC-USDT 1h · {len(candles)} 根 ≈ {len(candles)/bars_per_year:.2f} 年")
    print(f"SuperTrend(10, {a.mult}) 完成交易 {len(trades)} 笔 · "
          f"平均持仓 {np.mean([t['bars'] for t in trades]):.0f} 根 · "
          f"毛收益均值 {np.mean([t['gross'] for t in trades]):.3f}%")

    # 特征行（vol 模型要求的 28 维，与 features.json 一致）——按 ts 对齐到交易
    ds, _ = S.get_dataset("BTC-USDT", "1h", len(candles), "tpsl", 30, 2.5, 2.0)
    row_by_ts = {r["ts"]: r for r in ds["rows"]}
    feats = list(ds["features"])
    model = S.SlModel("btc_1h_vol50")
    if model.features != feats:
        print("[失败] vol 模型的特征顺序与数据集不一致，拒绝继续")
        return 1

    T, X, y, apct = [], [], [], []
    ts_list = [t["ts"] for t in trades]
    ratios = labels.compute_targets(candles, ts_list, "vol", 50, [t["side"] for t in trades])
    for t, r in zip(trades, ratios):
        row = row_by_ts.get(t["ts"])
        p = atr_pct_at(ctx, t["i"])
        if row is None or r is None or not p:
            continue
        T.append(t); y.append(r); apct.append(p)
        X.append([_num(row.get(f)) for f in feats])
    X = np.asarray(X, float); y = np.asarray(y, float); apct = np.asarray(apct, float)
    print(f"可用于仓位实验的交易 {len(T)} 笔\n")
    if len(T) < 120:
        print("[失败] 样本不足")
        return 1

    target = float(np.median(apct))       # 目标波动率：取中位，使平均仓位≈1

    # ── 前向验证：逐折用「过去的交易」训 vol 模型，再给本折预测 ──
    n = len(T)
    block = n // (a.folds + 1)
    sizes_fixed = np.ones(n)
    sizes_atr = np.ones(n)
    sizes_ml = np.ones(n)
    fold_rows = []
    for k in range(1, a.folds + 1):
        tr_end, te_end = k * block, min(n, (k + 1) * block)
        if tr_end < 80 or te_end - tr_end < 20:
            continue
        b = lgb.train(dict(LGB_PARAMS, objective="regression", seed=42),
                      lgb.Dataset(X[:tr_end], label=y[:tr_end]), num_boost_round=a.rounds)
        ratio_hat = b.predict(X[tr_end:te_end])
        s_atr = np.clip(target / apct[tr_end:te_end], a.floor, a.cap)
        s_ml = np.clip(target / (ratio_hat * apct[tr_end:te_end]), a.floor, a.cap)
        sizes_atr[tr_end:te_end] = s_atr
        sizes_ml[tr_end:te_end] = s_ml
        # 该折上的样本外 R²（预测未来波动比的能力）
        r2 = 1 - ((y[tr_end:te_end] - ratio_hat) ** 2).sum() / \
             ((y[tr_end:te_end] - y[tr_end:te_end].mean()) ** 2).sum()
        fold_rows.append((k, te_end - tr_end, r2))
    # 前 block 笔没有模型可用，统一用 ATR 基线，保证三者交易集合相同
    sizes_atr[:block] = np.clip(target / apct[:block], a.floor, a.cap)
    sizes_ml[:block] = sizes_atr[:block]

    print("样本外 vol 预测（每折）：")
    for k, m, r2 in fold_rows:
        print(f"  折{k}: 测试 {m:>3} 笔   R²={r2:>7.3f}")
    print()

    rows = []
    for name, sz in (("A 固定仓位", sizes_fixed),
                     ("B ATR倒数缩放(无ML)", sizes_atr),
                     ("C ML预测波动缩放", sizes_ml)):
        st = stats(T, sz, a.fee, None, bars_per_year)
        if st:
            rows.append((name, st))

    print(f"{'方案':<22}{'笔数':>6}{'总收益%':>11}{'年化%':>9}{'夏普':>8}"
          f"{'最大回撤%':>11}{'Calmar':>9}{'均仓位':>8}")
    for name, st in rows:
        print(f"{name:<22}{st['n']:>6}{st['total']:>11.1f}{st['cagr']:>9.1f}"
              f"{st['sharpe']:>8.2f}{st['maxdd']:>11.1f}{st['calmar']:>9.2f}{st['avg_size']:>8.2f}")

    # ── 诊断：把开仓点按「开仓当时 ATR%」分五档，看毛期望是否随波动单调 ──
    # 注意：这是**全样本**分档，属于样本内观察，只用来找线索，不能当结论。
    print("\n诊断（全样本、样本内）：按开仓当时 ATR% 分五档，看毛期望收益有没有规律（不扣费）")
    if len(fold_rows) >= 3:
        order = np.argsort(apct)
        b5 = len(order) // 5
        print(f"{'档位':<8}{'笔数':>6}{'平均ATR%':>10}{'毛收益均值%':>13}{'胜率%':>8}")
        for q in range(5):
            sel = order[q * b5:(q + 1) * b5] if q < 4 else order[4 * b5:]
            g = np.asarray([T[k]["gross"] for k in sel])
            print(f"Q{q+1:<7}{len(sel):>6}{apct[sel].mean():>10.2f}{g.mean():>13.3f}"
                  f"{(g > 0).mean() * 100:>8.1f}")

    print("\n注：A/B/C 交易集合完全相同，只有仓位倍数不同；费率单边 %.2f%%。" % a.fee)
    return 0


def _num(v):
    try:
        f = float(v)
        return f if np.isfinite(f) else 0.0
    except (TypeError, ValueError):
        return 0.0


if __name__ == "__main__":
    sys.exit(main())
