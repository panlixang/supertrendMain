# -*- coding: utf-8 -*-
"""
「避开最差 K 个 regime + 波动缩放」前向验证 (walk-forward, 按年切折)
================================================================
每年 t 只用 t 之前年份决定：
  1) 训练窗内 mean_net 最低的 K 个 regime（n>=10）-> 砍掉
  2) ATR 缩放中枢（训练窗 ATR 中位数）
再在当年样本外评估。对比 K=1(原规则) 与 K=2(砍趋势+启动,只留震荡)。
产物：backtest/st_walkforward2.json
"""
from __future__ import annotations
import csv, json, sys
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from indicators import super_trend, ta_atr

BASE = Path(__file__).resolve().parent
candles = json.loads((BASE / "btc_1h_full.json").read_text(encoding="utf-8"))["base"]
o=[c["o"] for c in candles]; h=[c["h"] for c in candles]
l=[c["l"] for c in candles]; cl=[c["c"] for c in candles]; FEE=0.05
st=super_trend(o,h,l,cl,periods=10,multiplier=3.0,change_atr=True)
flips=sorted(st["flips"],key=lambda f:f["i"]); atr=ta_atr(h,l,cl,14)
ms={}
for r in csv.DictReader(open(BASE/"st_signals_full.csv",encoding="utf-8-sig")):
    try: ms[int(r["ts"])]=int(r["market_state"])
    except: pass
LABEL={0:"震荡",1:"趋势",2:"启动"}
trades=[]
for k in range(len(flips)-1):
    i,j=flips[k]["i"],flips[k+1]["i"]
    if j<=i: continue
    side=1 if flips[k]["type"]=="buy" else -1
    net=(cl[j]-cl[i])/cl[i]*100*side-2*FEE
    ap=atr[i]/cl[i]*100 if atr[i] and cl[i] else None
    y=datetime.fromtimestamp(candles[i]["ts"]/1000,tz=timezone.utc).year
    trades.append({"net":net,"atr":ap,"bars":j-i,"year":y,"ms":ms.get(candles[i]["ts"],-1)})
years=sorted(set(t["year"] for t in trades))

def sim(items,size_of):
    n=len(items)
    if n<3: return {"n":n,"total":None,"sharpe":None,"mdd":None}
    eq=1.0;peak=1.0;dds=[0.0];per=np.zeros(n);held=0
    for k,t in enumerate(items):
        r=size_of(t)*t["net"]/100.0; per[k]=r; eq*=(1+r); held+=t["bars"]
        peak=max(peak,eq); dds.append(eq/peak-1)
    curve=np.array(dds); total=(eq-1)*100
    span=max(held,1)/(24*365); sharpe=per.mean()/per.std()*np.sqrt(n/span) if per.std()>0 else 0.0
    return {"n":n,"total":round(float(total),1),"sharpe":round(float(sharpe),2),"mdd":round(float(curve.min()*100),1)}

def sz(med): return lambda t: np.clip(med/t["atr"],0.25,2.0) if t["atr"] else 1.0

KS=[1,2,3]
folds=[]
oos={K:[] for K in KS}; oos_A=[]; oos_B=[]
for y in years[1:]:
    train=[t for t in trades if t["year"]<y]; test=[t for t in trades if t["year"]==y]
    by_state={}
    for t in train: by_state.setdefault(t["ms"],[]).append(t["net"])
    cand={s:v for s,v in by_state.items() if len(v)>=10}
    ranked=sorted(cand,key=lambda s:np.mean(cand[s]))        # 最差(最负)在前
    med=np.median([t["atr"] for t in train if t["atr"]])
    A=sim(test,lambda t:1.0); B=sim(test,sz(med))
    f={"year":y,"n_test":len(test),"A":A["total"],"B":B["total"],"by_K":{}}
    for K in KS:
        dropped=ranked[:K]; kept=[t for t in test if t["ms"] not in dropped]
        d=sim(kept,sz(med)); f["by_K"][K]={"dropped":dropped,
            "dropped_labels":[LABEL.get(s,str(s)) for s in dropped],
            "n_kept":len(kept),"total":d["total"],"sharpe":d["sharpe"],"mdd":d["mdd"]}
        oos[K]+=kept
    oos_A+=test; oos_B+=test
    folds.append(f)

def agg_scale(items):
    m=np.median([t["atr"] for t in items if t["atr"]])
    return sim(items, lambda t: np.clip(m/t["atr"],0.25,2.0) if t["atr"] else 1.0)

aggregate={}
aggregate["A_all_fixed"]=sim(oos_A,lambda t:1.0)
aggregate["B_all_scaled"]=agg_scale(oos_B)
for K in KS:
    aggregate[f"E_drop{K}"]=agg_scale(oos[K])

# 样本内参照（全数据，按全样本最差K个regime）
def insample(K):
    by_state={}; 
    for t in trades: by_state.setdefault(t["ms"],[]).append(t["net"])
    cand={s:v for s,v in by_state.items() if len(v)>=10}
    ranked=sorted(cand,key=lambda s:np.mean(cand[s])); dropped=ranked[:K]
    med=np.median([t["atr"] for t in trades if t["atr"]])
    kept=[t for t in trades if t["ms"] not in dropped]
    d=sim(kept, lambda t: np.clip(med/t["atr"],0.25,2.0) if t["atr"] else 1.0)
    return {"dropped":dropped,"dropped_labels":[LABEL.get(s,str(s)) for s in dropped],**d}

# 固定规则「只交易 震荡(state0) + 缩放」的 OOS（每年缩放中枢用更早年份中位）
only_choppy_year={}; oos_choppy=[]
for y in years[1:]:
    train=[t for t in trades if t["year"]<y]; yr=[t for t in trades if t["year"]==y]
    med=np.median([t["atr"] for t in train if t["atr"]])
    chk=[t for t in yr if t["ms"]==0]
    d=sim(chk, lambda t: np.clip(med/t["atr"],0.25,2.0) if t["atr"] else 1.0)
    only_choppy_year[y]={"n":len(chk),"total":d["total"],"sharpe":d["sharpe"],"mdd":d["mdd"]}
    oos_choppy+=chk
m_ch=np.median([t["atr"] for t in oos_choppy if t["atr"]])
only_choppy_agg=sim(oos_choppy, lambda t: np.clip(m_ch/t["atr"],0.25,2.0) if t["atr"] else 1.0)

out={"meta":{"oos_years":[f["year"] for f in folds],"KS":KS},
     "folds":folds,"aggregate_oos":aggregate,
     "insample":{K:insample(K) for K in KS},
     "only_choppy":{"per_year":only_choppy_year,"aggregate":only_choppy_agg}}
(BASE/"st_walkforward2.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")

print("写出 st_walkforward2.json\n")
print("年份   A固定   B缩放  | K=1掉        K=1总   | K=2掉           K=2总")
for f in folds:
    k1=f["by_K"][1]; k2=f["by_K"][2]
    print("%d  %s  %s | %s  %s | %s  %s"%(
        f["year"],f["A"],f["B"],
        "/".join(k1["dropped_labels"]),k1["total"],
        "/".join(k2["dropped_labels"]),k2["total"]))
print("\n=== OOS 跨年聚合 ===")
print("A 全固定      :",aggregate["A_all_fixed"])
print("B 全缩放      :",aggregate["B_all_scaled"])
for K in KS:
    print(f"E 砍{K}个regime :",aggregate[f"E_drop{K}"])
print("\n=== 样本内参照 ===")
for K in KS:
    r=out["insample"][K]
    print(f"K={K} 掉{ '/'.join(r['dropped_labels']) } 累计 {r['total']:+}% 夏普 {r['sharpe']}")

oc=out["only_choppy"]
print("\n=== 固定规则「只交易 震荡(state0)」OOS ===")
for y,v in oc["per_year"].items():
    print(f"  {y}  n={v['n']:>3}  累计 {v['total']:+}%  夏普 {v['sharpe']}  回撤 {v['mdd']}%")
a=oc["aggregate"]
print(f"  OOS 聚合: 累计 {a['total']:+}%  夏普 {a['sharpe']}  回撤 {a['mdd']}%  n={a['n']}")
