# -*- coding: utf-8 -*-
"""V3 550: 不同百分比 trailing 缓冲宽度网格, 看哪个逼近真实模块 404.6%."""
import sys, csv, json, calendar, datetime
BASE = r"d:\个人项目代码\supertrendMain\backend"
sys.path.insert(0, BASE)
import signal_v3
from indicators import super_trend, ta_sma
FEAT = r"C:\Users\Administrator\AppData\Local\Temp\codebuddy-dropped-files\46a5ccbd-5091-496d-b717-8e8a681729ae\st_signals_1h_features.csv"
d = json.load(open(BASE + r"\backtest\btc_1h_full.json", encoding="utf-8")); base = d["base"]
c=[b["c"] for b in base]; o=[b["o"] for b in base]; h=[b["h"] for b in base]; l=[b["l"] for b in base]; n=len(c)
st = super_trend(o,h,l,c,periods=10,multiplier=3.0,change_atr=True)
flips=sorted(f["i"] for f in st["flips"]); atr=st["atr"]; ma30=ta_sma(c,30)
atr_pct=[(atr[i]/c[i]*100 if(atr[i] and c[i]) else 0.0) for i in range(n)]
st_dist=[0.0]*n
for i in range(n):
    if st["trend"][i] is None or atr[i] in (None,0): continue
    line=st["up"][i] if st["trend"][i]==1 else st["dn"][i]; st_dist[i]=(c[i]-line)/atr[i]
cum=[0.0]*(n+1)
for i in range(1,n): cum[i]=cum[i-1]+abs(c[i]-c[i-1])
er20=[0.0]*n
for i in range(20,n):
    den=cum[i]-cum[i-20]; er20[i]=abs(c[i]-c[i-20])/den if den>0 else 0.0
def msec(s): return calendar.timegm(datetime.datetime.strptime(s,"%Y/%m/%d %H:%M").timetuple())
ts2i={int(b["ts"]/1000):i for i,b in enumerate(base)}
fr_rows=list(csv.DictReader(open(FEAT,encoding="utf-8-sig")))
def num(v):
    try: return float(v)
    except: return None
def bf(r,i,sd):
    b=signal_v3.base_features(c,atr,atr_pct,er20,st_dist,flips,ma30,i,sd)
    if b is None: return None
    b.update({"slope_htf":num(r.get("4h_MA30斜率")),"dist_base_ma":num(r.get("1h_MA30距离")),
        "dist_htf_ma":num(r.get("4h_MA30距离")),"adx_chg20":num(r.get("前20根_ADX变化")),
        "er_chg20":num(r.get("前20根_ER变化")),"vol100":num(r.get("前100根_波动周期"))}); return b
sel=[]
for r in fr_rows:
    ts=msec(r["时间"]); i=ts2i.get(ts)
    if i is None: continue
    sd=int(r["信号"]); f=bf(r,i,sd)
    if f is None: continue
    if signal_v3.v3_decide(sd,f)["execute"]: sel.append((i-1,sd))
print("V3 550 集合:",len(sel))
def nxt(i,sd):
    for j in flips:
        if j>i and ((st["trend"][j]==-1 and sd==1) or (st["trend"][j]==1 and sd==-1)): return j
    return n-1
def trail_pnl(fi,sd,B):
    entry=c[fi]; j=nxt(fi,sd)
    if sd==1:
        peak=entry; stop=None
        for k in range(fi+1,j+1):
            peak=max(peak,h[k])
            if stop is None and (peak-entry)/entry>=B: stop=peak*(1-B)
            elif stop is not None:
                ns=peak*(1-B)
                if ns>stop: stop=ns
            if stop is not None and l[k]<=stop: return (stop-entry)/entry*100
        return (c[j]-entry)/entry*100
    else:
        peak=entry; stop=None
        for k in range(fi+1,j+1):
            peak=min(peak,l[k])
            if stop is None and (entry-peak)/entry>=B: stop=peak*(1+B)
            elif stop is not None:
                ns=peak*(1+B)
                if ns<stop: stop=ns
            if stop is not None and h[k]>=stop: return (entry-stop)/entry*100
        return (entry-c[j])/entry*100
def rev(fi,sd):
    j=nxt(fi,sd); return (c[j]-c[fi])/c[fi]*100*sd
def agg(pn,lab):
    eq=1.0
    for x in pn: eq*=(1+x/100)
    wins=sum(1 for x in pn if x>0)
    print("  %-12s 复利%7.1f%% 胜率%4.1f%% 红字%d" % (lab,(eq-1)*100,wins/len(pn)*100,sum(1 for x in pn if x>2)))
print("V3 550 不同百分比trailing缓冲(真实模块=404.6%):")
agg([rev(fi,sd) for fi,sd in sel],"反向平仓")
for pct in [1,2,3,5,8,10,15]:
    B=pct/100.0
    agg([trail_pnl(fi,sd,B) for fi,sd in sel],"trail %d%%"%pct)
