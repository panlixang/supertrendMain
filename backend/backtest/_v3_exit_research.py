# -*- coding: utf-8 -*-
"""V3 持仓侧研究(修正版)：用 master 真实出场价为基准，仅对 overlay 更早平仓的反事实用前向K线重算。

方法：每笔 trade 已知 entry(close[i]) 与 master 实际出场价(由真实盈亏反推)及 exit_result(TP/SL)。
overlay 只可能比 master 更早平仓(不会延长持有)，故逐根前向扫描：
  - 先定位 master 实际平仓bar(TP单看有利触达/SL单看不利触达)；
  - 在 [i+1, master_bar] 内若 overlay 触发，则以 overlay 价提前平仓，否则沿用 master。
这样不依赖复现 master 复杂出场模型，overlay 的边际效果干净可比。
"""
import sys, os, csv, json, bisect, calendar, datetime
from collections import Counter
BASE = r"d:\个人项目代码\supertrendMain\backend"
sys.path.insert(0, BASE)
from indicators import super_trend, ta_sma

OUT_JSON = os.path.join(BASE, "backtest", "btc_1h_full.json")
FEAT = r"C:\Users\Administrator\AppData\Local\Temp\codebuddy-dropped-files\46a5ccbd-5091-496d-b717-8e8a681729ae\st_signals_1h_features.csv"
TH = 2.0

d = json.load(open(OUT_JSON, encoding="utf-8")); base = d["base"]
o=[b["o"] for b in base]; h=[b["h"] for b in base]; l=[b["l"] for b in base]; c=[b["c"] for b in base]; n=len(c)
st_res = super_trend(o,h,l,c,periods=10,multiplier=3.0,change_atr=True)
atr=st_res["atr"]; trend=st_res["trend"]; up=st_res["up"]; dn=st_res["dn"]
flips=sorted((f["i"],f["type"]) for f in st_res["flips"])
ma30=ta_sma(c,30)
atr_pct=[(atr[i]/c[i]*100 if(atr[i] and c[i]) else 0.0) for i in range(n)]
st_dist=[0.0]*n
for i in range(n):
    if trend[i] is None or atr[i] in(None,0):continue
    line=up[i] if trend[i]==1 else dn[i]; st_dist[i]=(c[i]-line)/atr[i]

rows=list(csv.DictReader(open(FEAT,encoding="utf-8-sig")))
def msec(s):return calendar.timegm(datetime.datetime.strptime(s,"%Y/%m/%d %H:%M").timetuple())
base_sec={int(b["ts"]/1000):i for i,b in enumerate(base)}
def fnum(x):
    try:return float(x)
    except:return None
def pnl(r):return fnum(r["盈亏"]) or 0.0
def side(r):return int(r["信号"])

# V3 决策(含第三多头路径)
def v3_pass(r,i,add_third=False,third_slope=-0.3,third_mom=1.0):
    s=side(r)
    if s==1:
        s4=fnum(r["4h_MA30斜率"]);d1=fnum(r["1h_MA30距离"])
        if (s4 is not None and d1 is not None) and ((s4>0.16) or (s4>0 and d1>0.13)):
            return True,"成熟趋势"
        bm=(c[i]-c[i-5])/atr[i] if atr[i] else 0.0
        ac=atr_pct[i-20]/atr_pct[i-50]-1.0 if(i>=50 and atr_pct[i-50]>0) else 0.0
        stc=st_dist[i]-st_dist[i-20] if i>=20 else 0.0
        cmd=abs(c[i]-ma30[i])/atr[i] if(ma30[i] and atr[i]) else 0.0
        if bm>0.5 and ac>0 and stc>0 and cmd<3:
            return True,"早期启动"
        if add_third and s4 is not None and s4>third_slope and bm>third_mom:
            return True,"慢热接住"
        return False,"未通过"
    else:
        vc=fnum(r["前100根_波动周期"]);adx=fnum(r["前20根_ADX变化"]);d4=fnum(r["4h_MA30距离"]);er=fnum(r["前20根_ER变化"])
        if None in(vc,adx,d4,er):return False,"未通过"
        if vc<9.5 and adx<3.25:return True,"成熟趋势"
        if vc>9.5 and d4>-0.7 and er>-0.06:return True,"成熟趋势"
        return False,"未通过"

def metrics(sel,label):
    if not sel:print(f"[{label}] 空集");return
    pn=[x[0] for x in sel];n_=len(pn)
    wins=[x for x in pn if x>0];losses=[x for x in pn if x<=0]
    gp=sum(wins);gl=-sum(losses)
    eq=1.0;pk=1.0;mdd=0.0
    for x in pn:eq*=(1+x/100);pk=max(pk,eq);mdd=max(mdd,pk-eq)
    pf=gp/gl if gl else float("inf")
    red=sum(1 for x in pn if x>TH)
    print(f"[{label:20s}] 笔{n_:4d} 复利{eq-1:7.1%} 胜率{len(wins)/n_*100:4.1f}% PF={pf:4.2f} DD{mdd/pk*100:5.1f}% 红字{red:3d} 均盈{sum(pn)/n_:+.2f}%")

# master 实际平仓价 + 平仓bar
def master_exit(i,s,p):
    entry=c[i];long=(s==1)
    m_exit=entry*(1+p/100) if long else entry*(1-p/100)
    is_tp=(p>0)
    for k in range(i+1,min(i+400,n)):
        if long:
            if is_tp and h[k]>=m_exit: return k,m_exit,"TP"
            if (not is_tp) and l[k]<=m_exit: return k,m_exit,"SL"
        else:
            if is_tp and l[k]<=m_exit: return k,m_exit,"TP"
            if (not is_tp) and h[k]>=m_exit: return k,m_exit,"SL"
    return min(i+400,n-1),c[min(i+400,n-1)],"FLIP"

# overlay 反事实：在 [i+1,mbar] 内若触发更早平仓则采用
def overlay_pnl(i,s,entry,mbar,m_exit,is_tp,ov,K,T,Nb):
    long=(s==1);a0=atr[i] if atr[i] else 0.0
    init_stop=entry*(1-K*a0/entry) if long else entry*(1+K*a0/entry)
    fave=entry;confirmed=False
    for k in range(i+1,mbar+1):
        hi,lo=h[k],l[k]
        if long:
            fave=max(fave,hi)
            if ov=="trail" and not confirmed and (fave-entry)>=T*a0:confirmed=True
            if ov=="trail" and confirmed:
                ts=fave-T*a0
                if lo<=ts:return (ts-entry)/entry*100
            if ov=="confirm" and k<=i+Nb and lo<=init_stop:
                return (init_stop-entry)/entry*100
        else:
            fave=min(fave,lo)
            if ov=="trail" and not confirmed and (entry-fave)>=T*a0:confirmed=True
            if ov=="trail" and confirmed:
                ts=fave+T*a0
                if hi>=ts:return (entry-ts)/entry*100
            if ov=="confirm" and k<=i+Nb and hi>=init_stop:
                return (entry-init_stop)/entry*100
    return (m_exit-entry)/entry*100  # 未触发，沿用master

# ── 基准(master真实) ──
v3_rows=[(pnl(r),r,i) for r in rows for i in [base_sec.get(msec(r["时间"]))] if i is not None and v3_pass(r,i)[0]]
print("=== 基准 (master 真实盈亏) ===")
metrics(v3_rows,"V3 498(master)")
full_rows=[(pnl(r),r,i) for r in rows for i in [base_sec.get(msec(r["时间"]))] if i is not None]
metrics(full_rows,"全量808(master)")

# ── 假突破识别(早期启动 + master SL) ──
fake=[(pnl(r),r,i) for (pnl_,r,i) in v3_rows if v3_pass(r,i)[1]=="早期启动" and pnl_ < 0]
print(f"\n早期启动且master SL(假突破候选): {len(fake)} 笔, master累计 {sum(p for p,_,_ in fake):.1f}%")

# ── overlay 扫描 (V3 498) ──
print("\n=== 持仓侧 overlay (V3 498, 反事实重算) ===")
for ov,K,T,Nb in [("confirm",1.0,1.0,3),("confirm",1.0,1.0,2),
                  ("trail",1.0,1.0,3),("trail",1.5,1.0,3),("trail",1.0,1.5,3),("trail",2.0,2.0,3)]:
    out=[]
    for (_,r,i) in v3_rows:
        s=side(r);p=pnl(r);entry=c[i]
        mbar,mex,_=master_exit(i,s,p)
        cp=overlay_pnl(i,s,entry,mbar,mex,(p>0),ov,K,T,Nb)
        out.append((cp,r,i))
    metrics(out,f"{ov} K={K} T={T} N={Nb}")
    fo=[]
    for (_,r,i) in fake:
        s=side(r);p=pnl(r);entry=c[i]
        mbar,mex,_=master_exit(i,s,p)
        cp=overlay_pnl(i,s,entry,mbar,mex,(p>0),ov,K,T,Nb)
        fo.append((cp,r,i))
    print(f"    └ 假突破子集: master {sum(p for p,_,_ in fake):.1f}% → overlay {sum(p for p,_,_ in fo):.1f}%  原最差{min(p for p,_,_ in fake):.1f}% 新最差{min(p for p,_,_ in fo):.1f}%")

# ── 第三多头路径 (直接用master真实盈亏) ──
print("\n=== 第三多头路径 (慢热接住, master真实盈亏) ===")
for sl,mom in [(-0.3,1.0),(-0.5,0.8),(-0.2,1.2),(-0.6,0.6)]:
    sel=[]
    new_rec=0;new_loss=0;rew=0
    for r in rows:
        i=base_sec.get(msec(r["时间"]))
        if i is None:continue
        ok,path=v3_pass(r,i,add_third=True,third_slope=sl,third_mom=mom)
        if not ok:continue
        sel.append((pnl(r),r,i))
        if path=="慢热接住":
            if pnl(r)<0:new_loss+=1
            else:new_rec+=1
            if pnl(r)>TH:rew+=1
    metrics(sel,f"第三 sl={sl} mom={mom}")
    # 这些慢热接住里, 多少是"原被V3漏掉的大赢家"
    missed_before=[r for r in rows if (i:=base_sec.get(msec(r["时间"]))) is not None and (not v3_pass(r,i)[0]) and v3_pass(r,i,add_third=True,third_slope=sl,third_mom=mom)[1]=="慢热接住"]
    print(f"    └ 慢热接住 {new_rec+new_loss}笔(亏{new_loss}); 其中恢复大赢家(>2%)={rew}; 原被漏的大赢家中挽回={sum(1 for r in missed_before if pnl(r)>TH)}/{len(missed_before)}")
