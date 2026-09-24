# -*- coding: utf-8 -*-
"""
正期望值检验:
1) 毛收益 vs 扣手续费后净收益 (按不同费率)
2) t 检验 / bootstrap 置信区间 -> 判断是否统计显著
3) 收益集中度: 去掉最好的1笔/3笔后还剩多少
策略: 基线(全做) / 线性打分前20% / 单边规则ATR_pct阈值
"""
import csv
import math
import random

random.seed(42)
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
    pnl = gv(r, "pnl_pct")
    if pnl is None:
        continue
    data.append({"time": r["time"], "pnl": pnl, "win": 1 if pnl > 0 else 0,
                 "ATR_pct": gv(r, "ATR_pct"), "ADX14": gv(r, "ADX14")})

n = len(data); cut = int(n * 0.7)
train, test = data[:cut], data[cut:]

# z 参数与 ATR 阈值均只从训练集学习
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
        s += -w * z
    return s


# 单边阈值: 训练集上搜索, 要求保留>=50%
best = None
ats = sorted(x["ATR_pct"] for x in train if x["ATR_pct"] is not None)
for p in range(50, 95, 5):
    th = ats[int(len(ats) * p / 100)]
    ret = [x for x in train if x["ATR_pct"] is not None and x["ATR_pct"] <= th]
    if len(ret) < 0.5 * len(ats):
        continue
    m = sum(x["pnl"] for x in ret) / len(ret)
    if best is None or m > best[0]:
        best = (m, th, p)
ATR_TH = best[1]
print(f"训练集学到的单边阈值: ATR_pct <= {ATR_TH:.3f} (第{best[2]}分位), 训练集保留后均盈 {best[0]:+.3f}%\n")


def stat(seg, name):
    if not seg:
        return
    v = [x["pnl"] for x in seg]
    m = sum(v) / len(v)
    sd = math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1)) if len(v) > 1 else 0.0
    se = sd / math.sqrt(len(v))
    t = m / se if se else 0.0
    sv = sorted(v)
    med = sv[len(sv) // 2]
    bm = []
    for _ in range(2000):
        bm.append(sum(random.choice(v) for _ in v) / len(v))
    bm.sort()
    lo, hi = bm[int(0.025 * len(bm))], bm[int(0.975 * len(bm))]
    top1 = max(v)
    s3 = sorted(v, reverse=True)[:3]
    print(f"{name}: n={len(v)}")
    print(f"   毛平均 {m:+.3f}%  中位 {med:+.3f}%  标准差 {sd:.2f}  t值 {t:+.2f}  "
          f"95%CI [{lo:+.3f}%, {hi:+.3f}%]")
    print(f"   胜率 {sum(x['win'] for x in seg)/len(seg)*100:.1f}%  合计 {sum(v):+.1f}%")
    for fee in (0.10, 0.20):
        print(f"   扣费{fee:.2f}%(双边)后: 平均 {m-fee:+.3f}%  合计 {sum(v)-fee*len(v):+.1f}%"
              f"   {'仍为正' if m-fee > 0 else '转负'}")
    print(f"   集中度: 最好1笔 {top1:+.2f}%  前3笔合计 {sum(s3):+.1f}%  "
          f"剔除前3笔后平均 {(sum(v)-sum(s3))/(len(v)-3):+.3f}%")


print("=" * 76)
print("策略A: 基线 —— 所有信号全做")
print("=" * 76)
stat(data, "  全样本基线")
stat(test, "  C段(样本外)基线")

print("\n" + "=" * 76)
print("策略B: 线性打分(ATR_pct 69.2% + ADX14 30.8%, 均越小越好) 取前20%")
print("=" * 76)
for nm, seg in [("全样本", data), ("C段★", test)]:
    s2 = sorted(seg, key=lambda x: -lscore(x))
    k = max(1, int(len(s2) * 0.20))
    stat(s2[:k], f"  {nm}前20%")

print("\n" + "=" * 76)
print(f"策略C: 单边规则 —— 只做 ATR_pct <= {ATR_TH:.3f} 的信号")
print("=" * 76)
for nm, seg in [("全样本", data), ("C段★", test)]:
    s2 = [x for x in seg if x["ATR_pct"] is not None and x["ATR_pct"] <= ATR_TH]
    stat(s2, f"  {nm}低波动子集")

print("\n" + "=" * 76)
print("策略D: 反向对照组 —— 只做被过滤掉的高波动信号")
print("=" * 76)
for nm, seg in [("全样本", data), ("C段★", test)]:
    s2 = [x for x in seg if x["ATR_pct"] is not None and x["ATR_pct"] > ATR_TH]
    stat(s2, f"  {nm}高波动子集")
