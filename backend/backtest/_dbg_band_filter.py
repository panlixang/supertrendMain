# -*- coding: utf-8 -*-
"""
验证假设: "两端极值去掉、中间段保留" 是否优于线性打分?
1) 对每个特征画十分位盈亏剖面, 判断形状: U型(两端好) / 倒U型(中间好) / 单调
2) 在训练集上学习最优保留区间[lo,hi], 再在样本外检验集上验证
3) 组合多特征区间过滤
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
    sig = int(r["signal"]); pnl = gv(r, "pnl_pct")
    if pnl is None:
        continue
    st_dist = gv(r, "st_distance_atr"); cp = gv(r, "close_position")
    bd = gv(r, "break_distance_atr"); ms = gv(r, "MA30_slope"); rsi = gv(r, "RSI14")
    data.append({
        "time": r["time"], "pnl": pnl, "win": 1 if pnl > 0 else 0,
        "st_dist_d": None if st_dist is None else st_dist * sig,
        "close_pos_d": None if cp is None else (cp if sig == 1 else 1 - cp),
        "break_dist_d": None if bd is None else bd * sig,
        "ma_slope_d": None if ms is None else ms * sig,
        "rsi_d": None if rsi is None else (rsi - 50) * sig,
        "ER20": gv(r, "ER20"), "ADX14": gv(r, "ADX14"), "body_atr": gv(r, "body_atr"),
        "range_atr": gv(r, "range_atr"), "ATR_pct": gv(r, "ATR_pct"),
        "bars_since_flip": gv(r, "bars_since_flip"), "vol_ratio": gv(r, "vol_ratio"),
        "bbw_rank": gv(r, "bbw_rank"),
        "align": 1.0 if r["align"] == "1" else 0.0,
        "squeeze": 1.0 if r["squeeze"] == "1" else 0.0,
    })

FEATS = ["ATR_pct", "ADX14", "break_dist_d", "rsi_d", "ER20", "squeeze", "align",
         "bbw_rank", "vol_ratio", "st_dist_d", "bars_since_flip", "body_atr",
         "range_atr", "ma_slope_d", "close_pos_d"]

n = len(data); cut = int(n * 0.7)
train, test = data[:cut], data[cut:]
print(f"训练集 {len(train)} ({train[0]['time'][:10]}~{train[-1]['time'][:10]})   "
      f"检验集 {len(test)} ({test[0]['time'][:10]}~{test[-1]['time'][:10]})")
tb = sum(x["pnl"] for x in test) / len(test)
print(f"检验集基线: 平均 {tb:+.3f}%  合计 {sum(x['pnl'] for x in test):+.1f}%\n")


def mnp(s):
    return sum(x["pnl"] for x in s) / len(s) if s else 0.0


def prof(seg, f, nb=10):
    vals = sorted([x for x in seg if x[f] is not None], key=lambda x: x[f])
    k = len(vals) // nb
    out = []
    for b in range(nb):
        s2 = vals[b * k:(b + 1) * k] if b < nb - 1 else vals[(nb - 1) * k:]
        out.append(mnp(s2))
    return out


# ---------- 1. 形状判定 ----------
print("=" * 92)
print("【十分位盈亏剖面】D1=最小值档 ... D10=最大值档   (训练 / 检验)")
print("=" * 92)
print(f"{'字段':<17}{'两端均值':>9}{'中间均值':>9}{'形状判断':>12}   十分位剖面(训练)")
shape = {}
for f in FEATS:
    pt = prof(train, f)
    pc = prof(test, f)
    tails_t = sum(pt[:2] + pt[-2:]) / 4
    mid_t = sum(pt[2:8]) / 6
    tails_c = sum(pc[:2] + pc[-2:]) / 4
    mid_c = sum(pc[2:8]) / 6
    # 形状: 两端 vs 中间 (训练), 并在检验集确认
    if tails_t - mid_t > 0.15 and tails_c - mid_c > 0.15:
        s = "U型(两端好)"
    elif mid_t - tails_t > 0.15 and mid_c - tails_c > 0.15:
        s = "倒U(中间好)"
    elif abs(tails_t - mid_t) <= 0.15 and abs(tails_c - mid_c) <= 0.15:
        s = "平坦"
    else:
        s = "不一致"
    shape[f] = s
    ps = " ".join(f"{v:+.2f}" for v in pt)
    print(f"{f:<17}{tails_t:>9.3f}{mid_t:>9.3f}{s:>12}   {ps}")

# ---------- 2. 训练集学区间, 检验集验证 ----------
print("\n" + "=" * 92)
print("【区间过滤】训练集学最优保留区间 -> 检验集验证 (保留=区间内, 剔除=两端)")
print("=" * 92)


def quantile(sv, p):
    if p <= 0:
        return float("-inf")
    if p >= 100:
        return float("inf")
    i = int(len(sv) * p / 100)
    return sv[min(i, len(sv) - 1)]


bands = {}
print(f"{'字段':<17}{'保留区间':>22}{'保留n':>7}{'保留均盈':>10}{'剔除n':>7}{'剔除均盈':>10}{'判定':>10}")
for f in FEATS:
    sv = sorted(x[f] for x in train if x[f] is not None)
    best = None
    for lo_p in (0, 10, 20, 30, 40):
        for hi_p in (60, 70, 80, 90, 100):
            if hi_p - lo_p < 40:
                continue
            tlo, thi = quantile(sv, lo_p), quantile(sv, hi_p)
            ret = [x for x in train if x[f] is not None and tlo <= x[f] <= thi]
            if len(ret) < 0.35 * len(sv):
                continue
            m = mnp(ret)
            if best is None or m > best[0]:
                best = (m, tlo, thi, lo_p, hi_p)
    if best is None:
        continue
    _, tlo, thi, lo_p, hi_p = best
    bands[f] = (tlo, thi)
    rt = [x for x in test if x[f] is not None and tlo <= x[f] <= thi]
    ex = [x for x in test if x[f] is not None and not (tlo <= x[f] <= thi)]
    ok = "有效" if mnp(rt) > tb + 0.05 else ("无用" if mnp(rt) < tb - 0.05 else "持平")
    lo_s = "-inf" if tlo == float("-inf") else f"{tlo:.3f}"
    hi_s = "+inf" if thi == float("inf") else f"{thi:.3f}"
    print(f"{f:<17}{('[' + lo_s + ',' + hi_s + ']'):>22}{len(rt):>7}{mnp(rt):>10.3f}"
          f"{len(ex):>7}{mnp(ex):>10.3f}{ok:>10}")

# ---------- 3. 组合区间过滤 ----------
print("\n" + "=" * 92)
print("【组合区间过滤】训练集按保留后均盈排序选前K个特征, 要求全部落在区间内才做")
print("=" * 92)
rank = []
for f, (tlo, thi) in bands.items():
    ret = [x for x in train if x[f] is not None and tlo <= x[f] <= thi]
    rank.append((mnp(ret), f))
rank.sort(reverse=True)
print("训练集单特征保留后均盈排序: " + ", ".join(f"{f}={m:+.2f}%" for m, f in rank[:6]))

for K in (2, 3, 4):
    sel = [f for _, f in rank[:K]]
    def keep(x):
        return all(x[f] is not None and bands[f][0] <= x[f] <= bands[f][1] for f in sel)
    rt = [x for x in test if keep(x)]
    if len(rt) < 10:
        print(f"\nK={K} {sel}: 检验集只剩 {len(rt)} 笔, 样本过少, 不可用")
        continue
    print(f"\nK={K} {sel}")
    print(f"  检验集保留 {len(rt)}/{len(test)} 笔 ({len(rt)/len(test)*100:.0f}%)")
    print(f"  平均盈亏 {mnp(rt):+.3f}%  胜率 {sum(x['win'] for x in rt)/len(rt)*100:.1f}%  "
          f"合计 {sum(x['pnl'] for x in rt):+.1f}%   [基线 {tb:+.3f}%]")

# ---------- 4. 对照: 线性打分 vs 区间过滤 ----------
print("\n" + "=" * 92)
print("【对照】线性权重打分(ATR_pct+ADX14) 在检验集的表现")
print("=" * 92)
stats = {}
for f in ("ATR_pct", "ADX14"):
    vs = [x[f] for x in train if x[f] is not None]
    m = sum(vs) / len(vs)
    sd = math.sqrt(sum((v - m) ** 2 for v in vs) / len(vs))
    stats[f] = (m, sd)
def lscore(x):
    s = 0.0
    for f, w in (("ATR_pct", 0.692), ("ADX14", 0.308)):
        v = x[f]
        z = 0.0 if v is None else (v - stats[f][0]) / stats[f][1]
        s += -1 * w * z
    return s
st2 = sorted(test, key=lambda x: -lscore(x))
k = len(st2) // 5
for i, nm in enumerate(["Q1最高", "Q2", "Q3", "Q4", "Q5最低"]):
    seg = st2[i * k:(i + 1) * k] if i < 4 else st2[4 * k:]
    print(f"  {nm}: n={len(seg):<4} 平均 {mnp(seg):+.3f}%")
top = st2[:k]
print(f"  线性打分 前20%: 平均 {mnp(top):+.3f}%  合计 {sum(x['pnl'] for x in top):+.1f}%")
