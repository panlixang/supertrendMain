# -*- coding: utf-8 -*-
"""
稳健权重: A段(前40%)定权重 -> B段(40%~70%)验一致性 -> C段(后30%)样本外检验
只保留 A/B 两段相关方向一致的特征, 其余权重置 0(视为噪声)
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

FEATS = ["st_dist_d", "close_pos_d", "break_dist_d", "ma_slope_d", "rsi_d", "ER20",
         "ADX14", "body_atr", "range_atr", "ATR_pct", "bars_since_flip", "vol_ratio",
         "bbw_rank", "align", "squeeze"]

n = len(data)
a_end, b_end = int(n * 0.40), int(n * 0.70)
A, B, C = data[:a_end], data[a_end:b_end], data[b_end:]
print(f"A段 {len(A)} ({A[0]['time'][:10]}~{A[-1]['time'][:10]})  "
      f"B段 {len(B)} ({B[0]['time'][:10]}~{B[-1]['time'][:10]})  "
      f"C段 {len(C)} ({C[0]['time'][:10]}~{C[-1]['time'][:10]})")


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


def spear(a, b):
    return pear(ranks(a), ranks(b))


def corr(seg, f):
    xs = [x[f] for x in seg if x[f] is not None]
    ys = [x["pnl"] for x in seg if x[f] is not None]
    return spear(xs, ys)


TH = 0.03
print("\n" + "=" * 84)
print("【A/B 两段一致性筛选】  corr_A / corr_B 同号且 |corr| >= 0.03 才保留")
print("=" * 84)
print(f"{'字段':<18}{'corr_A':>9}{'corr_B':>9}{'corr_C':>9}{'一致?':>8}{'权重%':>8}")
cA = {f: corr(A, f) for f in FEATS}
cB = {f: corr(B, f) for f in FEATS}
cC = {f: corr(C, f) for f in FEATS}
keep = {}
for f in FEATS:
    ok = (cA[f] * cB[f] > 0) and abs(cA[f]) >= TH and abs(cB[f]) >= TH
    if ok:
        keep[f] = abs(cA[f]) + abs(cB[f])
tot = sum(keep.values()) if keep else 1.0
for f in FEATS:
    w = keep[f] / tot * 100 if f in keep else 0.0
    print(f"{f:<18}{cA[f]:>9.4f}{cB[f]:>9.4f}{cC[f]:>9.4f}{'保留' if f in keep else '剔除':>8}{w:>8.1f}")

if not keep:
    print("\n没有特征通过一致性筛选 -> 该数据集在'反向信号出场'口径下几乎无可用预测力")
    raise SystemExit

weights = {f: (keep[f] / tot * 100, 1 if cA[f] >= 0 else -1) for f in keep}

# z-score 参数只用 A 段
stats = {}
for f in FEATS:
    vs = [x[f] for x in A if x[f] is not None]
    m = sum(vs) / len(vs)
    sd = math.sqrt(sum((v - m) ** 2 for v in vs) / len(vs))
    stats[f] = (m, sd if sd else 1.0)


def score(x):
    s = 0.0
    for f, (w, sg) in weights.items():
        v = x[f]
        z = 0.0 if v is None else (v - stats[f][0]) / stats[f][1]
        s += sg * (w / 100.0) * z
    return s


def report(seg, name):
    seg = sorted(seg, key=lambda x: -score(x))
    k = len(seg) // 5
    print(f"\n--- {name} 按得分分5档 ---")
    print(f"{'档位':<10}{'数量':>6}{'平均盈亏%':>12}{'胜率%':>10}{'合计盈亏%':>12}")
    for b in range(5):
        s2 = seg[b * k:(b + 1) * k] if b < 4 else seg[4 * k:]
        print(f"Q{b+1}{len(s2):>8}{sum(x['pnl'] for x in s2)/len(s2):>12.3f}"
              f"{sum(x['win'] for x in s2)/len(s2)*100:>10.1f}{sum(x['pnl'] for x in s2):>12.1f}")
    tb = sum(x["pnl"] for x in seg) / len(seg)
    tbw = sum(x["win"] for x in seg) / len(seg) * 100
    print(f"基线: 平均 {tb:+.3f}%  胜率 {tbw:.1f}%  (样本 {len(seg)})")
    for pn, p in [("前10%", .10), ("前20%", .20), ("前30%", .30)]:
        m = max(1, int(len(seg) * p)); s2 = seg[:m]
        print(f"  筛选{pn}({len(s2)}笔): 平均 {sum(x['pnl'] for x in s2)/len(s2):+.3f}%  "
              f"胜率 {sum(x['win'] for x in s2)/len(s2)*100:.1f}%  "
              f"合计 {sum(x['pnl'] for x in s2):+.1f}%")


print("\n" + "=" * 84)
print("【权重方案】(仅保留通过一致性筛选的特征)")
print("=" * 84)
for f, (w, sg) in sorted(weights.items(), key=lambda kv: -kv[1][0]):
    print(f"  {f:<18} 权重 {w:>5.1f}%   方向 {'越大越好(+1)' if sg > 0 else '越小越好(-1)'}")
report(A, "A段(拟合)")
report(B, "B段(一致性)")
report(C, "C段(样本外★)")
