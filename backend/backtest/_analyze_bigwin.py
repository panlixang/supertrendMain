# -*- coding: utf-8 -*-
"""分析 盈亏>2% 的"大赢家"单子的特征规律。"""
import csv, statistics as st

SRC = r"C:\Users\Administrator\AppData\Local\Temp\codebuddy-dropped-files\46a5ccbd-5091-496d-b717-8e8a681729ae\st_signals_1h_features.csv"
rows = list(csv.DictReader(open(SRC, encoding="utf-8-sig")))
def fnum(x):
    try: return float(x)
    except: return None

num_cols = ["1h_MA30斜率","4h_MA30斜率","1h_MA30距离","4h_MA30距离",
            "前20根_涨跌幅","前20根_ATR变化","前20根_ER变化","前20根_ADX变化","前20根_ST距离变化",
            "前50根_ST翻转次数","前50根_高低点次数","前50根_趋势持续时间",
            "前100根_趋势生命周期","前100根_横盘周期","前100根_波动周期"]

# 目标: 盈亏>2% 的大赢家
TH=2.0
target=[r for r in rows if (fnum(r["盈亏"]) or 0)>TH]
rest=[r for r in rows if (fnum(r["盈亏"]) or 0)<=TH]
print(f"阈值 盈亏>{TH}% : 大赢家={len(target)}  其余={len(rest)}  (总{len(rows)})")
print(f"大赢家中 TP={sum(1 for r in target if r['止盈止损']=='TP')} SL={sum(1 for r in target if r['止盈止损']=='SL')}")
print(f"大赢家平均盈亏={st.mean([fnum(r['盈亏']) for r in target]):.2f}%")

def mean(g,c):
    v=[fnum(r[c]) for r in g if fnum(r[c]) is not None]
    return st.mean(v) if v else float('nan')

print(f"\n{'特征':16s} {'大赢家均值':>12s} {'其余均值':>12s} {'差':>10s}")
diffs=[]
for c in num_cols:
    d=mean(target,c)-mean(rest,c)
    diffs.append((c,d))
    print(f"{c:16s} {mean(target,c):12.4f} {mean(rest,c):12.4f} {d:10.4f}")
diffs.sort(key=lambda x:-abs(x[1]))

# 哪些 regime 出大赢家
from collections import Counter, defaultdict
reg_t=Counter(r["趋势区间"] for r in target)
reg_a=Counter(r["趋势区间"] for r in rows)
print("\n--- 各区间 大赢家占比 ---")
for k in reg_a:
    print(f"{k:8s} 大赢家 {reg_t[k]:3d}/{reg_a[k]:3d} = {reg_t[k]/reg_a[k]*100:4.1f}%")

# 4h方向一致性
def align(g):
    s=sum(1 for r in g if int(r['信号'])*int(r['4h方向'])>0); return s,len(g)
s,n=align(target); print(f"\n大赢家 信号-4h同向: {s}/{n}={s/n*100:.1f}%")

# 预测规则扫描: 保留"看起来像大赢家"的信号, 看大赢家覆盖率 & 该子集的大赢家密度
print("\n--- 单因子过滤: 看能否富集大赢家 ---")
def scan(col, op, ths):
    for t in ths:
        pred = (lambda v:v>=t) if op=="ge" else (lambda v:v<=t)
        keep=[r for r in rows if pred(fnum(r[col]) or 0)]
        if not keep: continue
        kt=sum(1 for r in keep if (fnum(r['盈亏']) or 0)>TH)
        density=kt/len(keep)*100
        cov=kt/len(target)*100
        print(f"  {col} {op}{t}: 保留{len(keep):3d} 大赢家密度={density:4.1f}% 覆盖大赢家={cov:4.0f}%")
scan("前20根_ER变化","ge",[0,0.05,0.1,0.15])
scan("前20根_ADX变化","ge",[0,2,4,6])
scan("前20根_ST距离变化","le",[-0.5,-1,-1.5])   # 价格远离带(趋势加强), 多正空负->信号反向
scan("4h_MA30斜率","ge",[0,0.5,1])
scan("前100根_横盘周期","le",[20,25,30])
scan("前50根_ST翻转次数","le",[2,3])

# 组合规则
print("\n--- 组合规则 ---")
def ev(pred,label):
    keep=[r for r in rows if pred(r)]
    if not keep: print(f"[{label}] 0"); return
    kt=sum(1 for r in keep if (fnum(r['盈亏']) or 0)>TH)
    pnls=[fnum(r['盈亏']) for r in keep]
    print(f"[{label}] 保留{len(keep)} 大赢家密度={kt/len(keep)*100:.1f}% 覆盖={kt/len(target)*100:.0f}% 累计={sum(pnls):.1f}%")
def P(r,c): return fnum(r[c]) or 0
ev(lambda r: P(r,'前20根_ER变化')>0 and P(r,'前20根_ADX变化')>0, "ER>0 & ADX>0")
ev(lambda r: P(r,'前20根_ER变化')>0.05 and P(r,'前20根_ADX变化')>0 and P(r,'4h_MA30斜率')>0, "ER>0.05&ADX>0&4h斜率>0")
ev(lambda r: P(r,'前20根_ST距离变化')< -0.5 and P(r,'前20根_ADX变化')>0, "ST距离加强&ADX>0")
ev(lambda r: P(r,'前100根_横盘周期')<=25 and P(r,'前20根_ER变化')>0, "横盘少&ER>0")
