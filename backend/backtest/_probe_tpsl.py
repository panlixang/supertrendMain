# -*- coding: utf-8 -*-
"""从 master 的 entry/exit/pnl 反推真实 TP/SL 固定水平。"""
import sys, os, csv, json, bisect, calendar, datetime
BASE = r"d:\个人项目代码\supertrendMain\backend"
sys.path.insert(0, BASE)
from indicators import super_trend
OUT_JSON = os.path.join(BASE, "backtest", "btc_1h_full.json")
MASTER = os.path.join(BASE, "backtest", "st_signals_1h.csv")
d = json.load(open(OUT_JSON, encoding="utf-8")); base = d["base"]
c = [b["c"] for b in base]; h = [b["h"] for b in base]; l = [b["l"] for b in base]; ts = [b["ts"] for b in base]
st_res = super_trend([b["o"] for b in base], h, l, c, periods=10, multiplier=3.0, change_atr=True)
flips = sorted((f["i"], f["type"]) for f in st_res["flips"])
base_sec = {int(b["ts"]/1000): i for i, b in enumerate(base)}
def msec(s): return calendar.timegm(datetime.datetime.strptime(s, "%Y/%m/%d %H:%M").timetuple())

rows = list(csv.DictReader(open(MASTER, encoding="utf-8-sig")))
tp_impl=[]; sl_impl=[]
for r in rows:
    i = base_sec.get(msec(r["time"]))
    if i is None: continue
    entry = float(r["close"]); pnl = float(r["pnl_pct"]); res = r["exit_result"]; s = int(r["signal"])
    long = s == 1
    if res == "TP":
        # exit = entry*(1+pnl/100) for TP (pnl>0)
        exit_px = entry*(1+pnl/100) if long else entry*(1-pnl/100)
        tp = (exit_px/entry - 1)*100 if long else (1-exit_px/entry)*100
        tp_impl.append(tp)
    else:  # SL (pnl<0)
        exit_px = entry*(1+pnl/100) if long else entry*(1-pnl/100)
        sl = (1-exit_px/entry)*100 if long else (exit_px/entry-1)*100
        sl_impl.append(sl)
def desc(a, name):
    a=sorted(a); 
    import statistics as st
    print(f"{name}: n={len(a)} min={a[0]:.3f} p25={a[len(a)//4]:.3f} 中位={a[len(a)//2]:.3f} p75={a[3*len(a)//4]:.3f} max={a[-1]:.3f} 均值={sum(a)/len(a):.3f}")
    # 看是否聚集: 统计靠近某值的占比
desc(tp_impl, "TP隐含%")
desc(sl_impl, "SL隐含%")
# 离散度: 标准差
import statistics as st2
print(f"TP std={st2.pstdev(tp_impl):.3f}  SL std={st2.pstdev(sl_impl):.3f}")
# 把 TP/SL 按 0.5 分桶
from collections import Counter
print("TP桶:", dict(sorted(Counter(round(x*2)/2 for x in tp_impl).items())))
print("SL桶:", dict(sorted(Counter(round(x*2)/2 for x in sl_impl).items())))
