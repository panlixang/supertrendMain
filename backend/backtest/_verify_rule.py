# -*- coding: utf-8 -*-
import sys, os, json, csv, statistics as st
BASE = r"d:\个人项目代码\supertrendMain\backend"
sys.path.insert(0, BASE)
from indicators import super_trend

OUT_JSON = os.path.join(BASE, "backtest", "btc_1h_full.json")
SRC = r"C:\Users\Administrator\AppData\Local\Temp\codebuddy-dropped-files\46a5ccbd-5091-496d-b717-8e8a681729ae\st_signals_1h_features.csv"
TH = 2.0

d = json.load(open(OUT_JSON, encoding="utf-8"))
base = d["base"]
o=[b["o"] for b in base]; h=[b["h"] for b in base]; l=[b["l"] for b in base]
c=[b["c"] for b in base]; ts=[b["ts"] for b in base]; n=len(c)
st_res = super_trend(o,h,l,c,periods=10,multiplier=3.0,change_atr=True)
atr = st_res["atr"]
atr_pct=[ (atr[i]/c[i]*100 if (atr[i] and c[i]) else 0.0) for i in range(n) ]

rows=list(csv.DictReader(open(SRC, encoding="utf-8-sig")))
import calendar, datetime
def msec(s): return calendar.timegm(datetime.datetime.strptime(s,"%Y/%m/%d %H:%M").timetuple())
base_sec={int(b["ts"]/1000):i for i,b in enumerate(base)}
def fnum(x):
    try: return float(x)
    except: return None

def build(i,sig):
    if i<50: return None
    atr_now=atr_pct[i]; atr_10=atr_pct[i-10]; atr_20=atr_pct[i-20]; atr_50=atr_pct[i-50]
    f={}
    f["atr_contract50"]=(atr_20/atr_50-1.0) if atr_50>0 else 0.0
    if sig>0: f["break_mom5"]=(c[i]-c[i-5])/atr[i] if atr[i] else 0.0
    else:     f["break_mom5"]=(c[i-5]-c[i])/atr[i] if atr[i] else 0.0
    # 同时算原 ER/ADX 规则所需
    return f

recs=[]
for r in rows:
    i=base_sec.get(msec(r["时间"]))
    if i is None or i<50: continue
    sig=int(r["信号"])
    f=build(i,sig)
    if f is None: continue
    f["big"]=1 if (fnum(r["盈亏"]) or 0)>TH else 0
    f["pnl"]=fnum(r["盈亏"]) or 0
    f["tp"]=1 if r["止盈止损"]=="TP" else 0
    f["sig"]=sig
    recs.append(f)

print(f"匹配 {len(recs)} 笔 (全量808)  大赢家基准 {sum(x['big'] for x in recs)} 笔 = {sum(x['big'] for x in recs)/len(recs)*100:.1f}%")

def report(keep,label):
    n=len(keep)
    if n==0: print(f"[{label}] 0"); return
    tp=sum(x["tp"] for x in keep); sl=n-tp
    pnls=[x["pnl"] for x in keep]; big=sum(x["big"] for x in keep)
    print(f"[{label}]")
    print(f"  保留 {n}/808 笔 ({n/808*100:.1f}%) | TP={tp} SL={sl} 胜率={tp/n*100:.1f}%")
    print(f"  平均盈亏={st.mean(pnls):.3f}%  累计盈亏={sum(pnls):.2f}%")
    print(f"  大赢家(>2%)密度={big/n*100:.1f}% ({big}笔)  覆盖大赢家={big/sum(x['big'] for x in recs)*100:.0f}%")

# 目标规则
r1=[x for x in recs if x["atr_contract50"]>0 and x["break_mom5"]>0.5]
report(r1, "目标规则: atr_contract50>0 且 break_mom5>0.5")

# 对照: 原 ER/ADX 规则 (需从CSV取)
er=[fnum(r["前20根_ER变化"]) for r in rows]; adx=[fnum(r["前20根_ADX变化"]) for r in rows]
# 重新用recs顺序对齐不方便, 直接对rows算并map
keep_eradx=[]
for r in rows:
    i=base_sec.get(msec(r["时间"]))
    if i is None or i<50: continue
    if (fnum(r["前20根_ER变化"]) or 0)>0 and (fnum(r["前20根_ADX变化"]) or 0)>0:
        keep_eradx.append(r)
report([{"pnl":fnum(r["盈亏"]) or 0,"big":1 if (fnum(r["盈亏"]) or 0)>TH else 0,
         "tp":1 if r["止盈止损"]=="TP" else 0} for r in keep_eradx], "对照: ER>0 & ADX>0")
report(recs, "对照: 全量808")
