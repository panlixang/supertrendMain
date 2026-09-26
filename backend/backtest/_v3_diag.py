# -*- coding: utf-8 -*-
"""V3 归因诊断：漏掉的大赢家 + V3放行内的亏损集中在哪 + 假突破画像。"""
import sys, os, csv, json, statistics as st
import calendar, datetime
BASE = r"d:\个人项目代码\supertrendMain\backend"
sys.path.insert(0, BASE)
from indicators import super_trend, ta_sma

OUT_JSON=os.path.join(BASE,"backtest","btc_1h_full.json")
SRC=r"C:\Users\Administrator\AppData\Local\Temp\codebuddy-dropped-files\46a5ccbd-5091-496d-b717-8e8a681729ae\st_signals_1h_features.csv"
TH=2.0

d=json.load(open(OUT_JSON,encoding="utf-8"));base=d["base"]
o=[b["o"] for b in base];h=[b["h"] for b in base];l=[b["l"] for b in base];c=[b["c"] for b in base];n=len(c)
st_res=super_trend(o,h,l,c,periods=10,multiplier=3.0,change_atr=True)
atr=st_res["atr"];trend=st_res["trend"];up=st_res["up"];dn=st_res["dn"];flips=sorted(f["i"] for f in st_res["flips"])
ma30=ta_sma(c,30);atr_pct=[(atr[i]/c[i]*100 if(atr[i] and c[i]) else 0.0) for i in range(n)]
st_dist=[0.0]*n
for i in range(n):
    if trend[i] is None or atr[i] in(None,0):continue
    line=up[i] if trend[i]==1 else dn[i];st_dist[i]=(c[i]-line)/atr[i]
def er20_at(i):
    if i<20:return 0.0
    num=abs(c[i]-c[i-20]);den=sum(abs(c[k]-c[k-1]) for k in range(i-20,i));return num/den if den>0 else 0.0
def flip50(i):return sum(1 for f in flips if i-50<f<=i)

rows=list(csv.DictReader(open(SRC,encoding="utf-8-sig")))
def msec(s):return calendar.timegm(datetime.datetime.strptime(s,"%Y/%m/%d %H:%M").timetuple())
base_sec={int(b["ts"]/1000):i for i,b in enumerate(base)}
def fnum(x):
    try:return float(x)
    except:return None
def pnl(r):return fnum(r["盈亏"]) or 0.0
def res(r):return r["止盈止损"]
def side(r):return int(r["信号"])

# ---- 闸门（与 _v3.py / signal_v3.py 完全一致）----
def long_mature(r):
    s4=fnum(r["4h_MA30斜率"]);d1=fnum(r["1h_MA30距离"])
    if s4 is None or d1 is None:return False
    return (s4>0.16) or (s4>0 and d1>0.13)
def short_gate(r):
    vc=fnum(r["前100根_波动周期"]);adx=fnum(r["前20根_ADX变化"]);d4=fnum(r["4h_MA30距离"]);er=fnum(r["前20根_ER变化"])
    if None in(vc,adx,d4,er):return False
    if vc<9.5 and adx<3.25:return True
    if vc>9.5 and d4>-0.7 and er>-0.06:return True
    return False
def long_bypass(i,sig):
    bm=(c[i]-c[i-5])/atr[i] if sig>0 else (c[i-5]-c[i])/atr[i] if atr[i] else 0.0
    ac=atr_pct[i-20]/atr_pct[i-50]-1.0 if(i>=50 and atr_pct[i-50]>0) else 0.0
    stc=st_dist[i]-st_dist[i-20]
    cmd=abs(c[i]-ma30[i])/atr[i] if(ma30[i] and atr[i]) else 0.0
    return dict(ok=(bm>0.5 and ac>0 and stc>0 and cmd<3),bm=bm,ac=ac,stc=stc,cmd=cmd)

def diagnose(r):
    i=base_sec.get(msec(r["时间"]))
    s=side(r)
    if i is None:return dict(pass_=False,path="无K线",reason="无K线",i=None)
    f50=flip50(i);er=er20_at(i)
    # 成熟趋势
    if s==1:
        if long_mature(r):
            fused = (f50>8 and er<0.15)
            return dict(pass_=not fused,path="成熟趋势",reason=("极端震荡熔断" if fused else ""),i=i,f50=f50,er=er)
        bp=long_bypass(i,s)
        if bp["ok"]:
            fused=(f50>8 and er<0.15)
            return dict(pass_=not fused,path="早期启动",reason=("极端震荡熔断" if fused else ""),i=i,f50=f50,er=er,**bp)
        # 失败归因
        s4=fnum(r["4h_MA30斜率"]);d1=fnum(r["1h_MA30距离"])
        if s4 is not None and s4<=0:
            reason="多:4h斜率≤0(未呈趋势)"
        elif s4 is not None and s4>0 and d1 is not None and d1<=0.13:
            reason="多:斜率>0但距MA30不足"
        else:
            reason="多:早期旁路未达成(动量/收缩/ST/追涨)"
        return dict(pass_=False,path="未通过",reason=reason,i=i,f50=f50,er=er,**bp)
    else:
        if short_gate(r):
            fused=(f50>8 and er<0.15)
            return dict(pass_=not fused,path="成熟趋势",reason=("极端震荡熔断" if fused else ""),i=i,f50=f50,er=er)
        vc=fnum(r["前100根_波动周期"]);adx=fnum(r["前20根_ADX变化"]);d4=fnum(r["4h_MA30距离"]);er_c=fnum(r["前20根_ER变化"])
        if vc is not None and vc<9.5:
            reason="空:波动周期<9.5但ADX变化≥3.25(震荡不够静)"
        elif vc is not None and vc>=9.5:
            reason="空:波动周期≥9.5但(距4hMA≤-0.7或ER变化≤-0.06)"
        else:
            reason="空:特征缺失"
        return dict(pass_=False,path="未通过",reason=reason,i=i,f50=f50,er=er)

for r in rows:
    r["_d"]=diagnose(r); r["_pnl"]=pnl(r)

# ============ PART A: 漏掉的大赢家 ============
print("="*70)
print(f"PART A: 大赢家(盈亏>2%) 但 V3 未放行  -- 共 {sum(1 for r in rows if r['_pnl']>TH and not r['_d']['pass_'])} 个被漏掉的'趋势机会'")
rej_win=[r for r in rows if r["_pnl"]>TH and not r["_d"]["pass_"]]
print("  其中 多:%d 空:%d"%(sum(1 for r in rej_win if side(r)==1),sum(1 for r in rej_win if side(r)==-1)))
# 失败原因分布
from collections import Counter
rc=Counter(r["_d"]["reason"] for r in rej_win)
print("  失败原因分布:")
for k,v in rc.most_common():print("    %-40s %d"%(k,v))
# 趋势区间分布
print("  趋势区间分布:",dict(Counter(r["趋势区间"] for r in rej_win)))
# 关键特征均值（被漏赢家 vs 被保留赢家）
kept_win=[r for r in rows if r["_pnl"]>TH and r["_d"]["pass_"]]
def meanfeat(sel,col):
    v=[fnum(r[col]) for r in sel if fnum(r[col]) is not None];return sum(v)/len(v) if v else float("nan")
for col in ["4h_MA30斜率","1h_MA30距离","4h_MA30距离","前20根_ER变化","前50根_ST翻转次数","前100根_波动周期"]:
    print("    %-18s 漏掉赢家均=%.3f  保留赢家均=%.3f"%(col,meanfeat(rej_win,col),meanfeat(kept_win,col)))

# ============ PART B: V3放行内的亏损 ============
print("="*70)
v3=[r for r in rows if r["_d"]["pass_"]]
loss=[r for r in v3 if r["_pnl"]<0]
print("PART B: V3放行 %d 笔, 其中亏损 %d 笔(%.1f%%), 累计亏损 %.2f%%"%(
    len(v3),len(loss),len(loss)/len(v3)*100,sum(r["_pnl"] for r in loss)))
print("  亏损按路径: %s"%dict(Counter(r["_d"]["path"] for r in loss)))
print("  亏损按方向: 多=%d 空=%d"%(sum(1 for r in loss if side(r)==1),sum(1 for r in loss if side(r)==-1)))
print("  亏损按趋势区间: %s"%dict(Counter(r["趋势区间"] for r in loss)))
# flip50 分桶
def bucket(val):
    if val<=3:return "≤3"
    if val<=6:return "4-6"
    if val<=8:return "7-8"
    return ">8"
print("  亏损按 flip50: %s"%dict(Counter(bucket(r["_d"]["f50"]) for r in loss)))
print("  全部V3按 flip50: %s"%dict(Counter(bucket(r["_d"]["f50"]) for r in v3)))
# 亏损深度
print("  亏损笔均盈亏=%.2f%%  最差单笔=%.2f%%"%(sum(r["_pnl"] for r in loss)/len(loss),min(r["_pnl"] for r in loss)))

# ============ PART C: 假突破（早期启动放行却SL）============
print("="*70)
early_pass=[r for r in v3 if r["_d"]["path"]=="早期启动"]
early_loss=[r for r in early_pass if r["_pnl"]<0]
early_win=[r for r in early_pass if r["_pnl"]>=0]
print("PART C: 早期启动旁路放行 %d 笔, 其中亏损 %d(%.1f%%)"%(len(early_pass),len(early_loss),len(early_loss)/len(early_pass)*100))
print("  ── 早期启动 入口特征对比(失败 vs 成功) ──")
for k,lab in [("bm","动量mom5"),("ac","ATR收缩转扩张"),("stc","ST距离变化"),("cmd","|C-MA30|/ATR")]:
    fv=[r["_d"].get(k) for r in early_loss if r["_d"].get(k) is not None]
    wv=[r["_d"].get(k) for r in early_win if r["_d"].get(k) is not None]
    if fv and wv:
        print("    %-18s 失败均=%.3f  成功均=%.3f  失败min=%.3f(阈值:bm>0.5 ac>0 stc>0 cmd<3)"%(
            lab,sum(fv)/len(fv),sum(wv)/len(wv),min(fv)))
# 早期失败里有多少其实已接近阈值(临界点假突破)
near=[r for r in early_loss if r["_d"].get("cmd") is not None and r["_d"]["cmd"]>=2.5]
print("  早期失败里 |C-MA30|/ATR∈[2.5,3) 临界追涨: %d 笔"%len(near))
# 入场后最高/最低 看是否被扫
def sweep(r):
    # 入场后最高值/最低值 相对入场
    try:
        en,hi,lo=[float(x) for x in r["出入场"].split("->")],[float(r["入场后最高值"])],[] 
        entry=float(r["出入场"].split("->")[0]); hi=float(r["入场后最高值"]); lo=float(r["最低值"])
        return entry,hi,lo
    except:return None,None,None
print("  早期失败: 先触最高再回落(假突破型) vs 直接反向")
fake=0;direct=0
for r in early_loss:
    entry=float(r["出入场"].split("->")[0]); hi=float(r["入场后最高值"]); lo=float(r["最低值"])
    if side(r)==1:
        if hi>entry*1.002: fake+=1   # 先涨过0.2%再跌
        else: direct+=1
    else:
        if lo<entry*0.998: fake+=1
        else: direct+=1
print("    先假突破(触发后反转): %d   直接反向: %d"%(fake,direct))

# ============ PART D: 漏掉赢家 是否属'假突破反被拦' ============
print("="*70)
print("PART D: 漏掉的大赢家 入场后走势(是否被ST翻转扫掉又拉回)")
fake_win=0
for r in rej_win:
    entry=float(r["出入场"].split("->")[0]); hi=float(r["入场后最高值"]); lo=float(r["最低值"])
    if side(r)==1:
        # 空头信号? 实际是做空还是做多? 信号-1=做空
        pass
# 信号方向说明: 信号=1 做多, =-1 做空
rfake=0
for r in rej_win:
    entry=float(r["出入场"].split("->")[0]); hi=float(r["入场后最高值"]); lo=float(r["最低值"])
    if side(r)==1:  # 做多
        if lo<entry*0.995: rfake+=1  # 先下破0.5%再拉回成大赢家
    else:            # 做空
        if hi>entry*1.005: rfake+=1
print(f"  漏掉赢家里 '先逆信号破0.5%再反转成大赢' 的有 {rfake} 笔 (震荡洗盘型)")
