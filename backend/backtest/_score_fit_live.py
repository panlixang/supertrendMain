"""用【实盘出场口径】重新拟合 ⑥ 的权重，并按置换检验显著性筛掉噪声指标。

与 _score_fit.py（反向平仓标签）的区别：
  1) 标签来自 bt_pattern_page.backtest()：TP1 1.5% 平70% + 保本 + ST跟踪 + 2%兜底
     —— 与实盘同源，避免"用反向平仓标签训练、部署到 TP1 出场"的口径错配。
  2) best_split 加最小样本约束（每侧 >= max(10, 8%n)），防止切到尾部极小样本。
  3) 权重 = max(0, gap - E[gap_random])   ← 减掉 best_split 多重检验的系统性偏差
     且要求置换分位 >= 80% 才保留，否则权重置 0（噪声指标直接淘汰）。
  4) s 用 (P95-P5)/6（主体分布跨度），不再用 (max-min)/12（受离群值主导）。
  5) 做双向交叉验证：2025训→2026测、2026训→2025测，看权重是否稳定。

用法：python _score_fit_live.py
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import bt_pattern_page as BP  # noqa: E402

SYM = "BTC-USDT"
BP.NOTIONAL = 100.0
BP.FEE = 0.05 / 100
N_PERM = 500
MIN_SIDE_FRAC = 0.08
PCT_KEEP = 80.0          # 置换分位门槛，低于此视为噪声 → 权重置 0
# 稳健化 1：切分点只许落在 [20%, 80%] 分位之间。
# 原因：best_split 在尾部小样本处能刷出很大的 gap，但那是过拟合，跨年必失效
# （实测 2026训→2025测 曾出现「只保留 1 笔」的灾难）。
Q_LO, Q_HI = 0.20, 0.80
# 稳健化 2：权重用 (gap-rand)^0.5 压缩差距，避免单一指标（ATR%）独占 70% 后
# 随市场环境漂移而整体失效。
W_POW = 0.5

KEYS = ["ATR_percent", "candle_range_ATR", "dist_break", "range_signed",
        "mom_signed", "wick_against", "bars_since_last_flip", "ADX14",
        "volume_ratio"]


def build_dataset():
    """返回 (X: dict[str, ndarray], y, prof, years, rows) —— live 出场口径标签。"""
    base, h4 = BP.load(SYM, use_cache=True)
    if not base:
        raise SystemExit("no data")
    sigs, opens, highs, lows, closes, up, dn, flip_idx = BP.build_signals(base, h4)
    tr = BP.backtest(sigs, highs, lows, closes, up, dn, flip_idx)   # 实盘出场
    tss = [c["ts"] for c in base]
    import datetime as dt
    years = []
    rows = []
    for s, t in zip(sigs, tr):
        f = s["feat"]
        d = s["dir"]
        rows.append({
            "ATR_percent": f["ATR_percent"],
            "candle_range_ATR": f["candle_range_ATR"],
            "dist_break": (f["distance_to_range_high_ATR"] if d > 0
                           else f["distance_to_range_low_ATR"]),
            "range_signed": d * (f["range_position"] - 0.5),
            "mom_signed": d * f["mom12_ATR"],
            "wick_against": (f["upper_wick_ratio"] if d > 0
                             else f["lower_wick_ratio"]),
            "bars_since_last_flip": f["bars_since_last_flip"],
            "ADX14": f["ADX14"],
            "volume_ratio": f["volume_ratio"],
        })
        years.append(dt.datetime.fromtimestamp(tss[s["i"]] / 1000,
                                               dt.timezone.utc).year)
    X = {k: np.array([r[k] for r in rows], dtype=float) for k in KEYS}
    prof = np.array([t["pnl"] for t in tr], dtype=float)
    y = (prof > 0).astype(float)
    return X, y, prof, np.array(years), tr


def best_split(x, y, min_side):
    """最佳单变量切分：返回 (thr, high_bad, gap)。gap = 坏侧胜率 - 好侧胜率。"""
    order = np.argsort(x)
    xs, ys = x[order], y[order]
    best, bthr, bhigh = 0.0, float(np.median(xs)), True
    for q in np.linspace(0.05, 0.95, 37):
        if q < 0.20 or q > 0.80:          # 稳健化：不许切在尾部
            continue
        thr = np.quantile(xs, q)
        above, below = ys[xs > thr], ys[xs <= thr]
        if len(above) < min_side or len(below) < min_side:
            continue
        gh, gl = above.mean() - below.mean(), below.mean() - above.mean()
        if abs(gh) > abs(best):
            best, bthr, bhigh = gh, thr, True
        if abs(gl) > abs(best):
            best, bthr, bhigh = gl, thr, False
    return bthr, bhigh, abs(best)


def fit(X, y, keys=KEYS, n_perm=N_PERM, verbose=True):
    """拟合一套 (thr, s, dir, w)。权重已按显著性筛选并归一化。"""
    n = len(y)
    min_side = max(10, int(MIN_SIDE_FRAC * n))
    rng = np.random.default_rng(0)
    info, wr = {}, {}
    for k in keys:
        x = X[k]
        thr, high_bad, gap = best_split(x, y, min_side)
        rnd = np.empty(n_perm)
        for i in range(n_perm):
            rnd[i] = best_split(x, rng.permutation(y), min_side)[2]
        pct = (rnd < gap).mean() * 100
        # 减掉随机基线（多重检验偏差），过显著性门槛，再压缩差距避免单指标独大
        w_raw = (max(0.0, gap - rnd.mean()) ** W_POW) if pct >= PCT_KEEP else 0.0
        s = max((np.quantile(x, 0.95) - np.quantile(x, 0.05)) / 6.0, 1e-9)
        info[k] = dict(thr=thr, high_bad=high_bad, s=s, gap=gap,
                       pct=pct, rnd_mean=rnd.mean(), w_raw=w_raw)
        wr[k] = w_raw
        if verbose:
            tag = ("KEEP" if pct >= PCT_KEEP else "DROP")
            print(f"  {k:<22}{'high=bad' if high_bad else 'low=bad ':>9} "
                  f"thr={thr:>8.3f} s={s:>7.3f} gap={gap:+.3f} "
                  f"rand={rnd.mean():.3f} pct={pct:>3.0f}% "
                  f"w_raw={w_raw:+.3f}  {tag}")
    tot = sum(wr.values())
    w = {k: (wr[k] / tot if tot > 0 else 0.0) for k in keys}
    return info, w


def score_of(X, info, w, keys=KEYS, idx=None):
    n = len(X[keys[0]]) if idx is None else len(idx)
    sc = np.zeros(n)
    for k in keys:
        if w[k] <= 0:
            continue
        x = X[k] if idx is None else X[k][idx]
        it = info[k]
        z = (x - it["thr"]) / it["s"] if it["high_bad"] else (it["thr"] - x) / it["s"]
        sc += w[k] / (1.0 + np.exp(-z))
    return sc


def scan_cut(sc, prof, y, label, cuts=(0.30, 0.35, 0.40, 0.45, 0.48, 0.50, 0.55)):
    print(f"\n  -- {label}: baseline n={len(prof)} net={prof.sum():+.2f}U "
          f"win={y.mean()*100:.1f}%")
    print(f"     {'cut':>6}{'keep':>6}{'blockLoss%':>12}{'keepWin%':>10}"
          f"{'net':>10}{'win%':>8}")
    best = None
    for c in cuts:
        keep = sc <= c
        if keep.sum() == 0:
            continue
        loser, winner = y == 0, y == 1
        bl = (1 - keep[loser].mean()) * 100 if loser.any() else 0.0
        kw = keep[winner].mean() * 100 if winner.any() else 0.0
        net = prof[keep].sum()
        wr = keep[winner].sum() / keep.sum() * 100
        print(f"     {c:>6.2f}{keep.sum():>6}{bl:>11.0f}%{kw:>9.0f}%"
              f"{net:>+10.2f}{wr:>7.1f}%")
        if best is None or net > best[1]:
            best = (c, net)
    if best:
        print(f"     -> best cut={best[0]:.2f} net={best[1]:+.2f}U")


def main():
    X, y, prof, years, tr = build_dataset()
    print(f"== live 出场口径数据集 ==  n={len(y)}  win={y.mean()*100:.1f}%  "
          f"net={prof.sum():+.2f}U")
    for yy in sorted(set(years)):
        m = years == yy
        print(f"   {yy}: n={m.sum():>4} win={y[m].mean()*100:>5.1f}% "
              f"net={prof[m].sum():>+8.2f}U")

    print("\n== 全量拟合（置换检验 + 显著性筛权重）==")
    info, w = fit(X, y)
    print(f"\n  归一化权重（Σw=1）：")
    for k in KEYS:
        print(f"    {k:<22} w={w[k]:.3f}")
    sc = score_of(X, info, w)
    scan_cut(sc, prof, y, "全量 in-sample")
    # 部署场景：上线用的是全量拟合参数，分别套到各年看真实预期
    for yy in (2025, 2026):
        m = years == yy
        if m.sum() < 30:
            continue
        scan_cut(sc[m], prof[m], y[m], f"全量参数 -> {yy}")

    # ── 交叉验证 ──
    for tr_y, te_y in ((2025, 2026), (2026, 2025)):
        a, b = years == tr_y, years == te_y
        if a.sum() < 30 or b.sum() < 30:
            continue
        info2, w2 = fit({k: X[k][a] for k in KEYS}, y[a], verbose=False)
        scb = score_of(X, info2, w2, idx=np.where(b)[0])
        print(f"\n== 交叉验证：{tr_y} 训练 -> {te_y} 测试 ==")
        for k in KEYS:
            print(f"    {k:<22} thr={info2[k]['thr']:>8.3f} "
                  f"{'high' if info2[k]['high_bad'] else 'low '} "
                  f"w={w2[k]:.3f} pct={info2[k]['pct']:>3.0f}%")
        scan_cut(scb, prof[b], y[b], f"{te_y} out-of-sample")

    # ── 部署参数 ──
    print("\n== 可部署参数（pattern_trade.SCORE_MODEL）==")
    print("   # 拟合口径：live 出场（TP1 1.5%+保本+ST跟踪）；权重已按置换检验筛选")
    for k in KEYS:
        it = info[k]
        arrow = "(x-thr)/s" if it["high_bad"] else "(thr-x)/s"
        print(f"   {k:<22} thr={it['thr']:.3f} s={it['s']:.3f} w={w[k]:.3f}  "
              f"b=sigmoid({arrow})")


if __name__ == "__main__":
    main()
