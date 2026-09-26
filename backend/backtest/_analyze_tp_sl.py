# -*- coding: utf-8 -*-
"""TP(赢) vs SL(亏) 特征差异 + 组合过滤规则测试。"""
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

tp=[r for r in rows if r["止盈止损"]=="TP"]
sl=[r for r in rows if r["止盈止损"]=="SL"]

def mean(g,c):
    v=[fnum(r[c]) for r in g if fnum(r[c]) is not None]
    return st.mean(v) if v else float('nan')

print(f"TP={len(tp)} SL={len(sl)}")
print(f"\n{'特征':16s} {'TP均值':>10s} {'SL均值':>10s} {'差(TP-SL)':>10s}")
for c in num_cols:
    d=mean(tp,c)-mean(sl,c)
    print(f"{c:16s} {mean(tp,c):10.4f} {mean(sl,c):10.4f} {d:10.4f}")

# 方向一致性：信号方向 vs 4h方向
print("\n--- 信号方向 vs 4h方向 一致性 ---")
def align_stats(g):
    same=sum(1 for r in g if int(r["信号"])*int(r["4h方向"])>0)
    return same, len(g)
for name,g in [("TP",tp),("SL",sl),("ALL",rows)]:
    s,n=align_stats(g)
    print(f"{name}: 同向={s}/{n} = {s/n*100:.1f}%")

# 组合规则测试：保留"真正趋势"信号
def evaluate(pred, label):
    keep=[r for r in rows if pred(r)]
    if not keep: 
        print(f"\n[{label}] 命中0"); return
    ktp=sum(1 for r in keep if r["止盈止损"]=="TP")
    ksl=sum(1 for r in keep if r["止盈止损"]=="SL")
    pnls=[fnum(r["盈亏"]) for r in keep]
    tot=sum(pnls)
    print(f"\n[{label}] 保留={len(keep)}/{len(rows)} 胜率={ktp/len(keep)*100:.1f}% "
          f"累计盈亏={tot:.2f}% (原全量=26.92%)")

# 规则1: 4h_MA30斜率>0 且 前20根_涨跌幅与信号同向
def pred1(r):
    s=int(r["信号"]); m4=fnum(r["4h_MA30斜率"]); mom=fnum(r["前20根_涨跌幅"])
    if m4 is None or mom is None: return False
    return m4>0 and ((s>0 and mom>0) or (s<0 and mom<0))
evaluate(pred1,"4h斜率>0 & 动量同向")

# 规则2: 4h_MA30斜率>0 且 前20根_ST距离变化 与信号同向(趋势带扩开)
def pred2(r):
    s=int(r["信号"]); m4=fnum(r["4h_MA30斜率"]); sd=fnum(r["前20根_ST距离变化"])
    if m4 is None or sd is None: return False
    return m4>0 and ((s>0 and sd<0) or (s<0 and sd>0))  # 价格远离带=趋势加强
evaluate(pred2,"4h斜率>0 & ST距离加强")

# 规则3: 高ER(前20根_ER变化正向) 且 ADX上升(前20根_ADX变化>0)
def pred3(r):
    er=fnum(r["前20根_ER变化"]); adx=fnum(r["前20根_ADX变化"])
    if er is None or adx is None: return False
    return er>0 and adx>0
evaluate(pred3,"ER变化>0 & ADX变化>0")

# 规则4: 综合 4h斜率>0 & 动量同向 & 翻转少(前50根_ST翻转次数<=3)
def pred4(r):
    s=int(r["信号"]); m4=fnum(r["4h_MA30斜率"]); mom=fnum(r["前20根_涨跌幅"]); fl=fnum(r["前50根_ST翻转次数"])
    if None in (m4,mom,fl): return False
    return m4>0 and ((s>0 and mom>0) or (s<0 and mom<0)) and fl<=3
evaluate(pred4,"4h斜率>0 & 动量同向 & 翻转<=3")

# 规则5: 排除 区间可交易=TRUE 中表现差的(趋势运行/延续) 仅保留恐慌+启动? 
def pred5(r):
    return r["趋势区间"] in ("震荡吸收期","趋势启动期","恐慌释放期")
evaluate(pred5,"保留 震荡/启动/恐慌")
