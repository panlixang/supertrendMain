# -*- coding: utf-8 -*-
"""
最终权重收敛:
1) 只保留 A/B/C 三段相关方向全部一致的特征 -> 稳健核心权重
2) 对比两种用法: 精选头部 vs 剔除底部
3) 输出 好信号/坏信号 的特征画像(可人工执行的规则)
z-score 参数固定取自 A 段(前40%), 全程无未来函数
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

n = len(data)
a_end, b_end = int(n * 0.40), int(n * 0.70)
A, B, C = data[:a_end], data[a_end:b_end], data[b_end:]
ALL = data


def ranks(v):
    idx = sorted(range(len(v)), key=lambda i: v[i]); r = [0.0] * len(v); i = 0
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
    n_ = len(a); ma = sum(a) / n_; mb = sum(b) / n_
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(n_))
    da = math.sqrt(sum((x - ma) ** 2 for x in a)); db = math.sqrt(sum((x - mb) ** 2 for x in b))
    return num / (da * db) if da and db else 0.0


def corr(seg, f):
    xs = [x[f] for x in seg if x[f] is not None]
    ys = [x["pnl"] for x in seg if x[f] is not None]
    return pear(ranks(xs), ranks(ys))


FEATS = ["ATR_pct", "ADX14", "break_dist_d", "rsi_d", "squeeze", "ER20", "align",
         "bbw_rank", "vol_ratio", "st_dist_d", "bars_since_flip", "body_atr",
         "range_atr", "ma_slope_d", "close_pos_d"]
print("=" * 88)
print("【三段一致性总表】 A=拟合 B=一致性 C=样本外")
print("=" * 88)
print(f"{'字段':<18}{'corr_A':>9}{'corr_B':>9}{'corr_C':>9}{'三段同号?':>11}{'稳健度':>9}")
robust = {}
for f in FEATS:
    ca, cb, cc = corr(A, f), corr(B, f), corr(C, f)
    same = ca * cb > 0 and ca * cc > 0
    mag = min(abs(ca), abs(cb), abs(cc))
    if same and mag >= 0.02:
        robust[f] = (ca, cb, cc, mag)
    print(f"{f:<18}{ca:>9.4f}{cb:>9.4f}{cc:>9.4f}{'是' if same else '否':>11}{mag:>9.4f}")

if not robust:
    print("\n无特征三段同号 -> 该口径下无可稳定利用的预测力")
    raise SystemExit

print("\n通过稳健筛选的特征(三段同号且最小|corr|>=0.02):")
tot = sum(v[3] for v in robust.values())
FINAL = {}
for f, (ca, cb, cc, mag) in robust.items():
    w = mag / tot * 100
    sg = 1 if ca >= 0 else -1
    FINAL[f] = (w, sg)
    print(f"  {f:<16} 权重 {w:>5.1f}%   方向 {'越大越好' if sg > 0 else '越小越好'}   "
          f"(corr {ca:+.3f}/{cb:+.3f}/{cc:+.3f})")

stats = {}
for f in FEATS:
    vs = [x[f] for x in A if x[f] is not None]
    m = sum(vs) / len(vs)
    sd = math.sqrt(sum((v - m) ** 2 for v in vs) / len(vs))
    stats[f] = (m, sd if sd else 1.0)


def score(x):
    s = 0.0
    for f, (w, sg) in FINAL.items():
        v = x[f]
        z = 0.0 if v is None else (v - stats[f][0]) / stats[f][1]
        s += sg * (w / 100.0) * z
    return s


for x in ALL:
    x["score"] = score(x)


def mnp(seg):
    return sum(x["pnl"] for x in seg) / len(seg) if seg else 0.0


def wr(seg):
    return sum(x["win"] for x in seg) / len(seg) * 100 if seg else 0.0


print("\n" + "=" * 88)
print("【最终权重在各段的表现】按得分降序分5档")
print("=" * 88)
for nm, seg in [("A段", A), ("B段", B), ("C段★样本外", C), ("全样本", ALL)]:
    s2 = sorted(seg, key=lambda x: -x["score"]); k = len(s2) // 5
    qs = [s2[i * k:(i + 1) * k] if i < 4 else s2[4 * k:] for i in range(5)]
    print(f"\n{nm}: 基线 {mnp(seg):+.3f}%  胜率 {wr(seg):.1f}%")
    print("  " + "  ".join(f"Q{i+1}:{mnp(q):+.3f}%" for i, q in enumerate(qs)))
    for pn, p in [("前20%", .20), ("前30%", .30), ("前50%", .50)]:
        m = max(1, int(len(s2) * p)); t = s2[:m]
        print(f"    精选{pn}: 平均 {mnp(t):+.3f}%  胜率 {wr(t):.1f}%  合计 {sum(x['pnl'] for x in t):+.1f}%")
    # 剔除底部20%
    keep_seg = s2[:len(s2) - k]
    print(f"    剔除底部20%后: 平均 {mnp(keep_seg):+.3f}%  胜率 {wr(keep_seg):.1f}%  "
          f"合计 {sum(x['pnl'] for x in keep_seg):+.1f}%  (基线合计 {sum(x['pnl'] for x in seg):+.1f}%)")

# ---- 好/坏信号画像 ----
print("\n" + "=" * 88)
print("【信号画像】全样本 得分最高20% vs 最低20% 的特征均值")
print("=" * 88)
s2 = sorted(ALL, key=lambda x: -x["score"]); k = len(s2) // 5
top, bot = s2[:k], s2[-k:]
print(f"{'字段':<18}{'头部20%':>12}{'底部20%':>12}{'差异':>12}")
for f in FEATS:
    tv = [x[f] for x in top if x[f] is not None]
    bv = [x[f] for x in bot if x[f] is not None]
    tm = sum(tv) / len(tv) if tv else float("nan")
    bm = sum(bv) / len(bv) if bv else float("nan")
    print(f"{f:<18}{tm:>12.3f}{bm:>12.3f}{tm - bm:>12.3f}")
print(f"\n头部20%: 平均盈亏 {mnp(top):+.3f}%  胜率 {wr(top):.1f}%")
print(f"底部20%: 平均盈亏 {mnp(bot):+.3f}%  胜率 {wr(bot):.1f}%")
