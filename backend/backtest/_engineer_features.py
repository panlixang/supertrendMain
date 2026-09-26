# -*- coding: utf-8 -*-
"""从原始 1h K 线重新构造特征, 测试能否抬高 大赢家(盈亏>2%) 的 17% 基准率。"""
import sys, os, json, csv, math, statistics as st
BASE = r"d:\个人项目代码\supertrendMain\backend"
sys.path.insert(0, BASE)
from indicators import super_trend

OUT_JSON = os.path.join(BASE, "backtest", "btc_1h_full.json")
SRC = r"C:\Users\Administrator\AppData\Local\Temp\codebuddy-dropped-files\46a5ccbd-5091-496d-b717-8e8a681729ae\st_signals_1h_features.csv"
TH = 2.0

# ---- 加载 K 线 ----
d = json.load(open(OUT_JSON, encoding="utf-8"))
base = d["base"]
o=[b["o"] for b in base]; h=[b["h"] for b in base]; l=[b["l"] for b in base]
c=[b["c"] for b in base]; ts=[b["ts"] for b in base]; n=len(c)
st_res = super_trend(o,h,l,c,periods=10,multiplier=3.0,change_atr=True)
atr = st_res["atr"]
atr_pct=[ (atr[i]/c[i]*100 if (atr[i] and c[i]) else 0.0) for i in range(n) ]

# ---- 加载信号 + 标签 ----
rows=list(csv.DictReader(open(SRC, encoding="utf-8-sig")))
def msec(s):
    import calendar, datetime
    return calendar.timegm(datetime.datetime.strptime(s,"%Y/%m/%d %H:%M").timetuple())
base_sec={int(b["ts"]/1000):i for i,b in enumerate(base)}
def fnum(x):
    try: return float(x)
    except: return None

# ---- 构造特征函数 (只用 [0..i]) ----
def maxmin(a,lo,hi):  # hi exclusive
    if hi<=lo: return 0,0
    return max(a[lo:hi]), min(a[lo:hi])

feat_names=[]
def build_features(i, sig):
    f={}
    if i<50: return None
    ap=atr_pct
    # ATR 序列
    atr_now=ap[i]; atr_10=ap[i-10]; atr_20=ap[i-20]; atr_50=ap[i-50]
    # 1) 波动率收缩(线圈): 近期ATR相对50根前更低
    f["atr_contract50"] = (atr_20/atr_50 - 1.0) if atr_50>0 else 0.0   # <0=收缩
    f["atr_contract20"] = (atr_10/atr_20 - 1.0) if atr_20>0 else 0.0
    # 2) ATR 突破(扩张): 最近10根ATR相对之前扩张
    f["atr_expand10"] = (atr_now/atr_10 - 1.0) if atr_10>0 else 0.0     # >0=扩张
    f["atr_expand20"] = (atr_now/atr_20 - 1.0) if atr_20>0 else 0.0
    # 3) 区间压缩率: 近10根振幅 / 前40根振幅
    h1,lo1=maxmin(h,i-10,i); h2,lo2=maxmin(h,i-50,i-10)
    rng1=h1-lo1; rng2=h2-lo2
    f["range_compress"] = (rng1/rng2 - 1.0) if rng2>0 else 0.0          # <0=压缩
    # 4) 关键位突破幅度(对齐信号): 突破前50根通道
    hh,ll=maxmin(h,i-50,i); 
    if sig>0: f["donchian_break"]=(c[i]-hh)/atr[i] if atr[i] else 0.0
    else:     f["donchian_break"]=(ll-c[i])/atr[i] if atr[i] else 0.0
    # 5) 突破近期摆动高点/低点幅度(对齐)
    hh2,ll2=maxmin(h,i-20,i); 
    if sig>0: f["swing_break"]=(c[i]-hh2)/atr[i] if atr[i] else 0.0
    else:     f["swing_break"]=(ll2-c[i])/atr[i] if atr[i] else 0.0
    # 6) ATR 百分位排名(近期100根内)
    win=ap[i-100:i+1]
    f["atr_rank"]=sum(1 for x in win if x<=atr_now)/len(win) if win else 0.5
    # 7) 突破K线强度(对齐): 近5根顺信号方向净位移(ATR化)
    if sig>0: f["break_mom5"]=(c[i]-c[i-5])/atr[i] if atr[i] else 0.0
    else:     f["break_mom5"]=(c[i-5]-c[i])/atr[i] if atr[i] else 0.0
    # 8) 波动率收缩后再扩张的组合信号得分
    f["coil_then_break"] = 1.0 if (f["atr_contract50"]<0 and f["atr_expand10"]>0) else 0.0
    return f

# ---- 汇总 ----
recs=[]
for r in rows:
    i=base_sec.get(msec(r["时间"]))
    if i is None or i<50: continue
    sig=int(r["信号"])
    f=build_features(i,sig)
    if f is None: continue
    f["big"]=1 if (fnum(r["盈亏"]) or 0)>TH else 0
    f["pnl"]=fnum(r["盈亏"]) or 0
    recs.append(f)

big=[x for x in recs if x["big"]]
print(f"匹配信号 {len(recs)} 笔, 大赢家 {len(big)} 笔, 基准率={len(big)/len(recs)*100:.1f}%")

# ---- 单特征阈值扫描 ----
print("\n=== 单特征富集扫描 (大赢家密度 vs 基准) ===")
def scan(name, vals, bins):
    for lo,hi,lab in bins:
        if lo is None: keep=[x for x in recs if x[vals]<=hi]
        elif hi is None: keep=[x for x in recs if x[vals]>=lo]
        else: keep=[x for x in recs if lo<=x[vals]<hi]
        if not keep: continue
        kb=sum(x["big"] for x in keep)
        dens=kb/len(keep)*100; cov=kb/len(big)*100
        print(f"  {name:16s} {lab:14s} 保留{len(keep):4d} 密度={dens:4.1f}% 覆盖={cov:4.0f}%")

scan("atr_contract50", "atr_contract50", [(-1.0,-0.2,"<-0.2"),(-0.2,0.0,"-0.2~0"),(None,0.0,"<=0"),(0.0,None,">0")])
scan("atr_expand10", "atr_expand10", [(-1.0,0.0,"<0"),(0.0,0.2,"0~0.2"),(0.2,None,">0.2")])
scan("range_compress", "range_compress", [(-1.0,-0.3,"<-0.3"),(-0.3,0.0,"-0.3~0"),(None,0.0,"<=0")])
scan("donchian_break", "donchian_break", [(0.0,0.5,"0~0.5"),(0.5,1.0,"0.5~1"),(1.0,None,">1")])
scan("swing_break", "swing_break", [(0.0,0.5,"0~0.5"),(0.5,None,">0.5")])
scan("atr_rank", "atr_rank", [(0.0,0.4,"<0.4"),(0.4,0.6,"0.4~0.6"),(0.6,1.01,">0.6")])
scan("break_mom5", "break_mom5", [(0.0,0.5,"0~0.5"),(0.5,1.0,"0.5~1"),(1.0,None,">1")])

# ---- 组合规则 ----
print("\n=== 组合规则 ===")
def ev(pred,label):
    keep=[x for x in recs if pred(x)]
    if not keep: print(f"[{label}] 0"); return
    kb=sum(x["big"] for x in keep)
    pnls=[x["pnl"] for x in keep]
    print(f"[{label}] 保留{len(keep):4d} 密度={kb/len(keep)*100:4.1f}% 覆盖={kb/len(big)*100:4.0f}% 累计={sum(pnls):7.1f}%")
ev(lambda x: x["coil_then_break"]==1, "线圈后突破(coil)")
ev(lambda x: x["atr_contract50"]<0 and x["atr_expand10"]>0 and x["donchian_break"]>0.5, "线圈+扩张+突破0.5")
ev(lambda x: x["range_compress"]<-0.3 and x["atr_expand10"]>0, "区间压缩+扩张")
ev(lambda x: x["donchian_break"]>0.5 and x["atr_rank"]>0.5, "突破>0.5 & ATR中高位")
ev(lambda x: x["coil_then_break"]==1 and x["donchian_break"]>0.3, "线圈突破 & 通道突破>0.3")
ev(lambda x: x["range_compress"]<-0.2 and x["break_mom5"]>0.5, "压缩 & 突破动能>0.5")
# 真实信号组合: 波动率已高位 + 突破
ev(lambda x: x["atr_contract50"]>0 and x["donchian_break"]>0.5, "波动已高 & 通道突破>0.5")
ev(lambda x: x["atr_contract50"]>0 and x["break_mom5"]>0.5, "波动已高 & 突破动能>0.5")
ev(lambda x: x["atr_rank"]>0.6 and x["donchian_break"]>0.5, "ATR高位 & 通道突破>0.5")
ev(lambda x: x["atr_rank"]>0.6 and x["swing_break"]>0.5, "ATR高位 & 摆动突破>0.5")
ev(lambda x: x["atr_contract50"]>0 and x["atr_expand10"]>0 and x["donchian_break"]>0.3, "波动高&扩张&突破0.3")
ev(lambda x: x["atr_rank"]>0.6 and x["break_mom5"]>0.5, "ATR高位 & 突破动能>0.5")
ev(lambda x: x["donchian_break"]>0.5 and x["swing_break"]>0.5, "通道突破>0.5 & 摆动突破>0.5")
