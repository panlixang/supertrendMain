# -*- coding: utf-8 -*-
import csv, statistics as st
SRC = r"C:\Users\Administrator\AppData\Local\Temp\codebuddy-dropped-files\46a5ccbd-5091-496d-b717-8e8a681729ae\st_signals_1h_features.csv"
rows = list(csv.DictReader(open(SRC, encoding="utf-8-sig")))
def fnum(x):
    try: return float(x)
    except: return None
TH=2.0
def isbig(r): return (fnum(r["盈亏"]) or 0)>TH
def P(r,c): return fnum(r[c]) or 0

def report(keep, label):
    n=len(keep)
    if n==0: print(f"[{label}] 0笔"); return
    tp=sum(1 for r in keep if r["止盈止损"]=="TP")
    sl=sum(1 for r in keep if r["止盈止损"]=="SL")
    pnls=[fnum(r["盈亏"]) for r in keep]
    big=sum(1 for r in keep if isbig(r))
    print(f"[{label}]")
    print(f"  保留 {n}/808 笔 | TP={tp} SL={sl} 胜率={tp/n*100:.1f}%")
    print(f"  平均盈亏={st.mean(pnls):.3f}%  累计盈亏={sum(pnls):.2f}%")
    print(f"  大赢家(>2%)密度={big/n*100:.1f}% ({big}笔)  全量大赢家基准=137/808=17.0%")

# 条件1: ER>0 & ADX>0
c1=[r for r in rows if P(r,"前20根_ER变化")>0 and P(r,"前20根_ADX变化")>0]
report(c1, "条件1: 前20根_ER变化>0 且 前20根_ADX变化>0")

# 条件2: 剔除趋势衰减期 + ER>0 & ADX>0
c2=[r for r in rows if r["趋势区间"]!="趋势衰减期" and P(r,"前20根_ER变化")>0 and P(r,"前20根_ADX变化")>0]
report(c2, "条件2: 剔除趋势衰减期 + ER>0 & ADX>0")

# 对照: 全量
report(rows, "对照: 全量808")
