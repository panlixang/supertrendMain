# -*- coding: utf-8 -*-
"""真引擎版对账(修正偏移):
master st_signals_1h.csv 的808信号 = 引擎 ST(10,3.0,change_atr=True) 翻转, 但信号时间戳记在翻转的
【下一根】(offset=-1)。故用 master 的550(V3放行) 精确映射到引擎翻转bar, 接 run_backtest 出场。
不改任何现有代码。"""
import sys, os, csv, json, calendar, datetime
BASE = r"d:\个人项目代码\supertrendMain\backend"
sys.path.insert(0, BASE)
import signal_v3
from indicators import super_trend, ta_sma
from position import ExitRules
from position_enhanced import EnhancedExitRules
import backtest_engine as BE

TH = 2.0
OUT_JSON = os.path.join(BASE, "backtest", "btc_1h_full.json")
FEAT = r"C:\Users\Administrator\AppData\Local\Temp\codebuddy-dropped-files\46a5ccbd-5091-496d-b717-8e8a681729ae\st_signals_1h_features.csv"
d = json.load(open(OUT_JSON, encoding="utf-8")); base = d["base"]
o=[b["o"] for b in base]; h=[b["h"] for b in base]; l=[b["l"] for b in base]; c=[b["c"] for b in base]; n=len(c)
st_res = super_trend(o,h,l,c,periods=10,multiplier=3.0,change_atr=True)
flips=sorted((f["i"],f["type"]) for f in st_res["flips"])      # 引擎原生翻转
flip_idxs=sorted(f["i"] for f in st_res["flips"])
flip_idx_type={i:typ for i,typ in flips}
atr=st_res["atr"]
ma30=ta_sma(c,30)
atr_pct=[(atr[i]/c[i]*100 if(atr[i] and c[i]) else 0.0) for i in range(n)]
st_dist=[0.0]*n
for i in range(n):
    if st_res["trend"][i] is None or atr[i] in(None,0):continue
    line=st_res["up"][i] if st_res["trend"][i]==1 else st_res["dn"][i]; st_dist[i]=(c[i]-line)/atr[i]
cum=[0.0]*(n+1)
for i in range(1,n):cum[i]=cum[i-1]+abs(c[i]-c[i-1])
er20=[0.0]*n
for i in range(20,n):
    den=cum[i]-cum[i-20];er20[i]=abs(c[i]-c[i-20])/den if den>0 else 0.0

rows=list(csv.DictReader(open(FEAT,encoding="utf-8-sig")))
def msec(s):return calendar.timegm(datetime.datetime.strptime(s,"%Y/%m/%d %H:%M").timetuple())
ts2i={int(b["ts"]/1000):i for i,b in enumerate(base)}
def _num(v):
    try:return float(v)
    except:return None
def pnl(r):return _num(r["盈亏"]) or 0.0
def side(r):return int(r["信号"])
def build_feats(r,i,sd):
    bf=signal_v3.base_features(c,atr,atr_pct,er20,st_dist,flip_idxs,ma30,i,sd)
    if bf is None:return None
    feats=dict(bf)
    feats.update({"slope_htf":_num(r.get("4h_MA30斜率")),
        "dist_base_ma":_num(r.get("1h_MA30距离")),
        "dist_htf_ma":_num(r.get("4h_MA30距离")),
        "adx_chg20":_num(r.get("前20根_ADX变化")),
        "er_chg20":_num(r.get("前20根_ER变化")),
        "vol100":_num(r.get("前100根_波动周期"))})
    return feats

# 选550: CSV-V3放行(与之前所有"550"数字同一口径), 映射到引擎翻转bar(i-1)
sel=[]   # (flip_idx, flip_ts, sd, r)
miss_dir=0; not_flip=0
for r in rows:
    ts=msec(r["时间"]); i=ts2i.get(ts)
    if i is None:continue
    sd=side(r)
    f=build_feats(r,i,sd)
    if f is None:continue
    v=signal_v3.v3_decide(sd,f)
    if not v["execute"]:continue
    fi=i-1                       # 引擎翻转bar = 信号bar - 1
    if fi not in flip_idx_type:not_flip+=1;continue
    exp_typ="buy" if sd==1 else "sell"
    if flip_idx_type[fi]!=exp_typ:miss_dir+=1;continue
    sel.append((fi,base[fi]["ts"],sd,r))
print(f"V3放行映射引擎翻转: {len(sel)} 笔 (方向不符{miss_dir}, 非翻转{not_flip})")
from collections import Counter
print("  路径(按CSV V3):", dict(Counter(signal_v3.v3_decide(side(r),build_feats(r,ts2i[msec(r['时间'])],side(r)))['path'] for _,_,_,r in sel)))

P={"periods":10,"multiplier":3.0,"src":"hl2","change_atr":True}
def run_engine(er):
    return BE.run_backtest(base,P,init_cash=10000.0,fee_rate=0.0005,
                           allow_short=True,leverage=1,sizing="equity",
                           exit_rules=er,full_trades=True)
def metrics(pairs,label):
    pairs=sorted(pairs,key=lambda x:x[0]);pn=[x[1] for x in pairs];n_=len(pn)
    if n_==0:print(f"[{label}] 空集");return
    wins=[x for x in pn if x>0];gl=-sum(x for x in pn if x<=0)
    eq=1.0;pk=1.0;mdd=0.0
    for x in pn:eq*=(1+x/100);pk=max(pk,eq);mdd=max(mdd,pk-eq)
    pf=sum(wins)/gl if gl else float("inf")
    red=sum(1 for x in pn if x>TH)
    print(f"[{label:26s}] 笔{n_:4d} 复利{eq-1:7.1%} 胜率{len(wins)/n_*100:4.1f}% PF={pf:4.2f} DD{mdd/pk*100:5.1f}% 红字{red:3d}")

print("\n===== 引擎真版出场(run_backtest, 精确550) =====")
variants={
 "C 无trailing(反向平仓基线)": None,
 "A 纯ST-trailing(无TP)": ExitRules(enabled=True,trail_with_st=True,sl_mode="st",
        move_sl_to_entry=False,tp1_pct=1e9,tp1_ratio=0,sl_pct=2.0),
 "B 实盘默认(TP1+保本+trailing)": ExitRules(enabled=True,trail_with_st=True,sl_mode="st",
        move_sl_to_entry=True,tp1_pct=1.5,tp1_ratio=70,sl_pct=2.0),
 "D 增强默认(TP3=+3.5%全平)": EnhancedExitRules(enabled=True,trail_with_st=True,sl_mode="st",
        move_sl_to_entry=True,sl_pct=2.0),
 "E 增强(TP3=反向信号,大赢家放行)": EnhancedExitRules(enabled=True,trail_with_st=True,sl_mode="st",
        move_sl_to_entry=True,sl_pct=2.0,tp3_mode="reverse_signal"),
}
sel_ts={ts for _,ts,_,_ in sel}
for name,er in variants.items():
    res=run_engine(er)
    tmap={t["entry_ts"]:t for t in res["trades_list"]}
    pairs=[(ts,tmap[ts]["pnl_pct"]) for ts in sel_ts if ts in tmap]
    metrics(pairs,name)
    print(f"    命中{len(pairs)}/漏{len(sel_ts)-len(pairs)}")

# ── 自包含研究模型: 同550 反向平仓基线+trail叠加(对账) ──
print("\n===== 研究反事实(同550: 反向平仓基线+trail叠加) =====")
def next_opp(i,sd):
    for j,typ in flips:
        if j>i and ((typ=="sell" and sd==1) or (typ=="buy" and sd==-1)):return j
    return n-1
def baseline_pnl(i,sd):
    j=next_opp(i,sd);return (c[j]-c[i])/c[i]*100*sd
def trail_overlay(i,sd,T):
    j=next_opp(i,sd);a0=atr[i] or 0.0;entry=c[i];peak=entry;conf=False
    for k in range(i+1,j+1):
        hi,lo=h[k],l[k]
        if sd==1:
            peak=max(peak,hi)
            if not conf and (peak-entry)>=T*a0:conf=True
            if conf:
                stop=peak-T*a0
                if lo<=stop:return (stop-entry)/entry*100
        else:
            peak=min(peak,lo)
            if not conf and (entry-peak)>=T*a0:conf=True
            if conf:
                stop=peak+T*a0
                if hi>=stop:return (entry-stop)/entry*100
    return baseline_pnl(i,sd)
metrics([(ts,baseline_pnl(fi,sd)) for fi,ts,sd,_ in sel],"研究 反向平仓基线")
for T in [1.0,1.5,2.0]:
    metrics([(ts,trail_overlay(fi,sd,T)) for fi,ts,sd,_ in sel],f"研究 trail T={T}")
