# -*- coding: utf-8 -*-
"""新出场策略: 贴极值的1%百分比移动止损(阶梯trailing)
规则(以空单为例, 多单对称):
  入场价 E. 价格相对入场每跌满1%:
    跌1% -> 止损 = E-0.1%  (≈E, 即锁0%)
    跌2% -> 止损 = E-1%    (锁1%)
    ... 即 止损 = 极值 ∓ 1% (极值=最有利价, 1%缓冲)
  未达1%盈利前只有反向信号平仓; 达1%后止损激活并只上移; 跌破止损或反向信号出场。
自包含逐K线模拟。不改任何现有代码。
两套信号: 808(形态页原始ST信号) / 550(V3过滤后)。"""
import sys, os, csv, json, calendar, datetime
from collections import Counter
BASE = r"d:\个人项目代码\supertrendMain\backend"
sys.path.insert(0, BASE)
import signal_v3
from indicators import super_trend, ta_sma

OUT_JSON = os.path.join(BASE, "backtest", "btc_1h_full.json")
MASTER = os.path.join(BASE, "backtest", "st_signals_1h.csv")
FEAT = r"C:\Users\Administrator\AppData\Local\Temp\codebuddy-dropped-files\46a5ccbd-5091-496d-b717-8e8a681729ae\st_signals_1h_features.csv"
TH = 2.0
TRAIL = 0.01  # 1% 缓冲

d = json.load(open(OUT_JSON, encoding="utf-8")); base = d["base"]
o=[b["o"] for b in base]; h=[b["h"] for b in base]; l=[b["l"] for b in base]; c=[b["c"] for b in base]; n=len(c)
st_res = super_trend(o,h,l,c,periods=10,multiplier=3.0,change_atr=True)
flips=sorted((f["i"],f["type"]) for f in st_res["flips"]); flip_idxs=sorted(f["i"] for f in st_res["flips"]); typ={i:t for i,t in flips}
atr=st_res["atr"]; ma30=ta_sma(c,30)
atr_pct=[(atr[i]/c[i]*100 if(atr[i] and c[i]) else 0.0) for i in range(n)]
st_dist=[0.0]*n
for i in range(n):
    if st_res["trend"][i] is None or atr[i] in(None,0):continue
    line=st_res["up"][i] if st_res["trend"][i]==1 else st_res["dn"][i]; st_dist[i]=(c[i]-line)/atr[i]
cum=[0.0]*(n+1)
for i in range(1,n):cum[i]=cum[i-1]+abs(c[i]-c[i-1])
er20=[0.0]*n
for i in range(20,n):
    den=cum[i]-cum[i-20]; er20[i]=abs(c[i]-c[i-20])/den if den>0 else 0.0

# 映射 master 信号 -> 引擎翻转bar(offset -1)
rows=list(csv.DictReader(open(MASTER,encoding="utf-8-sig")))
def msec(s):return calendar.timegm(datetime.datetime.strptime(s,"%Y/%m/%d %H:%M").timetuple())
ts2i={int(b["ts"]/1000):i for i,b in enumerate(base)}
master_sig=[]
for r in rows:
    ts=msec(r["time"]); i=ts2i.get(ts)
    if i is None:continue
    fi=i-1
    if fi not in typ:continue
    master_sig.append((fi,int(r["signal"]),r))

# V3 过滤
frows=list(csv.DictReader(open(FEAT,encoding="utf-8-sig")))
def num(v):
    try:return float(v)
    except:return None
def build_feats(r,i,sd):
    b=signal_v3.base_features(c,atr,atr_pct,er20,st_dist,flip_idxs,ma30,i,sd)
    if b is None:return None
    b.update({"slope_htf":num(r.get("4h_MA30斜率")),"dist_base_ma":num(r.get("1h_MA30距离")),
        "dist_htf_ma":num(r.get("4h_MA30距离")),"adx_chg20":num(r.get("前20根_ADX变化")),
        "er_chg20":num(r.get("前20根_ER变化")),"vol100":num(r.get("前100根_波动周期"))});return b
fmap={msec(r["时间"]):r for r in frows}
v3set=set(); v3path={}
for fi,sd,r in master_sig:
    fr=fmap.get(msec(r["time"]))
    if fr is None:continue
    f=build_feats(fr,ts2i[msec(r["time"])],sd)
    if f is None:continue
    v=signal_v3.v3_decide(sd,f)
    if v["execute"]:
        v3set.add((fi,sd)); v3path[(fi,sd)]=v["path"]

def next_opp(i,sd):
    for j,t in flips:
        if j>i and ((t=="sell" and sd==1) or (t=="buy" and sd==-1)):return j
    return n-1

def step_trail_pnl(fi,sd):
    """贴极值1%百分比移动止损; 未达1%盈利前仅反向信号平仓。"""
    entry=c[fi]; j=next_opp(fi,sd)
    if sd==1:
        ext=entry; stop=None; exited=False
        for k in range(fi+1,j+1):
            ext=max(ext,h[k])
            if stop is None:
                if (ext-entry)/entry>=TRAIL: stop=ext*(1-TRAIL)
            else:
                ns=ext*(1-TRAIL)
                if ns>stop: stop=ns
            if stop is not None and l[k]<=stop:
                return (stop-entry)/entry*100, k  # 触发止损
        return (c[j]-entry)/entry*100, j  # 反向信号平仓
    else:
        ext=entry; stop=None; exited=False
        for k in range(fi+1,j+1):
            ext=min(ext,l[k])
            if stop is None:
                if (entry-ext)/entry>=TRAIL: stop=ext*(1+TRAIL)
            else:
                ns=ext*(1+TRAIL)
                if ns<stop: stop=ns
            if stop is not None and h[k]>=stop:
                return (entry-stop)/entry*100, k
        return (entry-c[j])/entry*100, j

def reverse_close_pnl(fi,sd):
    j=next_opp(fi,sd)
    return (c[j]-c[fi])/c[fi]*100*sd

def metrics(pairs,label):
    pairs=sorted(pairs,key=lambda x:x[0]); pn=[x[1] for x in pairs]; n_=len(pn)
    if n_==0:print(f"[{label}] 空集");return
    wins=[x for x in pn if x>0]; gl=-sum(x for x in pn if x<=0)
    eq=1.0;pk=1.0;mdd=0.0
    for x in pn:eq*=(1+x/100);pk=max(pk,eq);mdd=max(mdd,pk-eq)
    pf=sum(wins)/gl if gl else float("inf")
    red=sum(1 for x in pn if x>TH)
    print(f"[{label:30s}] 笔{n_:4d} 复利{eq-1:7.1%} 总利{sum(pn):7.1f}% 胜率{len(wins)/n_*100:4.1f}% PF={pf:4.2f} DD{mdd/pk*100:5.1f}% 红字{red:3d} 均{pn and sum(pn)/n_:5.2f}%")

print("===== 新策略: 贴极值1%百分比移动止损 =====")
# 808 原始
sig808=[(fi,sd) for fi,sd,_ in master_sig]
metrics([(fi,step_trail_pnl(fi,sd)[0]) for fi,sd in sig808],"808 原始 新trail")
metrics([(fi,reverse_close_pnl(fi,sd)) for fi,sd in sig808],"808 原始 反向平仓基线")
# 550 V3过滤
sig550=[(fi,sd) for fi,sd in v3set]
metrics([(fi,step_trail_pnl(fi,sd)[0]) for fi,sd in sig550],"550 V3过滤 新trail")
metrics([(fi,reverse_close_pnl(fi,sd)) for fi,sd in sig550],"550 V3过滤 反向平仓基线")
# 550 路径分布
print("\n550 V3路径分布(新trail):")
pathpn={}
for fi,sd in sig550:
    pathpn.setdefault(v3path[(fi,sd)],[]).append(step_trail_pnl(fi,sd)[0])
import functools
for p,pn in pathpn.items():
    wins=sum(1 for x in pn if x>0)
    comp=functools.reduce(lambda a,b:a*(1+b/100),pn,1)-1
    print(f"  {p:8s} 笔{len(pn):3d} 复利{comp:6.1%} 胜率{wins/len(pn)*100:4.1f}% 红字{sum(1 for x in pn if x>TH)}")
