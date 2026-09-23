"""诊断 range_signed：真区分度还是拟合噪声？并给出候选修复参数。

方法：
  1) 分位分布 + 分组胜率/净收益（看方向是否单调）
  2) 置换检验：打乱 win/loss 标签 N 次，看 best_split 随机能刷出的最大 gap
     —— 若实际 gap 落在随机分布内，说明该指标的权重是噪声
  3) 当前 (thr,s) 下惩罚 b 的触发率
"""
import csv
import numpy as np

CSV = "_raw_full.csv"
N_PERM = 500


def load():
    out = []
    with open(CSV, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("profit_U") in ("", None) or r.get("result") not in ("win", "loss"):
                continue
            out.append(r)
    return out


def raw_feats(r):
    d = 1.0 if r["direction"] == "buy" else -1.0
    g = lambda k: float(r[k])
    return {
        "ATR_percent": g("ATR_percent"),
        "candle_range_ATR": g("candle_range_ATR"),
        "dist_break": g("distance_to_range_high_ATR") if d > 0 else g("distance_to_range_low_ATR"),
        "range_signed": d * (g("range_position") - 0.5),
        "mom_signed": d * g("mom12_ATR"),
        "wick_against": g("upper_wick_ratio") if d > 0 else g("lower_wick_ratio"),
        "bars_since_last_flip": g("bars_since_last_flip"),
        "ADX14": g("ADX14"),
        "volume_ratio": g("volume_ratio"),
    }


def best_split(x, y):
    order = np.argsort(x)
    xs, ys = x[order], y[order]
    best, bthr, bhigh = 0.0, float(np.median(xs)), True
    for q in np.linspace(0.05, 0.95, 37):
        thr = np.quantile(xs, q)
        above, below = ys[xs > thr], ys[xs <= thr]
        if len(above) < 5 or len(below) < 5:
            continue
        gh, gl = above.mean() - below.mean(), below.mean() - above.mean()
        if abs(gh) > abs(best):
            best, bthr, bhigh = gh, thr, True
        if abs(gl) > abs(best):
            best, bthr, bhigh = gl, thr, False
    return bthr, bhigh, abs(best)


def main():
    rows = load()
    y = np.array([1.0 if r["result"] == "win" else 0.0 for r in rows])
    prof = np.array([float(r["profit_U"]) for r in rows])
    keys = list(raw_feats(rows[0]).keys())
    X = {k: np.array([raw_feats(r)[k] for r in rows]) for k in keys}
    print(f"样本 {len(rows)}  基准胜率 {y.mean()*100:.1f}%  净 {prof.sum():+.1f}U\n")

    # ── 1) 置换检验：各指标的 gap 是真信号还是噪声 ──
    print("== 置换检验（打乱标签 500 次，随机 best_split 能刷出的 gap）==")
    print(f"  {'指标':<22}{'实际gap':>9}{'随机均值':>10}{'随机P95':>9}"
          f"{'实际分位':>9}  判定")
    rng = np.random.default_rng(0)
    for k in keys:
        x = X[k]
        _, _, gap = best_split(x, y)
        rnd = np.empty(N_PERM)
        for i in range(N_PERM):
            rnd[i] = best_split(x, rng.permutation(y))[2]
        pct = (rnd < gap).mean() * 100
        verdict = ("真实" if pct >= 95 else ("边缘" if pct >= 85 else "疑似噪声"))
        print(f"  {k:<22}{gap:>9.3f}{rnd.mean():>10.3f}"
              f"{np.quantile(rnd, 0.95):>9.3f}{pct:>8.0f}%  {verdict}")

    # ── 2) range_signed 分布与分组表现 ──
    x = X["range_signed"]
    print("\n== range_signed 分布 ==")
    print(f"  min={x.min():+.3f} max={x.max():+.3f} mean={x.mean():+.3f} "
          f"std={x.std():.3f}")
    for q in [0, 5, 10, 25, 50, 75, 90, 95, 100]:
        print(f"    P{q:<3}= {np.quantile(x, q/100):+.3f}")

    print("\n== range_signed 十分位分组表现（看是否单调）==")
    print(f"  {'分组':<12}{'笔数':>5}{'胜率':>8}{'净U':>10}{'均值U':>9}")
    edges = np.quantile(x, np.linspace(0, 1, 11))
    for i in range(10):
        m = (x >= edges[i]) & (x <= edges[i + 1]) if i == 9 else \
            (x >= edges[i]) & (x < edges[i + 1])
        if m.sum() == 0:
            continue
        print(f"  [{edges[i]:+.2f},{edges[i+1]:+.2f}){m.sum():>5}"
              f"{y[m].mean()*100:>7.1f}%{prof[m].sum():>+10.1f}{prof[m].mean():>+9.2f}")

    # ── 3) 当前参数下 b 的触发率 ──
    thr, s = 0.418, 0.057
    b = 1.0 / (1.0 + np.exp(-(x - thr) / s))
    print(f"\n== 当前 thr={thr} s={s} 下惩罚 b 的触发情况 ==")
    print(f"  b>0.01: {(b>0.01).mean()*100:.1f}%   b>0.1: {(b>0.1).mean()*100:.1f}%   "
          f"b>0.5: {(b>0.5).mean()*100:.1f}%   b>0.9: {(b>0.9).mean()*100:.1f}%")
    print(f"  b 均值 {b.mean():.3f}  →  实际贡献权重 ≈ 0.095*{b.mean():.3f}"
          f" = {0.095*b.mean():.4f}（名义 0.095，几乎作废）")

    # ── 4) 候选修复参数扫描 ──
    print("\n== 候选 (thr, s) 扫描：看 b 与实际胜率的秩相关 + 触发率 ==")
    print(f"  {'thr':>7}{'s':>7}{'b>0.5占比':>10}{'与y秩相关':>11}{'高b组胜率':>11}"
          f"{'低b组胜率':>11}{'gap':>8}")
    from scipy.stats import spearmanr
    for thr2 in [0.10, 0.20, 0.30, 0.418]:
        for s2 in [0.057, 0.10, 0.15, 0.20]:
            b2 = 1.0 / (1.0 + np.exp(-(x - thr2) / s2))
            rho = spearmanr(b2, y).correlation
            hi, lo = b2 >= 0.5, b2 < 0.5
            if hi.sum() < 5 or lo.sum() < 5:
                continue
            g = y[lo].mean() - y[hi].mean()
            print(f"  {thr2:>7.3f}{s2:>7.3f}{(b2>0.5).mean()*100:>9.1f}%"
                  f"{rho:>11.3f}{y[hi].mean()*100:>10.1f}%"
                  f"{y[lo].mean()*100:>10.1f}%{g:>+8.3f}")


if __name__ == "__main__":
    main()
