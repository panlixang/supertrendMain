# -*- coding: utf-8 -*-
"""检查10-13 16:00的BUY信号为何被过滤"""
import os, sys, json, urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "backtest"))
from engine import (run_core, _extract, super_trend, st_signals,
                    enhanced_signal_handler, _as_p, SymbolTradeConfig, Params, EnhancedExitRules)

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
print("检查10-13 16:00 BUY信号为何被过滤")
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

print(f"\n关键配置:")
print(f"  use_scoring: {cfg.use_scoring}")
print(f"  scoring_full_threshold: {cfg.scoring_full_threshold}")
print(f"  er_min: {cfg.er_min}")
print(f"  er_trend: {cfg.er_trend}")
print(f"  cooldown_sec: {cfg.cooldown_sec}")

# 2. 拉取K线
CANDLES = {}
for tf, gran in [("1h", 3600), ("15m", 900), ("4h", 14400)]:
    CANDLES[tf] = pull_bitget("AMDUSDT", gran, limit=500)

klines = CANDLES["1h"]

# 3. 找到10-13 16:00的信号
t_target = int(datetime(2026, 10, 13, 16, 0, tzinfo=timezone.utc).timestamp() * 1000)

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
    print("\n未找到10-13 16:00的信号")
    sys.exit(1)

print(f"\n10-13 16:00 BUY信号原始数据:")
print(f"  type: {target_sig['type']}")
print(f"  price: {target_sig['price']:.2f}")
print(f"  score: {target_sig.get('score', 'N/A')}")
print(f"  grade: {target_sig.get('grade', 'N/A')}")

# 4. 模拟gate判定
print(f"\n模拟gate判定过程:")

# 构造window（截止到信号时刻的K线）
window = [c for c in klines if c["ts"] <= t_target]

# 调用strategy.evaluate
from engine import strategy
full = strategy.evaluate(CANDLES, vars(params), target_sig)

print(f"\nStrategy评估结果:")
print(f"  score: {full.get('score')}")
print(f"  grade: {full.get('grade')}")
print(f"  regime: {full.get('regime', {}).get('regime')}")
print(f"  er: {full.get('regime', {}).get('er')}")

# 调用enhanced_signal_handler
gate_result = enhanced_signal_handler(full, window, cfg, candles_by_tf=CANDLES, p=_as_p(params))

print(f"\nGate判定结果:")
print(f"  trade: {gate_result.get('trade')}")
print(f"  verdict: {gate_result.get('verdict')}")
print(f"  hidden: {gate_result.get('hidden')}")
print(f"  gate_reasons: {gate_result.get('gate_reasons')}")

print("\n" + "=" * 80)
print("分析")
print("=" * 80)

if not gate_result.get('trade'):
    print(f"\n此信号被拒绝！")
    print(f"拒绝原因: {gate_result.get('gate_reasons', ['未知'])}")
    print(f"\n可能的原因:")

    if gate_result.get('gate_reasons'):
        for reason in gate_result['gate_reasons']:
            print(f"  - {reason}")

    print(f"\n如果43实盘开了仓，说明:")
    print(f"  1. 43线上的过滤逻辑与本地engine不一致")
    print(f"  2. 或者43上这个信号的评分/ER值与本地计算不同")
    print(f"  3. 或者43配置与API返回的配置不一致")
else:
    print(f"\n此信号应该开仓！")
    print(f"信号价格: {target_sig['price']:.2f}")
    print(f"43实盘开仓价: 606.93")
    print(f"差异: {abs(target_sig['price'] - 606.93):.2f} USDT")
