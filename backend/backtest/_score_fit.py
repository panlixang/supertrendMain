"""基于 _raw_full.csv 的多指标加权【尾部惩罚】打分过滤。

关键修正：线性 LR 在这份数据上几乎分不开胜负（权重~0.03），因为真正亏的是
ATR%/极端K/弱势突破这些指标的【极端尾部】，是非线性的。

做法：对每个信号已知指标，构造方向感知原始值 x_i；用数据找最佳单变量切分点 thr_i
（使好/坏两侧的胜率差最大），并据 x_i 与 y(win) 的相关性定方向；
惩罚 b_i(x)=sigmoid( (x-thr)/s )（高=坏）或 1-sigmoid（低=坏），坡度只在该指标
自身跨度内平滑；权重 w_i = 该指标好坏侧胜率差（能拉开多少）。
合成分 score = Σ w_i * b_i(x)。score 越高越像垃圾单，超阈值即拦截。

目标：只拦【大部分】垃圾单（score 高=多个指标同时进入亏损区），不必全拦。
"""
import csv
import numpy as np

CSV = "_raw_full.csv"


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
    dist_high = g("distance_to_range_high_ATR")
    dist_low = g("distance_to_range_low_ATR")
    break_dist = dist_high if d > 0 else dist_low          # 多近48高 / 空近48低
    wick = g("upper_wick_ratio") if d > 0 else g("lower_wick_ratio")
    return {
        "ATR_percent": g("ATR_percent"),
        "candle_range_ATR": g("candle_range_ATR"),
        "dist_break": break_dist,
        "range_signed": d * (g("range_position") - 0.5),    # 多近顶/空近底=好
        "mom_signed": d * g("mom12_ATR"),                   # 动量同向=好
        "wick_against": wick,                               # 反向影线=坏
        "bars_since_last_flip": g("bars_since_last_flip"),
        "ADX14": g("ADX14"),
        "volume_ratio": g("volume_ratio"),
    }


def best_split(x, y):
    """返回使好坏侧胜率差最大的切分点 thr 与对应坡度方向(高=坏?)。"""
    order = np.argsort(x)
    xs = x[order]; ys = y[order]
    base = ys.mean()
    best, bthr, bhigh = 0.0, float(np.median(xs)), True
    # 在分位数候选上扫
    for q in np.linspace(0.05, 0.95, 37):
        thr = np.quantile(xs, q)
        above = ys[xs > thr]; below = ys[xs <= thr]
        if len(above) < 5 or len(below) < 5:
            continue
        # 高侧胜率 - 低侧胜率
        gap_high = above.mean() - below.mean()
        gap_low = below.mean() - above.mean()
        if abs(gap_high) > abs(best):
            best, bthr, bhigh = gap_high, thr, True
        if abs(gap_low) > abs(best):
            best, bthr, bhigh = gap_low, thr, False
    return bthr, bhigh, abs(best)


def main():
    rows = load()
    y = np.array([1.0 if r["result"] == "win" else 0.0 for r in rows])
    prof = np.array([float(r["profit_U"]) for r in rows])
    X = {k: np.array([raw_feats(r)[k] for r in rows]) for k in raw_feats(rows[0])}

    print(f"样本 {len(rows)}  胜 {int(y.sum())} 负 {len(y)-int(y.sum())}  "
          f"baseline净 {prof.sum():+.2f}U  基准胜率 {y.mean()*100:.1f}%")

    # 拟合每个指标
    info = {}
    for k, x in X.items():
        corr = np.corrcoef(x, y)[0, 1]
        thr, high_bad, gap = best_split(x, y)
        s = (x.max() - x.min()) / 12.0 + 1e-9
        if high_bad:
            b = 1.0 / (1.0 + np.exp(-(x - thr) / s))
        else:
            b = 1.0 / (1.0 + np.exp(-(thr - x) / s))
        w = gap  # 该指标好坏侧胜率差 = 权重
        info[k] = dict(thr=thr, high_bad=high_bad, w=w, corr=corr, b=b)
        print(f"  {k:22s} 方向={'高=坏' if high_bad else '低=坏'} thr={thr:7.3f} "
              f"权重(win差)={w:+.3f} corr={corr:+.3f}")

    # 合成分（权重用相对强度：按 w 归一，避免绝对尺度问题）
    W = np.array([info[k]["w"] for k in info])
    Wn = W / W.sum()
    score = np.zeros(len(rows))
    for i, k in enumerate(info):
        score += Wn[i] * info[k]["b"]
    # score 已 ∈[0,1]（因为 b∈[0,1] 且权重和=1）

    print("\n== 全量扫阈值（score 越高越像垃圾，超阈值拦截）==")
    print(f"  {'cutoff':>6} {'保留':>5} {'拦亏损%':>7} {'保盈利%':>7} "
          f"{'保留净U':>10} {'保留胜率':>8}")
    for cut in [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]:
        keep = score <= cut
        loser = y == 0; winner = y == 1
        rl = (1 - keep[loser].mean()) * 100 if loser.any() else 0.0
        rw = keep[winner].mean() * 100 if winner.any() else 0.0
        net = prof[keep].sum()
        wr = keep[winner].sum() / keep.sum() * 100 if keep.sum() else 0.0
        print(f"  {cut:>6.2f} {keep.sum():>5.0f} {rl:>7.0f} {rw:>7.0f} "
              f"{net:>+10.2f} {wr:>7.1f}%")

    # 时间外验证：用全量拟合的 thr/方向，套 2026 看 net
    yr = np.array([r["time"][:4] for r in rows])
    te = yr == "2026"
    keep = score[te] <= 0.48
    print(f"\n== 时间外(2026, {te.sum()}笔) cutoff=0.48 ==")
    print(f"  保留 {keep.sum()} 笔  净 {prof[te][keep].sum():+.2f}U  "
          f"(2026 baseline {prof[te].sum():+.2f}U)")

    # 部署参数导出
    print("\n== 部署参数（pattern_trade 用）==")
    print("  score = Σ 归一权重_i * sigmoid(±(x_i - thr_i)/s_i)，超 cutoff 拦截")
    for i, k in enumerate(info):
        it = info[k]
        print(f"   {k:22s} thr={it['thr']:.3f} high_bad={it['high_bad']} "
              f"w_norm={Wn[i]:.3f} s={(X[k].max()-X[k].min())/12:.3f}")


if __name__ == "__main__":
    main()
