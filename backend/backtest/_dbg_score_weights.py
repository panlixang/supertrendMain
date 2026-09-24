# -*- coding: utf-8 -*-
"""
基于 st_signals_1h.csv 探索: 哪些"开仓时已知"的字段对盈亏有预测力, 并给出综合权重.
- 排除所有未来函数字段(future_*, success_label, reverse_signal_price, exit_result, pnl)
- 方向类特征统一转成"方向中性": 值越大 = 越有利于信号方向
- 训练(前70%)算权重 -> 检验(后30%)做样本外验证
"""
import csv
import math

PATH = r"d:\个人项目代码\supertrendMain\backend\backtest\st_signals_1h.csv"
rows = list(csv.DictReader(open(PATH, encoding="utf-8-sig")))


def gv(r, k):
    v = r.get(k, "")
    if v == "" or v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


data = []
for r in rows:
    sig = int(r["signal"])
    pnl = gv(r, "pnl_pct")
    if pnl is None:
        continue
    st_dist = gv(r, "st_distance_atr")
    cp = gv(r, "close_position")
    bd = gv(r, "break_distance_atr")
    ms = gv(r, "MA30_slope")
    rsi = gv(r, "RSI14")
    d = {
        "time": r["time"],
        "pnl": pnl,
        "win": 1 if pnl > 0 else 0,
        # 方向中性化: 越大 = 越顺信号方向
        "st_dist_d": None if st_dist is None else st_dist * sig,
        "close_pos_d": None if cp is None else (cp if sig == 1 else 1 - cp),
        "break_dist_d": None if bd is None else bd * sig,
        "ma_slope_d": None if ms is None else ms * sig,
        "rsi_d": None if rsi is None else (rsi - 50) * sig,
        "ER20": gv(r, "ER20"),
        "ADX14": gv(r, "ADX14"),
        "body_atr": gv(r, "body_atr"),
        "range_atr": gv(r, "range_atr"),
        "ATR_pct": gv(r, "ATR_pct"),
        "bars_since_flip": gv(r, "bars_since_flip"),
        "vol_ratio": gv(r, "vol_ratio"),
        "bbw_rank": gv(r, "bbw_rank"),
        "align": 1.0 if r["align"] == "1" else 0.0,
        "squeeze": 1.0 if r["squeeze"] == "1" else 0.0,
    }
    data.append(d)

FEATS = ["st_dist_d", "close_pos_d", "break_dist_d", "ma_slope_d", "rsi_d", "ER20",
         "ADX14", "body_atr", "range_atr", "ATR_pct", "bars_since_flip", "vol_ratio",
         "bbw_rank", "align", "squeeze"]


def ranks(v):
    idx = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and v[idx[j + 1]] == v[idx[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            r[idx[k]] = avg
        i = j + 1
    return r


def pear(a, b):
    n = len(a)
    ma = sum(a) / n
    mb = sum(b) / n
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((x - mb) ** 2 for x in b))
    return num / (da * db) if da and db else 0.0


def spear(a, b):
    return pear(ranks(a), ranks(b))


n = len(data)
cut = int(n * 0.7)
train, test = data[:cut], data[cut:]
print(f"总样本 {n}  训练 {len(train)} ({train[0]['time']} ~ {train[-1]['time']})  "
      f"检验 {len(test)} ({test[0]['time']} ~ {test[-1]['time']})")
base_pnl = sum(x["pnl"] for x in data) / n
base_win = sum(x["win"] for x in data) / n * 100
print(f"全样本基线: 平均盈亏 {base_pnl:+.3f}%  胜率 {base_win:.1f}%\n")

# ---- 1. 各特征与盈亏的秩相关(训练集) ----
print("=" * 78)
print("【训练期】各特征 vs 盈亏(pnl_pct) 的 Spearman 相关")
print("=" * 78)
print(f"{'字段':<18}{'相关系数':>10}{'|r|':>8}{'分5档平均盈亏(低→高)':>10}")
res = {}
for f in FEATS:
    xs, ys = [], []
    for x in train:
        if x[f] is not None:
            xs.append(x[f]); ys.append(x["pnl"])
    c = spear(xs, ys)
    # 五分位平均盈亏
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    k = len(order) // 5
    q = []
    for b in range(5):
        seg = order[b * k:(b + 1) * k] if b < 4 else order[4 * k:]
        q.append(sum(ys[i] for i in seg) / len(seg))
    res[f] = c
    qs = " ".join(f"{v:+.2f}" for v in q)
    print(f"{f:<18}{c:>10.4f}{abs(c):>8.4f}   {qs}")

# ---- 2. 权重 = |r| 归一化 ----
print("\n" + "=" * 78)
print("【权重方案】权重 ∝ |相关系数|, 符号 = 相关方向(正=越大越好)")
print("=" * 78)
tot = sum(abs(v) for v in res.values())
weights = {}
print(f"{'字段':<18}{'相关系数':>10}{'权重%':>9}{'含义(得分越高越好)':>10}")
meaning = {
    "st_dist_d": "价格离ST线越远(趋势延续空间)",
    "close_pos_d": "收盘收在顺势那一端(长上影/长下影)",
    "break_dist_d": "突破前20根高/低的幅度",
    "ma_slope_d": "MA30斜率与信号同向",
    "rsi_d": "RSI偏向信号方向(多头偏高/空头偏低)",
    "ER20": "效率系数=趋势顺畅度",
    "ADX14": "趋势强度",
    "body_atr": "K线实体大小",
    "range_atr": "当根振幅",
    "ATR_pct": "波动率水平",
    "bars_since_flip": "距上次翻转根数(趋势新鲜度)",
    "vol_ratio": "放量程度",
    "bbw_rank": "布林带宽百分位",
    "align": "与4h方向一致",
    "squeeze": "处于低波动挤压",
}
for f in FEATS:
    w = abs(res[f]) / tot * 100
    sign = 1 if res[f] >= 0 else -1
    weights[f] = (w, sign)
    print(f"{f:<18}{res[f]:>10.4f}{w:>9.1f}   {meaning[f]}")

# ---- 3. 用训练集参数做 z-score, 在检验集上打分 ----
stats = {}
for f in FEATS:
    vs = [x[f] for x in train if x[f] is not None]
    m = sum(vs) / len(vs)
    sd = math.sqrt(sum((v - m) ** 2 for v in vs) / len(vs))
    stats[f] = (m, sd if sd else 1.0)


def score(x):
    s = 0.0
    for f in FEATS:
        w, sg = weights[f]
        v = x[f]
        z = 0.0 if v is None else (v - stats[f][0]) / stats[f][1]
        s += sg * (w / 100.0) * z
    return s


for x in test:
    x["score"] = score(x)

test.sort(key=lambda x: -x["score"])
print("\n" + "=" * 78)
print("【样本外检验】按综合得分从高到低分5档")
print("=" * 78)
k = len(test) // 5
print(f"{'档位':<8}{'数量':>6}{'平均盈亏%':>12}{'胜率%':>10}{'合计盈亏%':>12}")
for b in range(5):
    seg = test[b * k:(b + 1) * k] if b < 4 else test[4 * k:]
    mp = sum(x["pnl"] for x in seg) / len(seg)
    wr = sum(x["win"] for x in seg) / len(seg) * 100
    print(f"Q{b+1}(高→低){len(seg):>6}{mp:>12.3f}{wr:>10.1f}{sum(x['pnl'] for x in seg):>12.1f}")

for pctname, pct in [("前10%", 0.10), ("前20%", 0.20), ("前30%", 0.30)]:
    m = int(len(test) * pct)
    seg = test[:m]
    mp = sum(x["pnl"] for x in seg) / len(seg)
    wr = sum(x["win"] for x in seg) / len(seg) * 100
    tb = sum(x["pnl"] for x in test) / len(test)
    print(f"\n筛选{pctname}({len(seg)}笔): 平均盈亏 {mp:+.3f}%  胜率 {wr:.1f}%  "
          f"合计 {sum(x['pnl'] for x in seg):+.1f}%   [检验集基线 {tb:+.3f}%]")
