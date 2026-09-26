# -*- coding: utf-8 -*-
"""550笔(新V3含路径3) 三实验：
实验1(最高价值): 全部550加「入场后3根K确认」(无效线=entry±K*ATR, N=3);
   比较 收益/DD/删除亏损数/删除红字数。
实验2: 434非红字单(550-116) 失败生命周期: 入场后1H 最大浮盈/最大浮亏/反向时间。
实验3: Slow Start(路径3)阈值网格优化。
"""
import sys, os, csv, json, bisect, calendar, datetime, statistics as st
BASE = r"d:\个人项目代码\supertrendMain\backend"
sys.path.insert(0, BASE)
import signal_v3
from indicators import super_trend, ta_sma

OUT_JSON = os.path.join(BASE, "backtest", "btc_1h_full.json")
FEAT = r"C:\Users\Administrator\AppData\Local\Temp\codebuddy-dropped-files\46a5ccbd-5091-496d-b717-8e8a681729ae\st_signals_1h_features.csv"
TH = 2.0

d = json.load(open(OUT_JSON, encoding="utf-8")); base = d["base"]
o=[b["o"] for b in base]; h=[b["h"] for b in base]; l=[b["l"] for b in base]; c=[b["c"] for b in base]; n=len(c)
st_res = super_trend(o,h,l,c,periods=10,multiplier=3.0,change_atr=True)
atr=st_res["atr"]; trend=st_res["trend"]; up=st_res["up"]; dn=st_res["dn"]
flips=sorted(f["i"] for f in st_res["flips"])
ma30=ta_sma(c,30)
atr_pct=[(atr[i]/c[i]*100 if(atr[i] and c[i]) else 0.0) for i in range(n)]
st_dist=[0.0]*n
for i in range(n):
    if trend[i] is None or atr[i] in(None,0):continue
    line=up[i] if trend[i]==1 else dn[i]; st_dist[i]=(c[i]-line)/atr[i]
cum=[0.0]*(n+1)
for i in range(1,n):cum[i]=cum[i-1]+abs(c[i]-c[i-1])
er20=[0.0]*n
for i in range(20,n):
    den=cum[i]-cum[i-20];er20[i]=abs(c[i]-c[i-20])/den if den>0 else 0.0

rows=list(csv.DictReader(open(FEAT,encoding="utf-8-sig")))
def msec(s):return calendar.timegm(datetime.datetime.strptime(s,"%Y/%m/%d %H:%M").timetuple())
base_sec={int(b["ts"]/1000):i for i,b in enumerate(base)}
def _num(v):
    if v is None or v=="":return None
    try:return float(v)
    except:return None
def pnl(r):return _num(r["盈亏"]) or 0.0
def side(r):return int(r["信号"])

def build_feats(r,i,sd):
    bf=signal_v3.base_features(c,atr,atr_pct,er20,st_dist,flips,ma30,i,sd)
    if bf is None:return None
    feats=dict(bf)
    feats.update({
        "slope_htf":_num(r.get("4h_MA30斜率")),
        "dist_base_ma":_num(r.get("1h_MA30距离")),
        "dist_htf_ma":_num(r.get("4h_MA30距离")),
        "adx_chg20":_num(r.get("前20根_ADX变化")),
        "er_chg20":_num(r.get("前20根_ER变化")),
        "vol100":_num(r.get("前100根_波动周期")),
    })
    return feats

# 选中550集合(新V3含路径3)
sel=[]
for idx,r in enumerate(rows):
    i=base_sec.get(msec(r["时间"]))
    if i is None:continue
    sd=side(r)
    f=build_feats(r,i,sd)
    if f is None:continue
    v=signal_v3.v3_decide(sd,f)
    if v["execute"]:
        sel.append((idx,r,i,sd,v["path"]))
print(f"550集合: 选入 {len(sel)} 笔")
from collections import Counter
print("  路径分布:", dict(Counter(p for _,_,_,_,p in sel)))

def metrics(sel,label):
    pn=[x[0] for x in sel]
    n_=len(pn);wins=[x for x in pn if x>0];losses=[x for x in pn if x<=0]
    gp=sum(wins);gl=-sum(losses)
    eq=1.0;pk=1.0;mdd=0.0
    for x in pn:eq*=(1+x/100);pk=max(pk,eq);mdd=max(mdd,pk-eq)
    pf=gp/gl if gl else float("inf")
    red=sum(1 for x in pn if x>TH)
    print(f"[{label:22s}] 笔{n_:4d} 复利{eq-1:7.1%} 胜率{len(wins)/n_*100:4.1f}% PF={pf:4.2f} DD{mdd/pk*100:5.1f}% 红字{red:3d} 均盈{sum(pn)/n_:+.2f}%")

# ── master 实际平仓bar/价 ──
def master_exit(i,s,p):
    entry=c[i];long=(s==1);m_exit=entry*(1+p/100) if long else entry*(1-p/100);is_tp=(p>0)
    for k in range(i+1,min(i+400,n)):
        if long:
            if is_tp and h[k]>=m_exit:return k
            if (not is_tp) and l[k]<=m_exit:return k
        else:
            if is_tp and l[k]<=m_exit:return k
            if (not is_tp) and h[k]>=m_exit:return k
    return min(i+400,n-1)

# ════════════ 实验1: 入场后3根K确认 ════════════
print("\n===== 实验1: 入场后3根K确认 (无效线 entry±K*ATR, N=3) =====")
base_metrics=[pnl(r) for _,r,_,_,_ in sel]
metrics([(pnl(r),r,i,sd) for _,r,i,sd,_ in sel],"基准550(无确认)")
for K in [0.5,1.0,1.5,2.0]:
    kept=[];rem_loss=0;rem_red=0;rem_total=0;rem_orig=0.0;rem_new=0.0
    for _,r,i,sd,_ in sel:
        entry=c[i];p=pnl(r);long=(sd==1);a0=atr[i] if atr[i] else 0.0
        inv=entry*(1-K*a0/entry) if long else entry*(1+K*a0/entry)
        mb=master_exit(i,sd,p)
        trig=False;cp=p
        for k in range(i+1,mb+1):
            if k<=i+3:
                if long and l[k]<=inv:cp=(inv-entry)/entry*100;trig=True;break
                if (not long) and h[k]>=inv:cp=(entry-inv)/entry*100;trig=True;break
        if trig:
            rem_total+=1
            if p<0:rem_loss+=1
            if p>TH:rem_red+=1
            rem_orig+=p;rem_new+=cp
            kept.append((cp,r,i,sd))   # 被确认出局 → 以确认价计入
        else:
            kept.append((p,r,i,sd))
    metrics(kept,f"确认 K={K} N=3")
    print(f"    └ 删除(确认出局) {rem_total}笔: 原亏损 {rem_loss}笔, 原红字 {rem_red}笔; "
          f"原累计 {rem_orig:+.1f}% → 确认后 {rem_new:+.1f}% (净 {rem_new-rem_orig:+.1f}%)")

# ════════════ 实验2: 434失败单 生命周期 ════════════
print("\n===== 实验2: 434非红字单(550-116) 失败生命周期 =====")
fail=[(r,i,sd) for _,r,i,sd,_ in sel if pnl(r)<=TH]
print(f"非红字单: {len(fail)} 笔")
mfe=[];mae=[];rev=[]
for r,i,sd in fail:
    entry=c[i];long=(sd==1);a0=atr[i] if atr[i] else 0.0
    p=pnl(r);mb=master_exit(i,sd,p)
    mf=0.0;ma=0.0;rv=None
    for k in range(i+1,mb+1):
        if long:
            mf=max(mf,(h[k]-entry)/entry*100);ad=(entry-l[k])/entry*100
            ma=max(ma,ad)
            if rv is None and ad>=0.5*a0/entry*100:rv=k-i
        else:
            mf=max(mf,(entry-l[k])/entry*100);ad=(h[k]-entry)/entry*100
            ma=max(ma,ad)
            if rv is None and ad>=0.5*a0/entry*100:rv=k-i
    if rv is None:rv=mb-i
    mfe.append(mf);mae.append(ma);rev.append(rv)
def stat(name,a):
    a=sorted(a)
    print(f"  {name}: 中位{a[len(a)//2]:.2f} 均值{sum(a)/len(a):.2f} p25{a[len(a)//4]:.2f} p75{a[3*len(a)//4]:.2f} 最大{a[-1]:.2f}")
stat("最大浮盈MFE(%)",mfe);stat("最大浮亏MAE(%)",mae);stat("反向时间(首破入场0.5ATR的K数)",rev)
# 生命周期: 失败单里有多少其实先到过+1%以上(可被trailing接住), 多少MAE>2%
print(f"  失败单中 曾浮盈>1%: {sum(1 for x in mfe if x>1)}笔  曾浮盈>2%: {sum(1 for x in mfe if x>2)}笔")
print(f"  失败单中 最大浮亏>2%: {sum(1 for x in mae if x>2)}笔  >3%: {sum(1 for x in mae if x>3)}笔")
print(f"  失败单中 反向时间<=3根(早死): {sum(1 for x in rev if x<=3)}笔  >10根(拖很久才死): {sum(1 for x in rev if x>10)}笔")

# ════════════ 实验3: Slow Start 阈值网格 ════════════
print("\n===== 实验3: Slow Start(路径3)阈值网格优化 =====")
orig_slope=signal_v3.SLOW_SLOPE_MIN;orig_mom=signal_v3.SLOW_MOM_MIN
res=[]
for smin in [-0.8,-0.6,-0.4,-0.2]:
    for mmin in [0.4,0.6,0.8,1.0,1.2]:
        signal_v3.SLOW_SLOPE_MIN=smin;signal_v3.SLOW_MOM_MIN=mmin
        cur=[]
        for idx,r in enumerate(rows):
            i=base_sec.get(msec(r["时间"]))
            if i is None:continue
            sd=side(r);f=build_feats(r,i,sd)
            if f is None:continue
            v=signal_v3.v3_decide(sd,f)
            if v["execute"]:cur.append((pnl(r),r,i))
        pn=[x[0] for x in cur];n_=len(pn);wins=[x for x in pn if x>0]
        gp=sum(wins);gl=-sum(x for x in pn if x<=0)
        eq=1.0
        for x in pn:eq*=(1+x/100)
        pf=gp/gl if gl else 0
        red=sum(1 for x in pn if x>TH)
        res.append((smin,mmin,n_,eq-1,pf,red))
signal_v3.SLOW_SLOPE_MIN=orig_slope;signal_v3.SLOW_MOM_MIN=orig_mom
res.sort(key=lambda x:-x[3])
print("  (slope_min, mom_min, 笔, 复利, PF, 红字) 按复利排序:")
for x in res:print(f"    {x[0]:+.1f} {x[1]:.1f}  n={x[2]:4d}  复利{x[3]:7.1%}  PF={x[4]:4.2f}  红字{x[5]:3d}")
best=res[0]
print(f"\n  最优(复利): slope_min={best[0]}, mom_min={best[1]} → 复利{best[3]:.1%} PF{best[4]:.2f} 红字{best[5]} (当前-0.6/0.6 → 550笔/404.6%/1.37/116)")
