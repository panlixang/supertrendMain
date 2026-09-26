# -*- coding: utf-8 -*-
"""Signal Engine V3: Trend Gate + Early Breakout 旁路 + 两级 Range Fuse. 与方案C对比。"""
import sys, os, json, csv, statistics as st
BASE = r"d:\个人项目代码\supertrendMain\backend"
sys.path.insert(0, BASE)
from indicators import super_trend, ta_sma
OUT_JSON=os.path.join(BASE,"backtest","btc_1h_full.json")
SRC=r"C:\Users\Administrator\AppData\Local\Temp\codebuddy-dropped-files\46a5ccbd-5091-496d-b717-8e8a681729ae\st_signals_1h_features.csv"
TH=2.0
d=json.load(open(OUT_JSON,encoding="utf-8"));base=d["base"]
o=[b["o"] for b in base];h=[b["h"] for b in base];l=[b["l"] for b in base];c=[b["c"] for b in base];n=len(c)
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

# ---- Gates ----
def long_gate(r):
    s4=fnum(r["4h_MA30斜率"]);d1=fnum(r["1h_MA30距离"])
    if s4 is None or d1 is None:return False
    return (s4>0.16) or (s4>0 and d1>0.13)
def short_gate(r):
    vc=fnum(r["前100根_波动周期"]);adx=fnum(r["前20根_ADX变化"]);d4=fnum(r["4h_MA30距离"]);er=fnum(r["前20根_ER变化"])
    if None in(vc,adx,d4,er):return False
    if vc<9.5 and adx<3.25:return True
    if vc>9.5 and d4>-0.7 and er>-0.06:return True
    return False

# ---- Long Early Breakout V2 旁路 (4条件) ----
def long_bypass_v2(i,sig):
    bm=(c[i]-c[i-5])/atr[i] if sig>0 else (c[i-5]-c[i])/atr[i] if atr[i] else 0.0
    ac=atr_pct[i-20]/atr_pct[i-50]-1.0 if(i>=50 and atr_pct[i-50]>0) else 0.0
    st_chg=st_dist[i]-st_dist[i-20]            # 多头需>0
    cmd=abs(c[i]-ma30[i])/atr[i] if(ma30[i] and atr[i]) else 0.0
    return bm>0.5 and ac>0 and st_chg>0 and cmd<3

# ---- V3 结构 ----
def v3_pass(r):
    i=base_sec.get(msec(r["时间"]))
    if i is None:return False
    sig=int(r["信号"])
    score=0
    if sig==1:
        if long_gate(r): score=100          # 路径1: 成熟趋势
        elif long_bypass_v2(i,sig): score=80 # 路径2: 早期启动
    else:
        if short_gate(r): score=100
    # 一级保护(降权, 非禁止)
    if flip50(i)>6: score-=20
    # 二级保护(硬禁)
    if flip50(i)>8 and er20_at(i)<0.15: score=-1000
    return score>0

# ---- 方案C (旧旁路+旧no_trade) 对照 ----
def no_trade_v2(i):
    if i<20:return False
    return (er20_at(i)<0.22) and (abs(c[i]-ma30[i])/atr[i] if(ma30[i] and atr[i]) else 0)<0.5 and abs(st_dist[i]-st_dist[i-20])<0.2
def c_pass(r):
    i=base_sec.get(msec(r["时间"]))
    if i is None:return False
    if no_trade_v2(i):return False
    sig=int(r["信号"])
    if sig==1:
        lg=long_gate(r)
        if not lg:
            bm=(c[i]-c[i-5])/atr[i] if atr[i] else 0.0
            ac=atr_pct[i-20]/atr_pct[i-50]-1.0 if(i>=50 and atr_pct[i-50]>0) else 0.0
            lg = bm>0.5 and ac>0
        return lg
    return short_gate(r)

def metrics(passes,label):
    kept=[r for r in rows if passes(r)]
    n=len(kept);pnls=[pnl(r) for r in kept];tp=sum(1 for r in kept if r["止盈止损"]=="TP")
    gp=sum(p for p in pnls if p>0);gl=-sum(p for p in pnls if p<0)
    eq=1.0;pk=1.0;mdd=0.0
    for p in pnls:eq*=(1+p/100);pk=max(pk,eq);mdd=max(mdd,pk-eq)
    big=sum(1 for r in kept if is_big(r))
    cum=eq-1; ddp=mdd/pk*100
    print(f"[{label}] 保留{n:3d} 胜率={tp/n*100:.1f}% 累计(复利)={cum*100:7.2f}% 算术和={sum(pnls):7.2f}% PF={gp/gl if gl else 0:4.2f} DD(标准)={ddp:5.1f}% 红字={big}({big/137*100:.0f}%)")

metrics(c_pass,"方案C(旧旁路+旧Fuse)")
metrics(v3_pass,"V3(Long V2旁路+两级Fuse)")

# Fuse 触发统计
hard=[r for r in rows if (lambda i: i is not None and flip50(i)>8 and er20_at(i)<0.15)(base_sec.get(msec(r["时间"])))]
soft=[r for r in rows if (lambda i: i is not None and flip50(i)>6)(base_sec.get(msec(r["时间"])))]
print(f"\nFuse 一级(降权 flip50>6): {len(soft)} 笔")
print(f"Fuse 二级(硬禁 flip50>8 & ER20<0.15): {len(hard)} 笔 (红字 {sum(1 for r in hard if is_big(r))}, SL {sum(1 for r in hard if r['止盈止损']=='SL')})")
