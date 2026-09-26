# -*- coding: utf-8 -*-
"""校验：build_signal_features_1h.py 产出的 V3 列 == 之前复盘结果（498 / +175.45% / PF1.35 / 红字105）。"""
import csv, os
BASE = r"d:\个人项目代码\supertrendMain\backend"
CSV = os.path.join(BASE, "backtest", "st_signals_1h_features.csv")
TH = 2.0
rows = list(csv.DictReader(open(CSV, encoding="utf-8-sig")))

def f(x):
    try: return float(x)
    except: return None

kept = [r for r in rows if r["V3_可执行"] == "TRUE"]
n = len(kept); pnls = [f(r["盈亏"]) or 0 for r in kept]
tp = sum(1 for r in kept if r["止盈止损"] == "TP")
gp = sum(p for p in pnls if p > 0); gl = -sum(p for p in pnls if p < 0)
eq = 1.0; pk = 1.0; mdd = 0.0
for p in pnls:
    eq *= (1 + p / 100); pk = max(pk, eq); mdd = max(mdd, pk - eq)
big = sum(1 for r in kept if (f(r["盈亏"]) or 0) > TH)
print(f"V3_可执行=TRUE: {n} 笔  胜率={tp/n*100:.1f}%  累计={sum(pnls):.2f}%  PF={gp/gl:.2f}  DD={mdd*100:.1f}%  红字={big}")

# 路径分布 & Fuse
from collections import Counter
print("路径分布:", dict(Counter(r["V3_路径"] for r in rows)))
print("分数分布:", dict(Counter(r["V3_分数"] for r in rows)))

path_rows = {p: [r for r in rows if r["V3_路径"] == p] for p in set(r["V3_路径"] for r in rows)}
for p, rs_ in sorted(path_rows.items()):
    if not rs_: continue
    ps = [f(r["盈亏"]) or 0 for r in rs_]
    g = sum(x for x in ps if x > 0); l = -sum(x for x in ps if x < 0)
    print(f"  [{p}] {len(rs_)} 笔 累计={sum(ps):.2f}% PF={g/l if l else 0:.2f} 红字={sum(1 for x in ps if x>TH)}")

# 期望值对照
EXP = (498, 38.0, 175.45, 1.35, 105)
GOT = (n, round(tp / n * 100, 1), round(sum(pnls), 2), round(gp / gl, 2), big)
print("\n期望:", EXP)
print("实得:", GOT)
print("一致 ✅" if n == EXP[0] and abs(sum(pnls) - EXP[2]) < 0.01 and big == EXP[4] else "不一致 ❌")
