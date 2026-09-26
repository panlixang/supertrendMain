# -*- coding: utf-8 -*-
"""验证 signal_v3 新增「路径3 慢热接住」：用真实模块(含Fuse)跑 808 笔，
对比 旧V3(CSV已存 V3_可执行 列) vs 新V3(带第三路径)。

feats 组装严格照抄 build_signal_features_1h.py，保证与回测/实盘同口径。
"""
import sys, os, csv, json, bisect, calendar, datetime
from collections import Counter
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

def metrics(sel,label):
    if not sel:print(f"[{label}] 空集");return
    pn=[x[0] for x in sel];n_=len(pn)
    wins=[x for x in pn if x>0];losses=[x for x in pn if x<=0]
    gp=sum(wins);gl=-sum(losses)
    eq=1.0;pk=1.0;mdd=0.0
    for x in pn:eq*=(1+x/100);pk=max(pk,eq);mdd=max(mdd,pk-eq)
    pf=gp/gl if gl else float("inf")
    red=sum(1 for x in pn if x>TH)
    print(f"[{label:16s}] 笔{n_:4d} 复利{eq-1:7.1%} 胜率{len(wins)/n_*100:4.1f}% "
          f"PF={pf:4.2f} DD{mdd/pk*100:5.1f}% 红字{red:3d} 均盈{sum(pn)/n_:+.2f}%")

def decide_all(disable_slow):
    """跑一遍全量。disable_slow=True 时用 monkeypatch 关掉路径3，即旧V3。"""
    orig = signal_v3.long_slow_catch
    if disable_slow:
        signal_v3.long_slow_catch = lambda s, m: False
    try:
        out = {}
        for idx, r in enumerate(rows):
            i = base_sec.get(msec(r["时间"]))
            if i is None: continue
            sd = int(r["信号"])
            bf = signal_v3.base_features(c, atr, atr_pct, er20, st_dist, flips, ma30, i, sd)
            if bf is None: continue
            feats = dict(bf)
            feats.update({
                "slope_htf": _num(r.get("4h_MA30斜率")),
                "dist_base_ma": _num(r.get("1h_MA30距离")),
                "dist_htf_ma": _num(r.get("4h_MA30距离")),
                "adx_chg20": _num(r.get("前20根_ADX变化")),
                "er_chg20": _num(r.get("前20根_ER变化")),
                "vol100": _num(r.get("前100根_波动周期")),
            })
            v = signal_v3.v3_decide(sd, feats)
            out[idx] = (v["execute"], v["path"])
        return out
    finally:
        signal_v3.long_slow_catch = orig

old_dec = decide_all(disable_slow=True)    # 旧V3
new_dec = decide_all(disable_slow=False)   # 新V3(含路径3)

old=[];new=[];paths=Counter();slow=[];recovered=[]
for idx, r in enumerate(rows):
    if idx not in new_dec: continue
    p = pnl(r)
    exe_old, _ = old_dec[idx]
    exe_new, path_new = new_dec[idx]
    paths[path_new] += 1
    if exe_old: old.append((p, r))
    if exe_new:
        new.append((p, r))
        if path_new == signal_v3.PATH_SLOW:
            slow.append((p, r))
            if not exe_old and p > TH: recovered.append((p, r))

print("=== 路径分布(新V3, 含Fuse) ===")
for k,v_ in paths.most_common():print(f"  {k}: {v_}")
print("\n=== 旧V3 vs 新V3(带路径3) ===")
metrics(old,"旧V3")
metrics(new,"新V3+路径3")
print(f"\n路径3「慢热接住」新增放行 {len(slow)} 笔, 累计 {sum(p for p,_ in slow):.2f}%, "
      f"其中亏损 {sum(1 for p,_ in slow if p<0)} 笔")
print(f"其中挽回「原被V3漏掉的大赢家(>2%)」: {len(recovered)} 笔, 累计 {sum(p for p,_ in recovered):.2f}%")
tot_missed=sum(1 for idx,r in enumerate(rows) if idx in old_dec and not old_dec[idx][0] and pnl(r)>TH)
print(f"原漏掉大赢家共 {tot_missed} 个 → 挽回 {len(recovered)} 个 ({len(recovered)/tot_missed*100:.0f}%)")
