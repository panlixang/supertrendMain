# -*- coding: utf-8 -*-
"""NoTradeZone V2 (ER20<0.22 & |MA30距离_ATR|<0.5 & |ST距离变化|<0.2) + Long/Short Gate, 套808回测。"""
import sys, os, json, csv, statistics as st
BASE = r"d:\个人项目代码\supertrendMain\backend"
sys.path.insert(0, BASE)
from indicators import super_trend, ta_sma

OUT_JSON = os.path.join(BASE, "backtest", "btc_1h_full.json")
SRC = r"C:\Users\Administrator\AppData\Local\Temp\codebuddy-dropped-files\46a5ccbd-5091-496d-b717-8e8a681729ae\st_signals_1h_features.csv"
TH=2.0

d=json.load(open(OUT_JSON,encoding="utf-8")); base=d["base"]
o=[b["o"] for b in base]; h=[b["h"] for b in base]; l=[b["l"] for b in base]
c=[b["c"] for b in base]; ts=[b["ts"] for b in base]; n=len(c)
st_res=super_trend(o,h,l,c,periods=10,multiplier=3.0,change_atr=True)
atr=st_res["atr"]; trend=st_res["trend"]; up=st_res["up"]; dn=st_res["dn"]
ma30=ta_sma(c,30)
# ST距离(带符号)
st_dist=[0.0]*n
for i in range(n):
    if trend[i] is None or atr[i] in (None,0): st_dist[i]=0.0
    else: line=up[i] if trend[i]==1 else dn[i]; st_dist[i]=(c[i]-line)/atr[i]
# ER20(当前效率比)
def er20_at(i):
    if i<20: return 0.0
    num=abs(c[i]-c[i-20]); den=sum(abs(c[k]-c[k-1]) for k in range(i-20,i))
    return num/den if den>0 else 0.0
# ATR化 MA30距离
def ma30_dist_atr(i):
    if ma30[i] in (None,0) or atr[i] in (None,0): return 0.0
    return abs(c[i]-ma30[i])/atr[i]

rows=list(csv.DictReader(open(SRC,encoding="utf-8-sig")))
import calendar, datetime
def msec(s): return calendar.timegm(datetime.datetime.strptime(s,"%Y/%m/%d %H:%M").timetuple())
base_sec={int(b["ts"]/1000):i for i,b in enumerate(base)}
def fnum(x):
    try: return float(x)
    except: return None
def pnl(r): return fnum(r["盈亏"]) or 0
def is_big(r): return pnl(r)>TH
def is_sl(r): return r["止盈止损"]=="SL"

# Long/Short Gate (用CSV列)
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

# NoTrade V2: 三条件同时满足=纯震荡
def no_trade_v2(i):
    if i<20: return False
    er=er20_at(i); md=ma30_dist_atr(i); sdc=abs(st_dist[i]-st_dist[i-20])
    return (er<0.22) and (md<0.5) and (sdc<0.2)

def passes(r):
    i=base_sec.get(msec(r["时间"]))
    if i is None: return False
    if no_trade_v2(i): return False
    sig=int(r["信号"])
    if sig==1: return long_gate(r)
    if sig==-1: return short_gate(r)
    return False

all_t=rows
kept=[r for r in rows if passes(r)]
removed=[r for r in rows if not passes(r)]

print(f"全量 {len(all_t)} | 通过 {len(kept)} | 被删 {len(removed)}")

del_sl=sum(1 for r in removed if is_sl(r))
del_big=sum(1 for r in removed if is_big(r))
del_smallwin=sum(1 for r in removed if (not is_sl(r)) and (not is_big(r)) and pnl(r)>0)
bigtot=sum(1 for r in all_t if is_big(r))
print("\n--- 被删除明细 ---")
print(f"  普通亏损单(SL)     : {del_sl}")
print(f"  红色盈利单(>2% TP) : {del_big}  (占全部红字 {del_big}/{bigtot} = {del_big/bigtot*100:.0f}%)")
print(f"  小盈利单(0~2% TP)  : {del_smallwin}")
print(f"  删除合计           : {len(removed)}")

def equity_dd(pnls):
    eq=1.0; peak=1.0; mdd=0.0
    for p in pnls:
        eq*=(1+p/100.0); peak=max(peak,eq); mdd=max(mdd,peak-eq)
    return mdd*100
def pf(pnls):
    gp=sum(p for p in pnls if p>0); gl=-sum(p for p in pnls if p<0)
    return gp/gl if gl>0 else float('inf')
def report(label,trades):
    n=len(trades)
    if n==0: print(f"[{label}] 0"); return
    pnls=[pnl(r) for r in trades]; tp=sum(1 for r in trades if r["止盈止损"]=="TP")
    kb=sum(1 for r in trades if is_big(r))
    print(f"[{label}] 交易数={n} TP={tp} SL={n-tp} 胜率={tp/n*100:.1f}%")
    print(f"  累计={sum(pnls):.2f}% 平均={st.mean(pnls):.3f}% PF={pf(pnls):.2f} DD={equity_dd(pnls):.1f}% 红字密度={kb/n*100:.1f}%")

print()
report("全量808(基线)",all_t)
report("V2三Gate后",kept)

# NoTrade V2 单独命中
nt=[r for r in rows if no_trade_v2(base_sec.get(msec(r["时间"])))]
print(f"\nNoTradeV2 单独禁: {len(nt)} 笔, 红字 {sum(1 for r in nt if is_big(r))} 笔, SL {sum(1 for r in nt if is_sl(r))} 笔")
