import pandas as pd, numpy as np
from datetime import datetime, timezone

def load(fn):
    df = pd.read_csv(fn)
    df['t'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
    return df.set_index('t').sort_index()

d1 = load('btc_1d_2019_2026.csv')
d4 = load('btc_4h_2019_2026.csv')
d1['ret'] = d1['close'].pct_change()
d4['ret'] = d4['close'].pct_change()
print(f"1d 范围 {d1.index[0].date()} -> {d1.index[-1].date()}  4h 范围 {d4.index[0].date()} -> {d4.index[-1].date()}")

print("\n### 分年表现 (日线)")
yr = d1['close'].resample('YS').first()
ye = d1['close'].resample('YE').last()
years = sorted(set(d1.index.year))
print(f"{'年':>5} {'年涨跌':>9} {'年化波动':>9} {'年内最大回撤':>12}")
for y in years:
    sub = d1[d1.index.year == y]
    chg = sub['close'].iloc[-1]/sub['close'].iloc[0]-1
    vol = sub['ret'].std()*np.sqrt(365)
    peak = sub['close'].cummax(); mdd = (sub['close']/peak-1).min()
    print(f"{y:>5} {chg*100:+8.1f}% {vol*100:8.0f}% {mdd*100:11.1f}%")

print("\n### 月度季节性 (跨7年聚合, 按月分组)")
me = d1['close'].resample('ME').last()
mchg = me.pct_change().dropna()           # 月对月收盘变动
mtab = mchg.groupby(mchg.index.month).agg(['mean','median'])
win = mchg.groupby(mchg.index.month).apply(lambda x:(x>0).mean())
print(f"{'月':>3} {'平均涨跌':>9} {'中位涨跌':>9} {'上涨月份占比':>12}")
for mo in range(1,13):
    print(f"{mo:>3} {mtab.loc[mo,'mean']*100:+8.1f}% {mtab.loc[mo,'median']*100:+8.1f}% {win.loc[mo]*100:11.0f}%")

print("\n### 减半周期")
halvings = [datetime(2020,5,11,tzinfo=timezone.utc), datetime(2024,4,20,tzinfo=timezone.utc)]
for h in halvings:
    after = d1[d1.index > h].head(365)
    if len(after)>1:
        ret = after['close'].iloc[-1]/after['close'].iloc[0]-1
        peak_ret = after['close'].max()/after['close'].iloc[0]-1
        print(f"减半 {h.date()}: 后365天收盘 {ret*100:+.1f}%  期间最高 {peak_ret*100:+.1f}%")

print("\n### 年化波动率逐年趋势 (成熟度)")
for y in years:
    sub = d1[d1.index.year==y]
    print(f"  {y}: {sub['ret'].std()*np.sqrt(365)*100:5.0f}%")

print("\n### 周内效应 (跨7年聚合, 日线)")
d1['dow']=d1.index.dayofweek
dw = d1.groupby('dow')['ret'].agg(['mean','std'])
win_d = d1.groupby('dow').apply(lambda x:(x['ret']>0).mean())
print(f"{'周':>3} {'均值bp':>8} {'波动%':>7} {'胜率%':>7}")
for i in range(7):
    print(f"{i:>3} {dw.loc[i,'mean']*1e4:+7.1f} {dw.loc[i,'std']*100:6.2f} {win_d.loc[i]*100:6.1f}")

print("\n### 4h 波动聚集 / 趋势 (长期)")
ar = d4['ret'].abs()
print(" |4h收益|滞后自相关:", {lag: round(ar.autocorr(lag),3) for lag in [1,2,6,12,24]})
sign=np.sign(d4['ret'].fillna(0)); runs=[]; c=1
for k in range(1,len(sign)):
    if sign.iloc[k]==sign.iloc[k-1] and sign.iloc[k]!=0: c+=1
    else:
        if sign.iloc[k-1]!=0: runs.append(c*sign.iloc[k-1]); c=1
pos=[abs(r) for r in runs if r>0]; neg=[abs(r) for r in runs if r<0]
print(f"4h 上涨连段均长 {np.mean(pos):.2f}根(={np.mean(pos)*4:.0f}h) 下跌连段均长 {np.mean(neg):.2f}根  (对称=无明显方向惯性)")
ema20=d4['close'].ewm(span=20).mean(); ema50=d4['close'].ewm(span=50).mean()
bull=d4['ret'][ema20>ema50]; bear=d4['ret'][ema20<ema50]
print(f"4h EMA20>50 后续均值 {bull.mean()*1e4:+.1f}bp  空头 {bear.mean()*1e4:+.1f}bp  排列占比 {(ema20>ema50).mean()*100:.0f}%")
