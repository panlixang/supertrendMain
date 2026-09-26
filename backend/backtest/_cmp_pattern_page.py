# -*- coding: utf-8 -*-
"""形态识别页真实口径 vs 回测口径 对比(全长)."""
import sys, csv, json, calendar, datetime
BASE = r"d:\个人项目代码\supertrendMain\backend"
sys.path.insert(0, BASE)
import signal_v3
from indicators import super_trend, ta_sma
from position import ExitRules
from position_enhanced import EnhancedExitRules
import backtest_engine as BE

FEAT = r"C:\Users\Administrator\AppData\Local\Temp\codebuddy-dropped-files\46a5ccbd-5091-496d-b717-8e8a681729ae\st_signals_1h_features.csv"
d = json.load(open(BASE + r"\backtest\btc_1h_full.json", encoding="utf-8")); base = d["base"]
c=[b["c"] for b in base]; o=[b["o"] for b in base]; h=[b["h"] for b in base]; l=[b["l"] for b in base]; n=len(c)
st = super_trend(o,h,l,c,periods=10,multiplier=3.0,change_atr=True)
flip_idxs=sorted(f["i"] for f in st["flips"]); flip_type={f["i"]:f["type"] for f in st["flips"]}
atr=st["atr"]; ma30=ta_sma(c,30)
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
    b=signal_v3.base_features(c,atr,atr_pct,er20,st_dist,flip_idxs,ma30,i,sd)
    if b is None: return None
    b.update({"slope_htf":num(r.get("4h_MA30斜率")),"dist_base_ma":num(r.get("1h_MA30距离")),
        "dist_htf_ma":num(r.get("4h_MA30距离")),"adx_chg20":num(r.get("前20根_ADX变化")),
        "er_chg20":num(r.get("前20根_ER变化")),"vol100":num(r.get("前100根_波动周期"))}); return b
# V3过滤后的550
sel=[]
for r in fr_rows:
    ts=msec(r["时间"]); i=ts2i.get(ts)
    if i is None: continue
    sd=int(r["信号"]); f=bf(r,i,sd)
    if f is None: continue
    if not signal_v3.v3_decide(sd,f)["execute"]: continue
    fi=i-1
    if fi not in flip_type: continue
    if flip_type[fi] != ("buy" if sd==1 else "sell"): continue
    sel.append((fi, base[fi]["ts"], sd))
# 全量原始信号(无V3) = 所有 flip
sel_all=[(fi, base[fi]["ts"], (1 if flip_type[fi]=="buy" else -1)) for fi in flip_idxs]
T2026 = calendar.timegm((2026,1,1,0,0,0))
print("V3过滤后:", len(sel), " 原始全量flip:", len(sel_all))
P={"periods":10,"multiplier":3.0,"src":"hl2","change_atr":True}
def run_engine(er):
    return BE.run_backtest(base,P,init_cash=10000.0,fee_rate=0.0005,
                           allow_short=True,leverage=1,sizing="equity",
                           exit_rules=er,full_trades=True)
def metrics(pairs,label):
    if not pairs: print("  [%s] 空集"%label); return
    pn=[x[1] for x in pairs]; n_=len(pn)
    wins=[x for x in pn if x>0]; gl=-sum(x for x in pn if x<=0)
    eq=1.0; pk=1.0; mdd=0.0
    for x in pn: eq*=(1+x/100); pk=max(pk,eq); mdd=max(mdd,pk-eq)
    pf=sum(wins)/gl if gl else float("inf")
    print("  [%s] 笔%3d 复利%7.1f%% 胜率%4.1f%% PF=%4.2f DD%5.1f%% 红字%d 均%5.2f%% R/DD%4.2f"%(
        label,n_,(eq-1)*100,len(wins)/n_*100,pf,mdd/pk*100,
        sum(1 for x in pn if x>2.0),sum(pn)/n_,(eq-1)/(mdd/pk) if mdd>0 else 0))
def report(name,er,iset):
    res=run_engine(er); tmap={t["entry_ts"]:t for t in res["trades_list"]}
    full=[(ts,tmap[ts]["pnl_pct"]) for _,ts,_ in iset if ts in tmap]
    y26=[(ts,tmap[ts]["pnl_pct"]) for _,ts,_ in iset if ts>=T2026*1000 and ts in tmap]
    print("\n== %s =="%name); metrics(full,"全长"); metrics(y26,"2026")

# A. 形态识别页真实口径: 原版ExitRules, TP1 1.5% 平70%, 单档 + 保本 + ST跟踪, 无V3过滤
report("【形态页真实】ExitRules TP1=1.5%平70% (无V3)",
       ExitRules(tp1_pct=1.5,tp1_ratio=70.0,sl_mode="st",sl_pct=2.0,
                 move_sl_to_entry=True,trail_with_st=True,reverse_close=False), sel_all)
# B. 形态页口径但开V3过滤
report("【形态页+V3】ExitRules TP1=1.5%平70% (V3过滤后)",
       ExitRules(tp1_pct=1.5,tp1_ratio=70.0,sl_mode="st",sl_pct=2.0,
                 move_sl_to_entry=True,trail_with_st=True,reverse_close=False), sel)
# C. 我回测的三挡口径: EnhancedExitRules 三挡 TP1=1%平30% (V3过滤后)
report("【我回测】EnhancedExitRules 三挡 TP1=1%平30% (V3过滤后)",
       EnhancedExitRules(enabled=True,trail_with_st=True,sl_mode="st",move_sl_to_entry=True,
                         sl_pct=2.0,tp1_pct=1.0,tp1_ratio=30.0,tp2_pct=2.0,tp2_ratio=40.0,
                         tp3_pct=3.5,tp3_ratio=100.0), sel)
