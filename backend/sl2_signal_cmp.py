# -*- coding: utf-8 -*-
"""
SuperTrend(策略) vs UT Bot Alerts(QuantNomad) —— 哪套信号更适合当训练的靶子
==========================================================================

先说结论性的结构事实：这两者**不是两套不同的策略**，而是同一个家族
「ATR 棘轮移动止损 (ATR ratcheting trailing stop)」上的两个点：

    SuperTrend  锚点 = hl2  (最高+最低)/2 ，带宽 = mult    × ATR(n)   经典默认 (10, 3.0)
    UT Bot      锚点 = close                ，带宽 = nLoss   × ATR(n)   QuantNomad 默认 (10, 1.0)

两者都是「收盘价穿越棘轮线即翻向」，翻向点几乎重合，差别主要是**灵敏度**
（UT Bot 默认 1.0×ATR 比 SuperTrend 的 3.0×ATR 敏感得多，翻向频繁得多）。

所以真正的问题不是"哪套策略好"，而是：**在这条灵敏度曲线上，哪个点产生的信号
既够多、又有可学的规律？** 本脚本用控制变量的方式回答：

  - 特征集**完全相同**（同一套通用行情上下文特征，与信号来源无关）
  - 验证口径**完全相同**（时序前向验证 walk-forward，借自 sl2_lab，不做随机切分）
  - 标签**完全相同**（fwd50 方向 / tpsl3.0-3.0-h50）
  - **唯一变量** = 信号源

用法：
    cd backend
    python sl2_signal_cmp.py
    python sl2_signal_cmp.py --fee 0.05
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from indicators import super_trend, ta_adx, ta_atr, ta_rma, ta_sma
from sl2_lab import LGB_PARAMS, walk_forward

FEE_PCT = 0.05          # 单边手续费 %（往返 = 2 倍）


# ──────────────────────────────────────────────────────────────
# 信号源
# ──────────────────────────────────────────────────────────────
def supertrend_signals(candles, period, mult, src="hl2"):
    o = [c["o"] for c in candles]; h = [c["h"] for c in candles]
    l = [c["l"] for c in candles]; cl = [c["c"] for c in candles]
    st = super_trend(o, h, l, cl, periods=period, multiplier=mult,
                     src=src, change_atr=True)
    return [(f["i"], 1 if f["type"] == "buy" else -1) for f in st["flips"]], st["atr"]


def utbot_signals(candles, atr_len, nloss, atr=None):
    """QuantNomad「UT Bot Alerts」的逐 bar 复刻。

    Pine 原式（src = close，xATRTrailingStop 初值 0）：
        src>t1 & src1>t1 -> t = max(t1, src-nLoss*ATR)
        src<t1 & src1<t1 -> t = min(t1, src+nLoss*ATR)
        src>t1           -> t = src - nLoss*ATR
        否则              -> t = src + nLoss*ATR
        pos 在 src 上穿/下穿 t 时翻向
    """
    cl = [c["c"] for c in candles]
    n = len(cl)
    if atr is None:
        atr = ta_atr([c["h"] for c in candles], [c["l"] for c in candles], cl, atr_len)
    trail = [0.0] * n
    pos = [0] * n
    for i in range(1, n):
        a = atr[i] or 0.0
        s, s1, t1 = cl[i], cl[i - 1], trail[i - 1]
        if s > t1 and s1 > t1:
            t = max(t1, s - nloss * a)
        elif s < t1 and s1 < t1:
            t = min(t1, s + nloss * a)
        elif s > t1:
            t = s - nloss * a
        else:
            t = s + nloss * a
        trail[i] = t
        if s1 < t1 and s > t1:
            pos[i] = 1
        elif s1 > t1 and s < t1:
            pos[i] = -1
        else:
            pos[i] = pos[i - 1]
    return [(i, pos[i]) for i in range(1, n) if pos[i] != pos[i - 1] and pos[i] != 0]


# ──────────────────────────────────────────────────────────────
# 通用特征（与信号来源无关，两套信号共用同一套）
# ──────────────────────────────────────────────────────────────
def build_ctx(candles):
    o = [c["o"] for c in candles]; h = [c["h"] for c in candles]
    l = [c["l"] for c in candles]; cl = [c["c"] for c in candles]
    v = [c["vol"] for c in candles]
    atr = ta_atr(h, l, cl, 14)
    atr_sma50 = ta_sma([x for x in atr if x is not None], 50)
    adx = ta_adx(h, l, cl, 14)
    sma20 = ta_sma(cl, 20)
    vsma20 = ta_sma(v, 20)
    rsi = _rsi(cl, 14)
    return dict(o=o, h=h, l=l, c=cl, v=v, atr=atr, atr50=_realign(atr, atr_sma50),
                adx=adx, sma20=sma20, vsma20=vsma20, rsi=rsi)


def _realign(src, short):
    """ta_sma 会丢掉前导 None，这里把它对齐回原始长度。"""
    off = len(src) - len(short)
    return [None] * off + list(short)


def _rsi(cl, n=14):
    out = [None] * len(cl)
    if len(cl) < n + 1:
        return out
    gains, losses = [0.0], [0.0]
    for i in range(1, len(cl)):
        d = cl[i] - cl[i - 1]
        gains.append(max(d, 0.0)); losses.append(max(-d, 0.0))
    ag = ta_rma(gains[1:], n); al = ta_rma(losses[1:], n)
    for i in range(len(ag)):
        if ag[i] is None:
            continue
        rs = ag[i] / al[i] if al[i] else 0.0
        out[i + 1] = 100 - 100 / (1 + rs)
    return out


FEATS = ["dir", "atr_pct", "atr_ratio", "er20", "adx", "adx_slope", "mom5", "mom20",
         "rsi", "vol_ratio", "body_atr", "wick_ratio", "dist_ma20_atr", "range_pos20",
         "sig_age"]


def features_at(ctx, i, side, prev_sig, n):
    c = ctx["c"]; h = ctx["h"]; l = ctx["l"]; o = ctx["o"]; v = ctx["v"]
    a = ctx["atr"][i] or 0.0
    if a <= 0 or i < 60:
        return None
    body = abs(c[i] - o[i]); rng = h[i] - l[i]
    er_num = abs(c[i] - c[i - 20])
    er_den = sum(abs(c[k] - c[k - 1]) for k in range(i - 19, i + 1))
    hh = max(h[i - 20:i + 1]); ll = min(l[i - 20:i + 1])
    return [
        side,
        a / c[i] * 100,
        (a / ctx["atr50"][i]) if ctx["atr50"][i] else 0.0,
        (er_num / er_den) if er_den else 0.0,
        ctx["adx"][i] or 0.0,
        (ctx["adx"][i] or 0.0) - (ctx["adx"][i - 5] or 0.0),
        side * (c[i] - c[i - 5]) / c[i - 5] * 100,
        side * (c[i] - c[i - 20]) / c[i - 20] * 100,
        ctx["rsi"][i] or 50.0,
        v[i] / (ctx["vsma20"][i] or v[i]) if ctx["vsma20"][i] else 1.0,
        body / a if a else 0.0,
        (rng - body) / body if body > 1e-12 else 0.0,
        side * (c[i] - ctx["sma20"][i]) / a if a else 0.0,
        (c[i] - ll) / (hh - ll) if hh > ll else 0.5,
        min((i - prev_sig) / 500.0, 1.0),
    ]


# ──────────────────────────────────────────────────────────────
# 标签
# ──────────────────────────────────────────────────────────────
def fwd(ctx, i, side, h=50):
    j = i + h
    if j >= len(ctx["c"]):
        return None
    e = ctx["c"][i]
    return (ctx["c"][j] - e) / e * 100 * side


def tpsl(ctx, i, side, tp=3.0, sl=3.0, h=50):
    e = ctx["c"][i]
    tp_p = e * (1 + tp / 100) if side > 0 else e * (1 - tp / 100)
    sl_p = e * (1 - sl / 100) if side > 0 else e * (1 + sl / 100)
    jmax = min(i + h, len(ctx["c"]) - 1)
    for k in range(i + 1, jmax + 1):
        if side > 0:
            if ctx["h"][k] >= tp_p: return 1.0
            if ctx["l"][k] <= sl_p: return 0.0
        else:
            if ctx["l"][k] <= tp_p: return 1.0
            if ctx["h"][k] >= sl_p: return 0.0
    return 1.0 if (ctx["c"][jmax] - e) * side > 0 else 0.0


def build_rows(ctx, sigs):
    X, prev, n = [], 0, len(ctx["c"])
    for (i, side) in sigs:
        f = features_at(ctx, i, side, prev, n)
        if f is not None:
            X.append((i, side, f))
        prev = i
    return X


def evaluate(name, ctx, sigs, folds, rounds, seeds, fee):
    rows = build_rows(ctx, sigs)
    if len(rows) < 120:
        print(f"{name:<26} 有效信号仅 {len(rows)}，跳过")
        return None
    idx = [r[0] for r in rows]
    side = [r[1] for r in rows]
    X = np.asarray([r[2] for r in rows], float)

    # 扣费期望
    rets = [fwd(ctx, i, s, 50) for i, s in zip(idx, side)]
    pairs = [(r, s) for r, s in zip(rets, side) if r is not None]
    rets = np.asarray([p[0] for p in pairs])
    net = rets - 2 * fee
    win = float((rets > 0).mean() * 100)

    out = {"name": name, "n": len(rows), "per_year": len(rows) / (len(ctx["c"]) / 24 / 365),
           "avg_ret": float(rets.mean()), "win": win, "net_ret": float(net.mean())}

    for tag, builder in (("fwd50", lambda: [fwd(ctx, i, s, 50) for i, s in zip(idx, side)]),
                         ("tpsl3/3h50", lambda: [tpsl(ctx, i, s) for i, s in zip(idx, side)])):
        y = builder()
        keep = [k for k, v in enumerate(y) if v is not None]
        if len(keep) < 120:
            continue
        Xk = X[keep]; yk = np.asarray([y[k] for k in keep], float)
        if tag == "fwd50":
            yk = (yk > 0).astype(float)
        means = []
        for sd in seeds:
            r = walk_forward(Xk, yk, "cls", folds, rounds, sd)
            if r:
                means.append(r[0])
        out[tag] = float(np.mean(means)) if means else None
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fee", type=float, default=FEE_PCT)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--rounds", type=int, default=200)
    ap.add_argument("--seeds", default="42,7,2024")
    a = ap.parse_args(argv)
    seeds = [int(x) for x in a.seeds.split(",")]

    doc = json.loads(Path("backtest/btc_1h_full_fetched.json").read_text(encoding="utf-8"))
    candles = doc.get("base") or doc.get("candles") or []
    ctx = build_ctx(candles)
    years = len(candles) / 24 / 365
    print(f"数据：BTC-USDT 1h · {len(candles)} 根 ≈ {years:.2f} 年\n")

    sets = []
    st_atr = None
    for mult in (1.0, 2.0, 3.0, 4.0):
        s, st_atr = supertrend_signals(candles, 10, mult)
        sets.append((f"SuperTrend(10, {mult})", s))
    for nloss in (0.5, 1.0, 1.5, 2.0):
        s = utbot_signals(candles, 10, nloss, atr=st_atr)
        tag = " ← QuantNomad 默认" if nloss == 1.0 else ""
        sets.append((f"UT Bot(nLoss={nloss}, ATR10){tag}", s))

    print(f"{'信号源':<30}{'信号数':>7}{'每年':>7}{'胜率%':>8}"
          f"{'fwd50均值%':>11}{'扣费后%':>9}{'fwd50_AUC':>11}{'tpsl_AUC':>10}")
    results = []
    for name, sigs in sets:
        r = evaluate(name, ctx, sigs, a.folds, a.rounds, seeds, a.fee)
        if not r:
            continue
        results.append(r)
        print(f"{name:<30}{r['n']:>7}{r['per_year']:>7.0f}{r['win']:>8.1f}"
              f"{r['avg_ret']:>11.2f}{r['net_ret']:>9.2f}"
              f"{(r.get('fwd50') or 0):>11.3f}{(r.get('tpsl3/3h50') or 0):>10.3f}")

    print(f"\n（手续费单边 {a.fee}%，往返扣 {2*a.fee}%；AUC 基线 0.5）")
    best = max((r for r in results if r.get("tpsl3/3h50")),
               key=lambda r: r["tpsl3/3h50"], default=None)
    if best:
        print(f"可学性最高：{best['name']}  tpsl AUC={best['tpsl3/3h50']:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
