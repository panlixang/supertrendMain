# -*- coding: utf-8 -*-
"""
策略学习 · 标签口径实验台 (sl2_lab)
===================================

当某个标签口径下模型 AUC≈0.5（学不动）时，用它回答一个问题 ——
**是超参没调好，还是这个靶子本身不可预测？**

核心方法：
1. 同一批特征，换不同「标签定义」训练 —— 靶子本身当变量。
2. 评估用**时序前向验证(walk-forward)**，不用随机切分：
   相邻信号的持仓区间大量重叠，随机切分等于把未来的标签漏进训练集。
3. 带对照组：标签随机打乱后重跑。分类 AUC 必须回到 ~0.5、回归 R² 必须 ≤0，
   否则说明是评估流程有泄漏，而不是靶子有信号。
4. 对头部候选做**置换检验(permutation test)**：把标签打乱 N 次得到"纯噪声下 AUC 的分布"，
   再看真实 AUC 落在什么分位，得出 p 值 —— 这才是"这套参数到底有没有效"的答案。

用法：
    cd backend
    python sl2_lab.py labels                 # 扫描预置的各类标签
    python sl2_lab.py sweep                  # 扫描 tp/sl/horizon 网格
    python sl2_lab.py perm --tp 3 --sl 3 --h 50 --perm 200
"""
from __future__ import annotations

import argparse
import json
import sys
from functools import lru_cache
from pathlib import Path

import lightgbm as lgb
import numpy as np
from sklearn.metrics import r2_score, roc_auc_score

import sl_v2 as S
import sl2_labels as labels
from indicators import super_trend

LGB_PARAMS = {"learning_rate": 0.05, "num_leaves": 15, "min_data_in_leaf": 20,
              "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1,
              "verbose": -1, "num_threads": 0}


def load_ctx(symbol="BTC-USDT", base_tf="1h", limit=36500):
    """取 K 线 + 信号特征行（复用 sl_v2 的磁盘缓存，秒级）。"""
    doc = json.loads((Path("backtest") / "btc_1h_full_fetched.json").read_text(encoding="utf-8"))
    candles = doc.get("base") or doc.get("candles") or []
    o = [c["o"] for c in candles]; h = [c["h"] for c in candles]
    l = [c["l"] for c in candles]; cl = [c["c"] for c in candles]
    st = super_trend(o, h, l, cl, periods=10, multiplier=3.0, change_atr=True)
    atr = st["atr"]

    ds, _ = S.get_dataset(symbol, base_tf, limit, "tpsl", 30, 2.5, 2.0)
    rows = sorted(ds["rows"], key=lambda r: r["ts"])       # 保证时序
    ts_idx = {c["ts"]: i for i, c in enumerate(candles)}
    idx = [ts_idx.get(r["ts"]) for r in rows]
    keep = [k for k, i in enumerate(idx) if i is not None]
    return dict(candles=candles, o=o, h=h, l=l, cl=cl, atr=atr,
                rows=[rows[k] for k in keep], idx=[idx[k] for k in keep],
                feats=list(ds["features"]))


# ──────────────────────────────────────────────────────────────
# 标签构造器： (ctx, i, side) -> float | None，None = 该样本不可用
# ──────────────────────────────────────────────────────────────
def make_fwd(h):
    def f(ctx, i, side):
        j = i + h
        if j >= len(ctx["cl"]):
            return None
        e = ctx["cl"][i]
        return (ctx["cl"][j] - e) / e * 100 if side > 0 else (e - ctx["cl"][j]) / e * 100
    return f


def make_tpsl(tp_pct, sl_pct, horizon):
    """先触 TP 记 +tp%，先触 SL 记 -sl%，都没触则按末根净收益。"""
    def f(ctx, i, side):
        e = ctx["cl"][i]; hh = ctx["h"]; ll = ctx["l"]
        tp = tp_pct / 100.0; sl = sl_pct / 100.0
        tp_p = e * (1 + tp) if side > 0 else e * (1 - tp)
        sl_p = e * (1 - sl) if side > 0 else e * (1 + sl)
        jmax = min(i + horizon, len(ctx["cl"]) - 1)
        if jmax <= i:
            return None
        for k in range(i + 1, jmax + 1):
            if side > 0:
                if hh[k] >= tp_p: return tp * 100
                if ll[k] <= sl_p: return -sl * 100
            else:
                if ll[k] <= tp_p: return tp * 100
                if hh[k] >= sl_p: return -sl * 100
        e2 = ctx["cl"][jmax]
        return (e2 - e) / e * 100 if side > 0 else (e - e2) / e * 100
    return f


def make_mfe_mae(horizon):
    """最大有利偏移 / 最大不利偏移。实现共用 sl2_labels，避免两处口径漂移。"""
    def f(ctx, i, side):
        return labels.mfe_mae(ctx["candles"], i, side, horizon)
    return f


def make_vol_ratio(horizon):
    """未来 horizon 根的已实现波动 / 当前 ATR。实现共用 sl2_labels，避免两处口径漂移。"""
    def f(ctx, i, side):
        return labels.vol_ratio(ctx["candles"], i, horizon, ctx["atr"])
    return f


def preset_targets():
    T = []
    for h in (5, 10, 20, 50, 100):
        T.append((f"fwd{h}_方向", "cls", make_fwd(h), 0.0))
        T.append((f"fwd{h}_幅度", "reg", make_fwd(h), None))
    for (tp, sl, h) in ((1.0, 1.0, 10), (1.5, 1.5, 20), (2.0, 2.0, 30),
                        (3.0, 3.0, 50), (2.5, 2.0, 30)):
        T.append((f"tpsl{tp}/{sl}_h{h}", "cls", make_tpsl(tp, sl, h), 0.0))
    T.append(("mfe/mae_h30", "reg", make_mfe_mae(30), None))
    T.append(("mfe/mae_h10", "reg", make_mfe_mae(10), None))
    T.append(("未来波动率比_h20", "reg", make_vol_ratio(20), None))
    T.append(("未来波动率比_h50", "reg", make_vol_ratio(50), None))
    return T


# ──────────────────────────────────────────────────────────────
# 时序前向验证 + 置换检验
# ──────────────────────────────────────────────────────────────
@lru_cache(maxsize=4)
def _mat(which: str, horizon: int, tp: float, sl: float):
    return None   # 占位，避免误用缓存


def make_matrix(ctx, feats, lab, thr):
    X, y = [], []
    for r, i in zip(ctx["rows"], ctx["idx"]):
        v = lab(ctx, i, int(r["dir"]))
        if v is None or not np.isfinite(v):
            continue
        X.append([_num(r.get(f)) for f in feats])
        y.append(v if thr is None else (1.0 if v > thr else 0.0))
    return np.asarray(X, float), np.asarray(y, float)


def _num(v):
    try:
        f = float(v)
        return f if np.isfinite(f) else 0.0
    except (TypeError, ValueError):
        return 0.0


def walk_forward(X, y, task, folds=5, rounds=200, seed=42):
    """过去训 / 紧接着的未来测。返回 (均值, 各折列表)。"""
    n = len(y)
    if n < 150 or folds < 2:
        return None
    block = n // (folds + 1)
    scores = []
    for k in range(1, folds + 1):
        tr_end, te_end = k * block, min(n, (k + 1) * block)
        if tr_end < 120 or te_end - tr_end < 25:
            continue
        Xtr, ytr, Xte, yte = X[:tr_end], y[:tr_end], X[tr_end:te_end], y[tr_end:te_end]
        if task == "cls" and (len(set(ytr.tolist())) < 2 or len(set(yte.tolist())) < 2):
            continue
        p = dict(LGB_PARAMS, seed=seed,
                 objective="binary" if task == "cls" else "regression")
        b = lgb.train(p, lgb.Dataset(Xtr, label=ytr), num_boost_round=rounds)
        pred = b.predict(Xte)
        scores.append(roc_auc_score(yte, pred) if task == "cls" else r2_score(yte, pred))
    if not scores:
        return None
    return float(np.mean(scores)), [round(s, 3) for s in scores]


def multi_seed(X, y, task, folds, rounds, seeds):
    res = [walk_forward(X, y, task, folds, rounds, s) for s in seeds]
    res = [r for r in res if r]
    if not res:
        return None
    means = [r[0] for r in res]
    allfold = [v for r in res for v in r[1]]
    return {"mean": float(np.mean(means)), "std": float(np.std(means)),
            "min_fold": float(np.min(allfold)), "max_fold": float(np.max(allfold)),
            "folds": allfold}


CORRECT = "cls"      # 分类基线 0.5
TASKS = {"cls": roc_auc_score}


# ──────────────────────────────────────────────────────────────
def cmd_labels(a):
    ctx = load_ctx()
    seeds = [int(x) for x in a.seeds.split(",")]
    print(f"信号 {len(ctx['rows'])} 笔 · 特征 {len(ctx['feats'])} 维 · K线 {len(ctx['candles'])} 根")
    print(f"评估：时序前向验证 {a.folds} 折 × {len(seeds)} 个随机种子\n")
    print(f"{'标签口径':<20}{'任务':<5}{'n':>5}{'均值':>8}{'种子σ':>8}{'最差折':>8}{'最好折':>8}  判定")
    for name, task, lab, thr in preset_targets():
        X, y = make_matrix(ctx, ctx["feats"], lab, thr)
        r = multi_seed(X, y, task, a.folds, a.rounds, seeds) if len(y) >= 150 else None
        if not r:
            print(f"{name:<20}{task:<5}{len(y):>5}   样本/折数不足")
            continue
        base = 0.5 if task == "cls" else 0.0
        edge = r["mean"] - base
        judge = ("有边际" if edge >= 0.05 else "极弱" if edge >= 0.03 else "无信号")
        if task == "reg":
            judge = "有解释力" if edge >= 0.05 else "弱" if edge >= 0.0 else "无解释力"
        print(f"{name:<20}{task:<5}{len(y):>5}{r['mean']:>8.3f}{r['std']:>8.3f}"
              f"{r['min_fold']:>8.3f}{r['max_fold']:>8.3f}  {judge}")

    print("\n[对照组] 打乱标签（分类应回到~0.5；回归应 ≤0，否则说明评估有泄漏）")
    rng = np.random.default_rng(0)
    for name, task, lab, thr in preset_targets()[:6]:
        X, y = make_matrix(ctx, ctx["feats"], lab, thr)
        if len(y) < 150:
            continue
        r = walk_forward(X, rng.permutation(y), task, a.folds, a.rounds, a.seed)
        if not r:
            continue
        ok = abs(r[0] - 0.5) < 0.08 if task == "cls" else r[0] <= 0.05
        print(f"  {name:<20}{task:<5}打乱后={r[0]:>7.3f}   {'✓ 正常' if ok else '⚠ 异常'}")
    return 0


def cmd_sweep(a):
    ctx = load_ctx()
    seeds = [int(x) for x in a.seeds.split(",")]
    tps = [float(x) for x in a.tp.split(",")]
    sls = [float(x) for x in a.sl.split(",")]
    hs = [int(x) for x in a.h.split(",")]
    print(f"扫描 {len(tps)}×{len(sls)}×{len(hs)} = {len(tps)*len(sls)*len(hs)} 组 tpsl 口径"
          f"（{a.folds} 折 × {len(seeds)} 种子）\n")
    rows = []
    for tp in tps:
        for sl in sls:
            for h in hs:
                X, y = make_matrix(ctx, ctx["feats"], make_tpsl(tp, sl, h), 0.0)
                r = multi_seed(X, y, "cls", a.folds, a.rounds, seeds)
                if r:
                    rows.append((f"tpsl{tp}/{sl}_h{h}", len(y), r))
    rows.sort(key=lambda x: -x[2]["mean"])
    print(f"{'口径':<20}{'n':>5}{'AUC均值':>9}{'种子σ':>8}{'最差折':>8}{'最好折':>8}")
    for name, n, r in rows[:20]:
        print(f"{name:<20}{n:>5}{r['mean']:>9.3f}{r['std']:>8.3f}"
              f"{r['min_fold']:>8.3f}{r['max_fold']:>8.3f}")
    if rows:
        print(f"\n最优：{rows[0][0]}  AUC={rows[0][2]['mean']:.3f}（最差折 {rows[0][2]['min_fold']:.3f}）")
        print("注意：最差折 < 0.5 说明不稳定，别只看均值。")
    return 0


def cmd_perm(a):
    ctx = load_ctx()
    # 注意：--tp/--sl/--h 在 sweep 里是逗号列表格式，这里取第一个值并转成数值
    h = int(str(a.h).split(",")[0])
    if a.mode == "tpsl":
        lab, task, thr = make_tpsl(float(str(a.tp).split(",")[0]),
                                   float(str(a.sl).split(",")[0]), h), "cls", 0.0
    elif a.mode == "vol":
        lab, task, thr = make_vol_ratio(h), "reg", None
    else:
        lab, task, thr = make_mfe_mae(h), "reg", None
    X, y = make_matrix(ctx, ctx["feats"], lab, thr)
    real = walk_forward(X, y, task, a.folds, a.rounds, a.seed)
    if not real:
        print("折数不足")
        return 1
    base = 0.5 if task == "cls" else 0.0
    print(f"真实 {task} 指标 = {real[0]:.3f}（基线 {base}）  各折 {real[1]}")
    print(f"置换检验：打乱标签 {a.perm} 次，看纯噪声能刷到多少…\n")

    rng = np.random.default_rng(1)
    null = []
    for _ in range(a.perm):
        r = walk_forward(X, rng.permutation(y), task, a.folds, a.rounds, a.seed)
        if r:
            null.append(r[0])
    null = np.asarray(null)
    p = (int((null >= real[0]).sum()) + 1) / (len(null) + 1)
    print(f"噪声分布：均值 {null.mean():.3f}  标准差 {null.std():.3f}  "
          f"95分位 {np.quantile(null, 0.95):.3f}  最大 {null.max():.3f}")
    print(f"\np 值 = {p:.4f}")
    if p < 0.05:
        print("→ 真实指标显著高于噪声分布，这套口径**有统计意义的边际**。")
    elif p < 0.2:
        print("→ 边缘显著，样本量偏小，建议拉长历史或换标的后再确认。")
    else:
        print("→ 与噪声无法区分。这套口径没有边际，换标签比调参有意义。")
    return 0


def cmd_permsweep(a):
    """选择偏倚校正的置换检验。

    普通 perm 问的是：「**指定**这一组口径，纯噪声下能刷到多少？」
    但只要你看过一组结果再挑最好那组，就必须问：「**在同样扫这么多个口径、每次都挑最大**的
    条件下，纯噪声能刷到多少？」—— 否则 p 值会被严重高估（多重比较陷阱）。
    """
    ctx = load_ctx()
    tps = [float(x) for x in a.tp.split(",")]
    sls = [float(x) for x in a.sl.split(",")]
    hs = [int(x) for x in a.h.split(",")]
    print(f"口径网格 {len(tps)}×{len(sls)}×{len(hs)} = {len(tps)*len(sls)*len(hs)} 组")
    mats = []
    for tp in tps:
        for sl in sls:
            for h in hs:
                X, y = make_matrix(ctx, ctx["feats"], make_tpsl(tp, sl, h), 0.0)
                mats.append((f"tpsl{tp}/{sl}_h{h}", X, y))

    real = []
    for name, X, y in mats:
        r = walk_forward(X, y, "cls", a.folds, a.rounds, a.seed)
        if r:
            real.append((name, r[0]))
    real.sort(key=lambda x: -x[1])
    real_max = real[0][1]
    print(f"真实最优：{real[0][0]}  AUC={real_max:.3f}")
    print(f"置换 {a.perm} 次，每次『把所有口径都扫一遍再取最大』…\n")

    rng = np.random.default_rng(1)
    null = []
    for k in range(a.perm):
        best = -1.0
        for _name, X, y in mats:
            r = walk_forward(X, rng.permutation(y), "cls", a.folds, a.rounds, a.seed)
            if r and r[0] > best:
                best = r[0]
        null.append(best)
        if (k + 1) % 10 == 0:
            print(f"  已完成 {k+1}/{a.perm}（当前噪声最大值中位 {np.median(null):.3f}）")
    null = np.asarray(null)
    p = (int((null >= real_max).sum()) + 1) / (len(null) + 1)
    print(f"\n噪声最大值分布：均值 {null.mean():.3f}  标准差 {null.std():.3f}  "
          f"95分位 {np.quantile(null, 0.95):.3f}  最大 {null.max():.3f}")
    print(f"\n校正后 p 值 = {p:.4f}")
    print("→ 这才是『扫了一堆口径挑最好』之后，该拿来看的 p 值。")
    print("  p≥0.05 说明：你挑出来的 0.545，纯运气也能刷到差不多的水平。")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="标签口径实验台（walk-forward 诚实评估）")
    ap.add_argument("cmd", choices=["labels", "sweep", "perm", "permsweep"],
                    default="labels", nargs="?")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--rounds", type=int, default=200)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--seeds", default="42,7,2024")
    ap.add_argument("--tp", default="2,3,4,5")
    ap.add_argument("--sl", default="1.5,2,3")
    ap.add_argument("--h", default="30,50,80")
    ap.add_argument("--mode", default="tpsl", choices=["tpsl", "vol", "mfe"],
                    help="perm 用：tpsl=分类方向, vol=未来波动率, mfe=最大有利/不利偏移")
    ap.add_argument("--perm", type=int, default=200)
    a = ap.parse_args(argv)
    return {"labels": cmd_labels, "sweep": cmd_sweep,
            "perm": cmd_perm, "permsweep": cmd_permsweep}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
