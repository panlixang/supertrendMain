# -*- coding: utf-8 -*-
"""检查10-13 20:00的BUY信号评分"""
import os, sys, json, urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "backtest"))
from engine import (run_core, _extract, super_trend, st_signals,
                    enhanced_signal_handler, _as_p, SymbolTradeConfig, Params, strategy)

BASE43 = "http://43.108.10.84:5174"

def get43(endpoint):
    resp = urllib.request.urlopen(BASE43 + endpoint, timeout=15)
    return json.loads(resp.read().decode('utf-8'))

def pull_bitget(symbol, gran, limit=500):
    url = (f"https://api.bitget.com/api/v2/mix/market/candles"
           f"?symbol={symbol}&productType=usdt-futures&granularity={gran}&limit={limit}")
    rows = json.loads(urllib.request.urlopen(url, timeout=20).read()).get("data", [])
    return [{"ts": int(x[0]), "o": float(x[1]), "h": float(x[2]),
             "l": float(x[3]), "c": float(x[4]), "vol": float(x[5])} for x in rows]

print("=" * 80)
print("检查10-13 20:00 BUY信号为何开仓")
print("=" * 80)

# 1. 获取43配置
symbols_data = get43("/api/trade/symbols")
amd_cfg = None
for s in symbols_data.get("symbols", []):
    if s.get("symbol") == "AMD-USDT-SWAP":
        amd_cfg = s
        break

SC, PR, ER = _extract(amd_cfg)
cfg = SymbolTradeConfig(**SC)
params = Params(**PR)

# 2. 拉取K线
CANDLES = {}
for tf, gran in [("1h", 3600), ("15m", 900), ("4h", 14400)]:
    CANDLES[tf] = pull_bitget("AMDUSDT", gran, limit=500)

klines = CANDLES["1h"]

# 3. 找到10-13 20:00的信号
t_target = int(datetime(2026, 10, 13, 20, 0, tzinfo=timezone.utc).timestamp() * 1000)

st_full = super_trend(
    [c["o"] for c in klines],
    [c["h"] for c in klines],
    [c["l"] for c in klines],
    [c["c"] for c in klines],
    periods=params.periods,
    multiplier=params.multiplier,
    src=params.src,
    change_atr=params.change_atr
)

all_sigs = st_signals(klines, st_full, "1h")

target_sig = None
for s in all_sigs:
    if s["ts"] == t_target:
        target_sig = s
        break

if not target_sig:
    print("\n未找到10-13 20:00的BUY信号")
    print("\n10-13 16:00到10-14 12:00之间的所有信号:")
    t_start = int(datetime(2026, 10, 13, 16, 0, tzinfo=timezone.utc).timestamp() * 1000)
    t_end = int(datetime(2026, 10, 14, 13, 0, tzinfo=timezone.utc).timestamp() * 1000)
    for s in all_sigs:
        if t_start <= s["ts"] < t_end:
            dt = datetime.fromtimestamp(s["ts"]/1000, tz=timezone.utc)
            print(f"  {dt.strftime('%m-%d %H:%M')} {s['type'].upper()} @ {s['price']:.2f}")
    sys.exit(0)

print(f"\n10-13 20:00 BUY信号原始数据:")
print(f"  type: {target_sig['type']}")
print(f"  price: {target_sig['price']:.2f}")

# 4. 模拟gate判定
window = [c for c in klines if c["ts"] <= t_target]
full = strategy.evaluate(CANDLES, vars(params), target_sig)

print(f"\nStrategy评估结果:")
print(f"  score: {full.get('score')}")
print(f"  grade: {full.get('grade')}")
print(f"  regime: {full.get('regime', {}).get('regime')}")
print(f"  er: {full.get('regime', {}).get('er')}")

gate_result = enhanced_signal_handler(full, window, cfg, candles_by_tf=CANDLES, p=_as_p(params))

print(f"\nGate判定结果:")
print(f"  trade: {gate_result.get('trade')}")
print(f"  verdict: {gate_result.get('verdict')}")
print(f"  hidden: {gate_result.get('hidden')}")
print(f"  gate_reasons: {gate_result.get('gate_reasons')}")

print("\n" + "=" * 80)
print("结论")
print("=" * 80)

if gate_result.get('trade'):
    print(f"\n此信号通过了过滤，应该开仓 ✓")
    print(f"  评分: {full.get('score')}")
    print(f"  等级: {full.get('grade')}")
    print(f"  对比日志: grade=B，与本地计算一致")
else:
    print(f"\n此信号被拒绝 ✗")
    print(f"  拒绝原因: {gate_result.get('gate_reasons', ['未知'])}")
    print(f"\n  但43实盘却开了仓，说明：")
    print(f"    1. 43线上的过滤逻辑与本地不一致")
    print(f"    2. 或者43上这个信号的评分与本地不同")
