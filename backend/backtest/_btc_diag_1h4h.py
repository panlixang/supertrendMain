"""诊断：同一批 block_4h 放行的信号里，
  - 4h 形态 dir==0 / None 的比例（即 bt_1htrend.py 误称为"1h无趋势"的旧滤镜实际触发的比例）
  - 1h 形态 dir==0 / None 的比例（本对话真正要加的 1h 无趋势滤镜）
解释两版结论为何相反。
"""
from __future__ import annotations
import bisect, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from indicators import super_trend
from pattern_recog import recognize as recognize_pattern
from _live_cfg_backtest import fetch_candles

SYM="BTC-USDT-SWAP"
candles = fetch_candles(SYM, "1h", 15600)
c4 = fetch_candles(SYM, "4h", 4200)
st = super_trend([c["o"] for c in candles],[c["h"] for c in candles],
                 [c["l"] for c in candles],[c["c"] for c in candles],
                 periods=10, multiplier=3.0, change_atr=True)
flips = st["flips"] or []

# 4h dir
pat4 = recognize_pattern([{"ts":c["ts"],"o":c["o"],"h":c["h"],"l":c["l"],"c":c["c"]} for c in c4])["pattern"]
p4 = {p["ts"]: p.get("dir") for p in pat4}
pts4 = [p["ts"] for p in pat4]
def d4(ts):
    i=bisect.bisect_right(pts4,ts)-1
    return p4[pts4[i]] if i>=0 else None

# 1h dir
pat1 = recognize_pattern([{"ts":c["ts"],"o":c["o"],"h":c["h"],"l":c["l"],"c":c["c"]} for c in candles])["pattern"]
p1 = {p["ts"]: p.get("dir") for p in pat1}

# block_4h 放行集合（与 _btc_pattern_tp_opt 同语义：仅拦明确反向）
b4h_block=0
n4h0=0   # 4h dir==0/None 的信号数
n1h0=0   # 1h dir==0/None 的信号数
total=0
for f in flips:
    i=f["i"]
    if i>=len(candles): continue
    ts=candles[i]["ts"]; sig=1 if f["type"]=="buy" else -1
    d=d4(ts)
    allowed=(d is None) or (d==0) or (d==sig)
    total+=1
    if not allowed: b4h_block+=1; continue
    if not (isinstance(d,int) and d!=0): n4h0+=1
    dd=p1.get(ts)
    if not (isinstance(dd,int) and dd!=0): n1h0+=1

print(f"信号总数={total}  block_4h拦截={b4h_block}  放行={total-b4h_block}")
print(f"放行信号中: 4h dir==0/None 的有 {n4h0}  ({n4h0/(total-b4h_block)*100:.1f}%)  ← 旧'无趋势'滤镜触发量")
print(f"放行信号中: 1h dir==0/None 的有 {n1h0}  ({n1h0/(total-b4h_block)*100:.1f}%)  ← 真正的1h无趋势滤镜触发量")
