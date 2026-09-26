# -*- coding: utf-8 -*-
"""针对 Lost Red 的发现, 实验: 放松 Long Gate / 多头专属启动旁路, 测PF/DD/红字覆盖。"""
import sys, os, json, csv, statistics as st
BASE = r"d:\个人项目代码\supertrendMain\backend"
sys.path.insert(0, BASE)
from indicators import super_trend, ta_sma
OUT_JSON=os.path.join(BASE,"backtest","btc_1h_full.json")
SRC=r"C:\Users\Administrator\AppData\Local\Temp\codebuddy-dropped-files\46a5ccbd-5091-496d-b717-8e8a681729ae\st_signals_1h_features.csv"
TH=2.0
d=json.load(open(OUT_JSON,encoding="utf-8"));base=d["base"]
o=[b["o"] for b in base];h=[b["h"] for b in base];l=[b["l"] for b in base];c=[b["c"] for b in base];ts=[b["ts"] for b in base];n=len(c)
st_res=super_trend(o,h,l,c,periods=10,multiplier=3.0,change_atr=True)
atr=st_res["atr"];trend=st_res["trend"];up=st_res["up"];dn=st_res["dn"];flips=sorted(f["i"] for f in st_res["flips"])
ma30=ta_sma(c,30);atr_pct=[(atr[i]/c[i]*100 if(atr[i] and c[i]) else 0.0) for i in range(n)]
st_dist=[0.0]*n
for i in range(n):
    if trend[i] is None or atr[i] in(None,0):st_dist[i]=0.0
    else:line=up[i] if trend[i]==1 else dn[i];st_dist[i]=(c[i]-line)/atr[i]
def er20_at(i):
    if i<20:return 0.0
    num=abs(c[i]-c[i-20]);den=sum(abs(c[k]-c[k-1]) for k in range(i-20,i));return num/den if den>0 else 0.0
def flip50(i):return sum(1 for f in flips if i-50<f<=i)
rows=list(csv.DictReader(open(SRC,encoding="utf-8-sig")))
import calendar,datetime
def msec(s):return calendar.timegm(datetime.datetime.strptime(s,"%Y/%m/%d %H:%M").timetuple())
base_sec={int(b["ts"]/1000):i for i,b in enumerate(base)}
def fnum(x):
    try:return float(x)
    except:return None
def pnl(r):return fnum(r["盈亏"]) or 0
def is_big(r):return pnl(r)>TH
def long_gate_v2(r):
    s4=fnum(r["4h_MA30斜率"]);d1=fnum(r["1h_MA30距离"])
    if s4 is None or d1 is None:return False
    return (s4>0.16) or (s4>0 and d1>0.13)
def short_gate(r):
    vc=fnum(r["前100根_波动周期"]);adx=fnum(r["前20根_ADX变化"]);d4=fnum(r["4h_MA30距离"]);er=fnum(r["前20根_ER变化"])
    if None in(vc,adx,d4,er):return False
    if vc<9.5 and adx<3.25:return True
    if vc>9.5 and d4>-0.7 and er>-0.06:return True
    return False
def no_trade_v2(i):
    if i<20:return False
    return (er20_at(i)<0.22) and (abs(c[i]-ma30[i])/atr[i] if(ma30[i] and atr[i]) else 0)<0.5 and abs(st_dist[i]-st_dist[i-20])<0.2

# 各变体 Long Gate
def lg_relaxA(r):  # 去掉 s4>0 耦合: s4>0.16 或 d1>0.13
    s4=fnum(r["4h_MA30斜率"]);d1=fnum(r["1h_MA30距离"])
    if s4 is None or d1 is None:return False
    return (s4>0.16) or (d1>0.13)
def lg_relaxB(r):  # s4>-0.3 且 d1>0.13
    s4=fnum(r["4h_MA30斜率"]);d1=fnum(r["1h_MA30距离"])
    if s4 is None or d1 is None:return False
    return (s4>0.16) or (s4>-0.3 and d1>0.13)

def red_index(i,sig):
    bm=(c[i]-c[i-5])/atr[i] if sig>0 else (c[i-5]-c[i])/atr[i] if atr[i] else 0.0
    ac=atr_pct[i-20]/atr_pct[i-50]-1.0 if(i>=50 and atr_pct[i-50]>0) else 0.0
    return bm,ac

def build_pass(long_fn, bypass_long=False):
    def passes(r):
        i=base_sec.get(msec(r["时间"]))
        if i is None:return False
        if no_trade_v2(i):return False
        sig=int(r["信号"])
        if sig==1:
            lg=long_fn(r)
            if bypass_long and not lg:
                bm,ac=red_index(i,sig)
                if bm>0.5 and ac>0: lg=True
            return lg
        return short_gate(r)
    return passes

def metrics(passes):
    kept=[r for r in rows if passes(r)]
    n=len(kept);pnls=[pnl(r) for r in kept];tp=sum(1 for r in kept if r["止盈止损"]=="TP")
    gp=sum(p for p in pnls if p>0);gl=-sum(p for p in pnls if p<0)
    eq=1.0;pk=1.0;mdd=0.0
    for p in pnls:eq*=(1+p/100);pk=max(pk,eq);mdd=max(mdd,pk-eq)
    big=sum(1 for r in kept if is_big(r))
    return f"保留{n:3d} 胜率={tp/n*100:.1f}% 累计={sum(pnls):7.2f}% PF={gp/gl if gl else 0:4.2f} DD={mdd*100:5.1f}% 红字={big}({big/137*100:.0f}%)"

print("V2 基线(LongGate原版)        :", metrics(build_pass(long_gate_v2)))
print("A: LongGate放宽(d1>0.13即可) :", metrics(build_pass(lg_relaxA)))
print("B: LongGate放宽(s4>-0.3&d1)  :", metrics(build_pass(lg_relaxB)))
print("C: 原LongGate+多头启动旁路   :", metrics(build_pass(long_gate_v2, bypass_long=True)))
