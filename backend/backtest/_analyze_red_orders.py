# -*- coding: utf-8 -*-
"""分析 st_signals_1h_features.csv，量化 区间可交易(TRUE/FALSE) 两组的
表现与特征差异，找出"非趋势(红字)"单子的规律。"""
import csv, statistics as st

SRC = r"C:\Users\Administrator\AppData\Local\Temp\codebuddy-dropped-files\46a5ccbd-5091-496d-b717-8e8a681729ae\st_signals_1h_features.csv"

rows = list(csv.DictReader(open(SRC, encoding="utf-8-sig")))

def fnum(x):
    try: return float(x)
    except: return None

num_cols = ["1h_MA30斜率","4h_MA30斜率","1h_MA30距离","4h_MA30距离",
            "前20根_涨跌幅","前20根_ATR变化","前20根_ER变化","前20根_ADX变化","前20根_ST距离变化",
            "前50根_ST翻转次数","前50根_高低点次数","前50根_趋势持续时间",
            "前100根_趋势生命周期","前100根_横盘周期","前100根_波动周期"]

def stats(group):
    n=len(group)
    if n==0: return None
    tp=sum(1 for r in group if r["止盈止损"]=="TP")
    sl=sum(1 for r in group if r["止盈止损"]=="SL")
    pnls=[fnum(r["盈亏"]) for r in group if fnum(r["盈亏"]) is not None]
    avg=st.mean(pnls) if pnls else 0
    tot=sum(pnls)
    win=sum(1 for p in pnls if p>0)
    return dict(n=n, tp=tp, sl=sl, winrate=tp/n*100, avg=avg, total=tot,
                pos_winrate=win/n*100 if n else 0)

print("="*70)
print(f"总单子数: {len(rows)}")
overall=stats(rows)
print(f"整体: TP={overall['tp']} SL={overall['sl']} | 胜率(TP%)={overall['winrate']:.1f}% | "
      f"平均盈亏={overall['avg']:.3f}% | 累计盈亏={overall['total']:.2f}%")

print("\n--- 按 区间可交易 分组 ---")
for flag in ["TRUE","FALSE"]:
    g=[r for r in rows if r["区间可交易"]==flag]
    s=stats(g)
    print(f"\n[区间可交易={flag}] n={s['n']}  TP={s['tp']} SL={s['sl']} "
          f"胜率={s['winrate']:.1f}% 均盈亏={s['avg']:.3f}% 累计={s['total']:.2f}%")

print("\n--- 按 趋势区间 分组 ---")
from collections import OrderedDict
reg=OrderedDict()
for r in rows:
    reg.setdefault(r["趋势区间"],[]).append(r)
for k,g in reg.items():
    s=stats(g)
    print(f"{k:8s} n={s['n']:3d} 胜率={s['winrate']:5.1f}% 均盈亏={s['avg']:7.3f}% 累计={s['total']:8.2f}%")

def feat_means(group):
    out={}
    for c in num_cols:
        vals=[fnum(r[c]) for r in group if fnum(r[c]) is not None]
        out[c]=st.mean(vals) if vals else float('nan')
    return out

print("\n--- 特征均值: 非趋势(FALSE) vs 趋势(TRUE) ---")
gf=[r for r in rows if r["区间可交易"]=="FALSE"]
gt=[r for r in rows if r["区间可交易"]=="TRUE"]
mf=feat_means(gf); mt=feat_means(gt)
print(f"{'特征':16s} {'FALSE均值':>12s} {'TRUE均值':>12s} {'差异':>10s}")
for c in num_cols:
    d=mf[c]-mt[c]
    print(f"{c:16s} {mf[c]:12.4f} {mt[c]:12.4f} {d:10.4f}")

# 单因子扫描：找能分离 FALSE 的简单阈值
print("\n--- 单因子阈值扫描 (目标: 覆盖尽量多的 FALSE 且尽量少误伤 TRUE) ---")
def scan(col, op, thresholds):
    best=None
    for t in thresholds:
        if op=="ge": pred=lambda v: v>=t
        else: pred=lambda v: v<=t
        tp_pred=[r for r in rows if pred(fnum(r[col]) or 0)]
        # 命中FALSE的比例(覆盖率) 与 命中的胜率
        hit_f=sum(1 for r in tp_pred if r["区间可交易"]=="FALSE")
        hit_t=sum(1 for r in tp_pred if r["区间可交易"]=="TRUE")
        cov=hit_f/len(gf)*100 if gf else 0
        if hit_f+hit_t>0:
            keep_win=sum(1 for r in tp_pred if r["止盈止损"]=="TP")/(hit_f+hit_t)*100
        else: keep_win=0
        print(f"  {col} {op}{t}: 命中={hit_f+hit_t} 覆盖FALSE={cov:.0f}% 误伤TRUE={hit_t} 命中胜率={keep_win:.1f}%")
scan("前50根_ST翻转次数","ge",[3,4,5,6])
scan("4h_MA30斜率","le",[0,1,2])
scan("前20根_涨跌幅","le",[0,1,2])
scan("前100根_横盘周期","ge",[20,30,40])
scan("前20根_ADX变化","le",[0,2])
