# -*- coding: utf-8 -*-
"""把用户的三套 Gate (Long/Short/NoTrade) 套到 808 笔回测, 统计删除的亏损单/红字盈利单, PF/DD/交易数。"""
import csv, statistics as st
SRC = r"C:\Users\Administrator\AppData\Local\Temp\codebuddy-dropped-files\46a5ccbd-5091-496d-b717-8e8a681729ae\st_signals_1h_features.csv"
TH=2.0
rows=list(csv.DictReader(open(SRC, encoding="utf-8-sig")))
def fnum(x):
    try: return float(x)
    except: return None

def pnl(r): return fnum(r["盈亏"]) or 0
def is_big(r): return pnl(r)>TH          # 红色盈利单
def is_sl(r): return r["止盈止损"]=="SL"  # 普通亏损单

# ---- Gate 定义 ----
def long_gate(r):
    s4=fnum(r["4h_MA30斜率"]); d1=fnum(r["1h_MA30距离"])
    if s4 is None or d1 is None: return False
    return (s4>0.16) or (s4>0 and d1>0.13)

def short_gate(r):
    vc=fnum(r["前100根_波动周期"]); adx=fnum(r["前20根_ADX变化"]); d4=fnum(r["4h_MA30距离"]); er=fnum(r["前20根_ER变化"])
    if None in (vc,adx,d4,er): return False
    if vc<9.5 and adx<3.25: return True
    if vc>9.5 and d4>-0.7 and er>-0.06: return True
    return False

def no_trade(r):
    adx=fnum(r["前20根_ADX变化"]); atr=fnum(r["前20根_ATR变化"])
    if adx is None or atr is None: return False
    return (adx>-6.9) and (atr<0.37)

def passes(r):
    if no_trade(r): return False
    sig=int(r["信号"])
    if sig==1: return long_gate(r)
    if sig==-1: return short_gate(r)
    return False

# ---- 统计工具 ----
def equity_dd(pnls):
    eq=1.0; peak=1.0; mdd=0.0
    for p in pnls:
        eq*=(1+p/100.0); peak=max(peak,eq); mdd=max(mdd, peak-eq)
    return mdd*100

def pf(pnls):
    gp=sum(p for p in pnls if p>0); gl=-sum(p for p in pnls if p<0)
    return (gp/gl) if gl>0 else float('inf')

def report(label, trades):
    n=len(trades)
    if n==0: print(f"[{label}] 0笔"); return
    pnls=[pnl(r) for r in trades]
    tp=sum(1 for r in trades if r["止盈止损"]=="TP")
    gp=sum(p for p in pnls if p>0); gl=-sum(p for p in pnls if p<0)
    print(f"[{label}]")
    print(f"  交易数={n}  TP={tp} SL={n-tp} 胜率={tp/n*100:.1f}%")
    print(f"  累计盈亏={sum(pnls):.2f}%  平均={st.mean(pnls):.3f}%")
    print(f"  PF={pf(pnls):.2f}  DD={equity_dd(pnls):.1f}%")

all_t=rows
kept=[r for r in rows if passes(r)]
removed=[r for r in rows if not passes(r)]

print(f"全量 {len(all_t)} 笔 | 通过Gate {len(kept)} 笔 | 被删 {len(removed)} 笔\n")

# 删除明细
del_sl=sum(1 for r in removed if is_sl(r))                 # 普通亏损单
del_big=sum(1 for r in removed if is_big(r))               # 红色盈利单
del_smallwin=sum(1 for r in removed if (not is_sl(r)) and (not is_big(r)) and pnl(r)>0)  # 小盈利(0~2%)
del_flat=sum(1 for r in removed if pnl(r)==0)
print("--- 被删除的单子明细 ---")
print(f"  普通亏损单(SL)         : {del_sl} 笔")
print(f"  红色盈利单(>2% TP)     : {del_big} 笔")
print(f"  小盈利单(0~2% TP)      : {del_smallwin} 笔")
print(f"  持平(0)                : {del_flat} 笔")
print(f"  删除合计               : {len(removed)} 笔")
print(f"  (红字盈利单占全部红字 {del_big}/{sum(1 for r in all_t if is_big(r))} = {del_big/sum(1 for r in all_t if is_big(r))*100:.0f}%)")

print()
report("全量808(基线)", all_t)
report("通过三Gate后", kept)

# 各Gate独立贡献(只看被NoTrade删 vs 被方向Gate删)
nt=[r for r in rows if no_trade(r)]
print(f"\nNoTradeZone 单独命中(将被禁): {len(nt)} 笔, 其中红字盈利单 {sum(1 for r in nt if is_big(r))} 笔, SL {sum(1 for r in nt if is_sl(r))} 笔")
