# -*- coding: utf-8 -*-
"""Lost Red Analysis: 55个被V2删掉的红字 vs 82个保留红字, 找可挽回规律。"""
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
atr=st_res["atr"]; trend=st_res["trend"]; up=st_res["up"]; dn=st_res["dn"]; flips=sorted(f["i"] for f in st_res["flips"])
ma30=ta_sma(c,30)
atr_pct=[(atr[i]/c[i]*100 if (atr[i] and c[i]) else 0.0) for i in range(n)]
st_dist=[0.0]*n
for i in range(n):
    if trend[i] is None or atr[i] in (None,0): st_dist[i]=0.0
    else: line=up[i] if trend[i]==1 else dn[i]; st_dist[i]=(c[i]-line)/atr[i]
def er20_at(i):
    if i<20: return 0.0
    num=abs(c[i]-c[i-20]); den=sum(abs(c[k]-c[k-1]) for k in range(i-20,i))
    return num/den if den>0 else 0.0
def flip50(i): return sum(1 for f in flips if i-50<f<=i)

rows=list(csv.DictReader(open(SRC,encoding="utf-8-sig")))
import calendar, datetime
def msec(s): return calendar.timegm(datetime.datetime.strptime(s,"%Y/%m/%d %H:%M").timetuple())
base_sec={int(b["ts"]/1000):i for i,b in enumerate(base)}
def fnum(x):
    try: return float(x)
    except: return None
def pnl(r): return fnum(r["盈亏"]) or 0
def is_big(r): return pnl(r)>TH

# V2 gates
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
def no_trade_v2(i):
    if i<20: return False
    return (er20_at(i)<0.22) and (abs(c[i]-ma30[i])/atr[i] if (ma30[i] and atr[i]) else 0)<0.5 and abs(st_dist[i]-st_dist[i-20])<0.2
def passes(r):
    i=base_sec.get(msec(r["时间"]))
    if i is None: return False
    if no_trade_v2(i): return False
    sig=int(r["信号"])
    return long_gate(r) if sig==1 else short_gate(r)

# 逐信号构造特征
def feats(r):
    i=base_sec.get(msec(r["时间"]))
    sig=int(r["信号"])
    bm = (c[i]-c[i-5])/atr[i] if sig>0 else (c[i-5]-c[i])/atr[i] if atr[i] else 0.0
    ac = atr_pct[i-20]/atr_pct[i-50]-1.0 if (i>=50 and atr_pct[i-50]>0) else 0.0
    er=er20_at(i); atrp=atr_pct[i]; fl=flip50(i)
    return dict(
        sig=sig,
        s4=fnum(r["4h_MA30斜率"]), s1=fnum(r["1h_MA30距离"]),
        er_chg=fnum(r["前20根_ER变化"]), adx_chg=fnum(r["前20根_ADX变化"]),
        atr_chg=fnum(r["前20根_ATR变化"]), st_chg=fnum(r["前20根_ST距离变化"]),
        bm=bm, ac=ac, er20=er, atrp=atrp, fl=fl,
        kept=passes(r), pnl=pnl(r))

recs=[feats(r) for r in rows if base_sec.get(msec(r["时间"])) is not None and base_sec.get(msec(r["时间"]))>=50]
reds=[x for x in recs if x["pnl"]>TH]
kept_red=[x for x in reds if x["kept"]]
lost_red=[x for x in reds if not x["kept"]]
print(f"红字总数 {len(reds)} | 保留 {len(kept_red)} | 删除 {len(lost_red)}")

cols=[("sig(方向1多/-1空)","sig"),("4h_MA30斜率","s4"),("1h_MA30距离","s1"),
      ("ER变化","er_chg"),("ADX变化","adx_chg"),("ATR变化","atr_chg"),
      ("ST距离变化","st_chg"),("break_mom5","bm"),("atr_contract50","ac"),
      ("ER20","er20"),("ATR%","atrp"),("翻转50","fl")]
def m(g,k):
    v=[x[k] for x in g]; return st.mean(v) if v else 0
print(f"\n{'特征':14s} {'保留红字':>10s} {'删除红字':>10s} {'差':>10s}")
for name,k in cols:
    print(f"{name:14s} {m(kept_red,k):10.4f} {m(lost_red,k):10.4f} {m(kept_red,k)-m(lost_red,k):10.4f}")

# 方向比例
from collections import Counter
print("\n方向比例  保留红字:",Counter(x['sig'] for x in kept_red)," 删除红字:",Counter(x['sig'] for x in lost_red))

# 用户假设: 删除红字里 空头 + break_mom5>0.5 + ATR扩张(atr_contract50>0 或 atr_chg>0)
def hyp(x): return (x['sig']==-1 and x['bm']>0.5 and (x['ac']>0 or (x['atr_chg'] or 0)>0))
print(f"\n删除红字中 满足[空头 & break_mom5>0.5 & (atr_contract>0或ATR变化>0)]: "
      f"{sum(1 for x in lost_red if hyp(x))}/{len(lost_red)}")

# ---- 实验: 趋势启动旁路 ----
def passes_bypass(r):
    i=base_sec.get(msec(r["时间"]))
    if i is None: return False
    if no_trade_v2(i): return False
    sig=int(r["信号"])
    bm=(c[i]-c[i-5])/atr[i] if sig>0 else (c[i-5]-c[i])/atr[i] if atr[i] else 0.0
    ac=atr_pct[i-20]/atr_pct[i-50]-1.0 if (i>=50 and atr_pct[i-50]>0) else 0.0
    bypass = bm>0.5 and ac>0
    return long_gate(r) if sig==1 else short_gate(r) or bypass

all_t=rows
kept_b=[r for r in rows if passes_bypass(r)]
def metrics(trades):
    n=len(trades); pnls=[pnl(r) for r in trades]; tp=sum(1 for r in trades if r["止盈止损"]=="TP")
    gp=sum(p for p in pnls if p>0); gl=-sum(p for p in pnls if p<0)
    eq=1.0;pk=1.0;mdd=0.0
    for p in pnls: eq*=(1+p/100);pk=max(pk,eq);mdd=max(mdd,pk-eq)
    return n,tp,sum(pnls),gp/gl if gl>0 else 0,mdd*100,sum(1 for r in trades if is_big(r))
n,tp,cum,pf,dd,big=metrics(kept_b)
print(f"\n[Early-Breakout旁路] 保留{n} 累计={cum:.2f}% PF={pf:.2f} DD={dd:.1f}% 红字={big}(覆盖{big/len(reds)*100:.0f}%)")

# ---- 极端震荡保险丝 提案 ----
def fuse(r):
    i=base_sec.get(msec(r["时间"]))
    if i is None or i<50: return False
    return flip50(i)>6 and er20_at(i)<0.15 and atr_pct[i]<0.8
fus=[r for r in rows if fuse(r)]
print(f"\n[极端震荡保险丝 提案] 命中={len(fus)} 笔 (红字 {sum(1 for r in fus if is_big(r))}, SL {sum(1 for r in fus if r['止盈止损']=='SL')})")
