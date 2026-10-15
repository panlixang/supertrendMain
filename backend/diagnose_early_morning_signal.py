# -*- coding: utf-8 -*-
"""诊断10-14凌晨的多单信号从何而来"""
import os, sys, json, urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "backtest"))
from engine import run_core, _extract, super_trend, st_signals

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
print("诊断10-14凌晨多单信号来源")
print("=" * 80)

# 获取43配置
symbols_data = get43("/api/trade/symbols")
amd_cfg = None
for s in symbols_data.get("symbols", []):
    if s.get("symbol") == "AMD-USDT-SWAP":
        amd_cfg = s
        break

# 拉取K线数据
CANDLES = {}
for tf, gran in [("1h", 3600), ("15m", 900), ("4h", 14400)]:
    CANDLES[tf] = pull_bitget("AMDUSDT", gran, limit=500)

klines = CANDLES["1h"]

# 用43配置运行回测
SC, PR, ER = _extract(amd_cfg)
result = run_core("AMD-USDT-SWAP", SC, PR, ER, tf="1h", candles=CANDLES)

# 找出10-13和10-14的所有交易
print("\n10-13到10-14的所有交易:")
print("-" * 80)

t_1013_start = int(datetime(2026, 10, 13, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
t_1015_start = int(datetime(2026, 10, 15, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

for t in result["trades"]:
    if t_1013_start <= t["entry_ts"] < t_1015_start:
        entry_time = datetime.fromtimestamp(t["entry_ts"]/1000, tz=timezone.utc)
        exit_time = datetime.fromtimestamp(t.get("exit_ts", 0)/1000, tz=timezone.utc) if t.get("exit_ts") else None

        print(f"\n{entry_time.strftime('%m-%d %H:%M')} {t['side'].upper():5s} @ {t['entry']:7.2f}", end="")
        if exit_time:
            print(f" -> {exit_time.strftime('%m-%d %H:%M')} @ {t.get('exit', 0):7.2f} "
                  f"| pnl={t.get('pnl', 0):6.2f} | {t.get('reason', '')}")
        else:
            print(" -> [持仓中]")

# 计算SuperTrend并找出所有翻转信号
print("\n" + "=" * 80)
print("10-13到10-14的所有ST翻转信号")
print("=" * 80)

from engine import Params
params = Params(**PR)

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

all_signals = st_signals(klines, st_full, "1h")

# 过滤10-13和10-14的信号
for sig in all_signals:
    if t_1013_start <= sig["ts"] < t_1015_start:
        sig_time = datetime.fromtimestamp(sig["ts"]/1000, tz=timezone.utc)

        marker = ""
        if abs(sig["price"] - 606.93) < 2:
            marker = " <<< 接近43实盘开仓价606.93"
        elif abs(sig["price"] - 591.97) < 2:
            marker = " <<< 接近本地回测开仓价591.97"

        print(f"{sig_time.strftime('%m-%d %H:%M')} {sig['type'].upper():4s} @ {sig['price']:7.2f}{marker}")

print("\n" + "=" * 80)
print("结论")
print("=" * 80)
print("\n从ST信号分析:")
print("1. 检查10-13晚上到10-14凌晨是否有BUY信号（做多）")
print("2. 检查该信号的价格是否在606.93附近")
print("3. 对比本地回测是否也产生了相同的信号")
print("4. 如果本地没有，说明43线上的信号判定逻辑与本地不一致")
