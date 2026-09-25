# -*- coding: utf-8 -*-
"""2026 BTC 1h 全 ST + v4-exit 逐笔明细 + 100U×10x 净额。"""
import sys, math, bisect, csv, datetime as dt
sys.path.insert(0, ".")
import sqlite3
from indicators import super_trend

DB = r"d:\个人项目代码\supertrendMain\backend\candle_data.db"
ATR_LEN, MULT, H = 10, 3, 300
UTC = dt.timezone.utc
Y0 = int(dt.datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
Y1 = int(dt.datetime(2027, 1, 1, tzinfo=UTC).timestamp() * 1000)
NOMINAL_LEV = 10.0
FEE_PCT = 0.1          # 往返 0.1%
START_U = 100.0

con = sqlite3.connect(DB)
rows = con.execute(
    "SELECT ts,o,h,l,c,vol FROM candles WHERE symbol='BTC-USDT' AND tf='1h' AND ts>=? AND ts<? ORDER BY ts",
    (Y0 - 7200 * 3600_000, Y1)).fetchall()
con.close()
bc = [{"ts": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "vol": r[5]} for r in rows]
bc = [c for c in bc if Y0 <= c["ts"] < Y1][:-1]

O=[c["o"] for c in bc]; Hh=[c["h"] for c in bc]; L=[c["l"] for c in bc]; C=[c["c"] for c in bc]; nB=len(bc)
st = super_trend(O,Hh,L,C, periods=ATR_LEN, multiplier=MULT, change_atr=True)
atr=st["atr"]; flips=st["flips"]; fidx=[f["i"] for f in flips]

def tm(ts):
    return dt.datetime.fromtimestamp(ts/1000, UTC).strftime("%m-%d %H:%M")

def v4(i, sd, sl, tp):
    """返回 (exit_type, exit_price, pnl_pct, exit_i)"""
    entry=C[i]; p=bisect.bisect_right(fidx,i); nf=flips[p]["i"] if p<len(flips) else i+H
    rc=lambda: C[nf] if nf<len(C) else C[-1]; end=min(i+H+1,len(C))
    for j in range(i+1,end):
        if sd==1:
            if L[j]<=sl: return ("sl", sl, (sl-entry)/entry*100, j)
            if Hh[j]>=tp: return ("tp", tp, 0.5*(tp-entry)/entry*100+0.5*(rc()-entry)/entry*100, j)
            if j==nf: return ("st", C[min(nf,len(C)-1)], (C[min(nf,len(C)-1)]-entry)/entry*100, j)
        else:
            if Hh[j]>=sl: return ("sl", sl, (entry-sl)/entry*100, j)   # 空单撞止损=亏损
            if L[j]<=tp: return ("tp", tp, 0.5*(entry-tp)/entry*100+0.5*(entry-rc())/entry*100, j)
            if j==nf: return ("st", C[min(nf,len(C)-1)], (entry-C[min(nf,len(C)-1)])/entry*100, j)
    last=min(i+H,len(C)-1)
    return ("st", C[last], (C[last]-entry)/entry*100 if sd==1 else (entry-C[last])/entry*100, last)

LABEL={"sl":"止损","tp":"止盈半仓","st":"ST翻转"}
records=[]; bal=START_U
for n_, f in enumerate(flips, 1):
    i=f["i"]
    if i>=nB: continue
    sd=1 if f["type"]=="buy" else -1
    a=atr[i]
    if a is None or (isinstance(a,float) and math.isnan(a)): continue
    entry=C[i]
    sl=entry-1.5*a if sd==1 else entry+1.5*a
    tp=entry+2.0*a if sd==1 else entry-2.0*a
    et, ep, pnlpct, ei = v4(i, sd, sl, tp)
    # 100U×10x：每笔净 U = (pnl% - 0.1) × 10
    u=(pnlpct-FEE_PCT)*NOMINAL_LEV
    bal+=u
    records.append({
        "#":len(records)+1, "入场时间":tm(bc[i]["ts"]), "方向":"多" if sd==1 else "空",
        "入场价":round(entry,2), "止损SL":round(sl,2), "止盈TP":round(tp,2),
        "出场时间":tm(bc[ei]["ts"]), "出场类型":LABEL[et], "出场价":round(ep,2),
        "盈亏%":round(pnlpct,3), "净U":round(u,2), "余额U":round(bal,2),
    })

out=r"d:\个人项目代码\supertrendMain\backend\backtest\btc_2026_trades.csv"
with open(out,"w",newline="",encoding="utf-8-sig") as fp:
    w=csv.DictWriter(fp, fieldnames=list(records[0].keys())); w.writeheader(); w.writerows(records)

print(f"2026 BTC 1h 全ST+v4-exit | 共 {len(records)} 笔 | 窗口 {tm(bc[0]['ts'])} ~ {tm(bc[-1]['ts'])}")
print("口径：100U本金 × 10x（名义1000U），每笔净 U = (盈亏% - 0.1%往返费) × 10，逐笔累加\n")
hdr=f"{'#':>3} {'入场':>12} {'方向':>3} {'入场价':>10} {'止损SL':>10} {'止盈TP':>10} {'出场':>12} {'类型':>8} {'出场价':>10} {'盈亏%':>8} {'净U':>8} {'余额U':>9}"
print(hdr); print("-"*len(hdr))
for r in records:
    print(f"{r['#']:>3} {r['入场时间']:>12} {r['方向']:>2} {r['入场价']:>10.2f} {r['止损SL']:>10.2f} {r['止盈TP']:>10.2f} "
          f"{r['出场时间']:>12} {r['出场类型']:>7} {r['出场价']:>10.2f} {r['盈亏%']:>+8.3f} {r['净U']:>+8.2f} {r['余额U']:>9.2f}")

pn=[float(r["盈亏%"]) for r in records]
mu=sum((x-FEE_PCT)*NOMINAL_LEV for x in pn)
w=[x for x in pn if x>0]; l=[-x for x in pn if x<=0]
mean=sum(pn)/len(pn); aw=sum(w)/len(w); al=sum(l)/len(l)
var=sum((x-mean)**2 for x in pn)/(len(pn)-1); t=mean/math.sqrt(var/len(pn))
eq=1.0
for x in pn: eq*=(1+x/100)
print("-"*len(hdr))
print(f"汇总: {len(pn)}笔 | 胜率 {len(w)/len(pn)*100:.1f}% | 均盈 {mean:+.3f}% | 盈亏比 {aw/abs(al):.2f} | "
      f"PF {sum(w)/sum(l):.2f} | t {t:+.2f} | 满仓复利(1x毛) {(eq-1)*100:+.1f}% | 期末余额 {START_U+mu:.1f}U (净 {mu:+.1f}U)")
print(f"出场分布: 止损 {sum(1 for r in records if r['出场类型']=='止损')} / "
      f"止盈半仓 {sum(1 for r in records if r['出场类型']=='止盈半仓')} / "
      f"ST翻转 {sum(1 for r in records if r['出场类型']=='ST翻转')}")
print(f"\nCSV 已导出: {out}")
