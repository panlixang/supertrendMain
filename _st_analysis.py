import pandas as pd, numpy as np, sys
sys.path.insert(0, 'backend')
from indicators import super_trend

def load(fn):
    df = pd.read_csv(fn)
    df['t'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
    return df.set_index('t').sort_index()

d4 = load('btc_4h_2019_2026.csv')
d1 = load('btc_1d_2019_2026.csv')
o4,h4,l4,c4 = d4.open.tolist(),d4.high.tolist(),d4.low.tolist(),d4.close.tolist()
o1,h1,l1,c1 = d1.open.tolist(),d1.high.tolist(),d1.low.tolist(),d1.close.tolist()
t4, t1 = d4.index, d1.index
N = len(c4)
HOLDS = [1,4,12,24,48,96]

def stats_for(p, m):
    st = super_trend(o4,h4,l4,c4, periods=p, multiplier=m, change_atr=True)
    flips = st['flips']; trend = st['trend']
    res = {}
    for side,typ in [('long','buy'),('short','sell')]:
        fidx=[f['i'] for f in flips if f['type']==typ]
        for H in HOLDS:
            fut=[]
            for i in fidx:
                if i+H < N:
                    fut.append((c4[i+H]/c4[i]-1) if side=='long' else (c4[i]/c4[i+H]-1))
            fut=np.array(fut)
            res[(side,H)]=(fut.mean()*100,(fut>0).mean()*100,len(fut))
    fi=np.array([f['i'] for f in flips]); gaps=np.diff(fi)
    whip=(gaps<=8).mean()*100 if len(gaps) else 0
    return st,flips,res,gaps.mean(),whip,len(flips)

P,M=10,3.0
st,flips,res,avglen,whip,nflip=stats_for(P,M)
print(f"=== Supertrend 4h signal behavior (period={P}, mult={M}, 2019-2026) ===")
print(f"total flips={nflip}  avg trend length={avglen:.1f} bars4h (= {avglen*4/24:.1f} d)")
print(f"whipsaw rate (reverse within 8 bars4h ~1.3d): {whip:.1f}%")
print("\nLONG(buy) flip -> forward return:")
for H in HOLDS:
    mu,wr,nn=res[('long',H)]
    print(f"  +{H:3d}bars({(H*4/24):4.1f}d):  win {wr:4.1f}%  mean {mu:+6.2f}%  n={nn}")
print("\nSHORT(sell) flip -> forward return (short pnl):")
for H in HOLDS:
    mu,wr,nn=res[('short',H)]
    print(f"  +{H:3d}bars({(H*4/24):4.1f}d):  win {wr:4.1f}%  mean {mu:+6.2f}%  n={nn}")

print("\n=== parameter sensitivity (hold 24 bars4h~4d) ===")
print(f"{'p,m':>8} {'flips':>6} {'buyWin':>7} {'buyMean':>8} {'sellWin':>8} {'sellMean':>9}")
for (pp,mm) in [(10,3.0),(15,9.1),(10,2.0),(14,3.0),(7,4.0)]:
    st2,fl2,res2,al2,w2,nf2=stats_for(pp,mm)
    bl,wr_b,_=res2[('long',24)]; sh,wr_s,_=res2[('short',24)]
    print(f"{pp},{mm:>4}: {nf2:6d} {wr_b:6.1f}% {bl:+7.2f}% {wr_s:7.1f}% {sh:+8.2f}%")

# multi-timeframe: 1d trend filters 4h buy flips
st1=super_trend(o1,h1,l1,c1, periods=10, multiplier=3.0, change_atr=True)
trend1=st1['trend']
def td_at(ts):
    idx=t1.searchsorted(ts, side='right')-1
    return trend1[idx] if idx>=0 else None
al=[]; co=[]
for f in flips:
    if f['type']!='buy' or f['i']+24>=N: continue
    ht=td_at(t4[f['i']]); ret=c4[f['i']+24]/c4[f['i']]-1
    (al if ht==1 else co if ht==-1 else []).append(ret)
al=np.array(al); co=np.array(co)
print("\n=== MTF: 1d trend filter on 4h buy flips (hold 24 bars4h) ===")
print(f"1d ALIGNED (bull):  win {(al>0).mean()*100:4.1f}%  mean {al.mean()*100:+6.2f}%  n={len(al)}")
print(f"1d CONTRA  (bear):  win {(co>0).mean()*100:4.1f}%  mean {co.mean()*100:+6.2f}%  n={len(co)}")
