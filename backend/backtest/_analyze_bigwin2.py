# -*- coding: utf-8 -*-
"""方向对齐后的特征: 找出大赢家(盈亏>2%)真正规律。"""
import csv, statistics as st
SRC = r"C:\Users\Administrator\AppData\Local\Temp\codebuddy-dropped-files\46a5ccbd-5091-496d-b717-8e8a681729ae\st_signals_1h_features.csv"
rows = list(csv.DictReader(open(SRC, encoding="utf-8-sig")))
def fnum(x):
    try: return float(x)
    except: return None
TH=2.0
def isbig(r): return (fnum(r["盈亏"]) or 0)>TH
target=[r for r in rows if isbig(r)]; rest=[r for r in rows if not isbig(r)]

# 方向对齐特征
def aligned(r, col):
    v=fnum(r[col]) or 0; s=int(r["信号"])
    return v*s   # 正向=顺着信号方向强化

pairs=[("ST距离变化(对齐)","前20根_ST距离变化"),
       ("涨跌幅(对齐)","前20根_涨跌幅"),
       ("ER变化(对齐)","前20根_ER变化"),
       ("ADX变化(对齐)","前20根_ADX变化"),
       ("4h斜率(对齐)","4h_MA30斜率"),
       ("1h斜率(对齐)","1h_MA30斜率")]

def mean(g,fn):
    v=[fn(r) for r in g]; return st.mean(v)
print(f"{'特征':16s} {'大赢家':>10s} {'其余':>10s} {'差':>10s}")
for name,col in pairs:
    fn=lambda r,c=col: aligned(r,c)
    d=mean(target,fn)-mean(rest,fn)
    print(f"{name:16s} {mean(target,fn):10.4f} {mean(rest,fn):10.4f} {d:10.4f}")

# 关键: 趋势强化(对齐ST距离变化>0) 且 信号方向趋势带已顺势
print("\n--- 方向对齐 ST距离变化 阈值扫描 (价格顺着信号方向远离带=趋势加速) ---")
for t in [0,0.5,1.0]:
    keep=[r for r in rows if aligned(r,"前20根_ST距离变化")>t]
    if not keep: continue
    kb=sum(1 for r in keep if isbig(r))
    print(f"  对齐ST距离>{t}: 保留{len(keep):3d} 大赢家密度={kb/len(keep)*100:4.1f}% 覆盖={kb/len(target)*100:4.0f}%")

# 组合: 趋势加速(对齐ST>0) & 非衰减期
def ev(pred,label):
    keep=[r for r in rows if pred(r)]
    if not keep: print(f"[{label}] 0"); return
    kb=sum(1 for r in keep if isbig(r))
    pnls=[fnum(r['盈亏']) for r in keep]
    print(f"[{label}] 保留{len(keep):3d} 大赢家密度={kb/len(keep)*100:4.1f}% 覆盖={kb/len(target)*100:4.0f}% 累计={sum(pnls):7.1f}%")
ev(lambda r: aligned(r,"前20根_ST距离变化")>0, "对齐ST距离>0")
ev(lambda r: aligned(r,"前20根_ST距离变化")>0 and r["趋势区间"]!="趋势衰减期", "对齐ST>0 & 非衰减")
ev(lambda r: aligned(r,"前20根_ST距离变化")>0 and (fnum(r["前20根_ER变化"]) or 0)>0, "对齐ST>0 & ER>0")
ev(lambda r: aligned(r,"前20根_ST距离变化")>0 and (fnum(r["前20根_ER变化"]) or 0)>0 and r["趋势区间"]!="趋势衰减期", "三条件")

# 信号方向分布
from collections import Counter
print("\n大赢家 信号方向:", Counter(r["信号"] for r in target))
print("全部 信号方向:", Counter(r["信号"] for r in rows))
