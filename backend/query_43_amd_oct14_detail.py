# -*- coding: utf-8 -*-
"""查询43服务器AMD 10-14完整交易记录"""
import urllib.request
import json
from datetime import datetime, timezone

BASE43 = "http://43.108.10.84:5174"

def get43(endpoint):
    resp = urllib.request.urlopen(BASE43 + endpoint, timeout=15)
    return json.loads(resp.read().decode('utf-8'))

print("=" * 80)
print("查询43服务器 - AMD 10月所有交易")
print("=" * 80)

# 获取交易历史，增加数量
history = get43("/api/trade/history?sym=AMD-USDT-SWAP&recent=100")

print(f"\nAMD总交易数: {history.get('total_trades', 0)}")
print(f"AMD总盈亏: {history.get('total_realized', 0):.4f} USDT\n")

# 筛选10月的所有交易
oct_trades = []
for trade in history.get("recent", []):
    entry_ts = trade.get("entry_ts", 0)
    entry_time = datetime.fromtimestamp(entry_ts/1000, tz=timezone.utc)

    if entry_time.month == 10 and entry_time.year == 2026:
        oct_trades.append(trade)

print(f"10月AMD交易: {len(oct_trades)} 笔\n")

for i, trade in enumerate(oct_trades, 1):
    entry_ts = trade.get("entry_ts", 0)
    exit_ts = trade.get("exit_ts")

    entry_time = datetime.fromtimestamp(entry_ts/1000, tz=timezone.utc)
    exit_time = datetime.fromtimestamp(exit_ts/1000, tz=timezone.utc) if exit_ts else None

    print(f"交易 #{i}")
    print(f"  方向: {trade.get('side', '').upper()}")
    print(f"  开仓: {entry_time.strftime('%Y-%m-%d %H:%M:%S')} UTC @ {trade.get('entry', 0):.2f}")
    if exit_time:
        print(f"  平仓: {exit_time.strftime('%Y-%m-%d %H:%M:%S')} UTC @ {trade.get('exit', 0):.2f}")
    print(f"  数量: {trade.get('qty', 0):.2f} AMD")
    print(f"  盈亏: {trade.get('realized', 0):.4f} USDT")
    print(f"  平仓原因: {trade.get('close_reason', 'N/A')}")
    print()

# 特别标注10-14的交易
print("=" * 80)
print("10-14当天的交易")
print("=" * 80)
t_1014_start = int(datetime(2026, 10, 14, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
t_1014_end = int(datetime(2026, 10, 15, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

trades_1014 = [t for t in oct_trades
               if t_1014_start <= t.get("entry_ts", 0) < t_1014_end
               or (t.get("exit_ts") and t_1014_start <= t.get("exit_ts") < t_1014_end)]

if trades_1014:
    for trade in trades_1014:
        entry_time = datetime.fromtimestamp(trade["entry_ts"]/1000, tz=timezone.utc)
        exit_time = datetime.fromtimestamp(trade["exit_ts"]/1000, tz=timezone.utc) if trade.get("exit_ts") else None

        print(f"\n方向: {trade['side'].upper()}")
        print(f"开仓: {entry_time.strftime('%Y-%m-%d %H:%M:%S')} UTC @ {trade['entry']:.2f}")
        if exit_time:
            print(f"平仓: {exit_time.strftime('%Y-%m-%d %H:%M:%S')} UTC @ {trade.get('exit', 0):.2f}")
        print(f"盈亏: {trade['realized']:.4f} USDT")
        print(f"原因: {trade.get('close_reason', '')}")

        print("\n>>> 对比本地engine:")
        print(f"    本地: 2026-10-14 12:00:00 UTC SHORT @ 591.97")
        print(f"    43实盘: {entry_time.strftime('%Y-%m-%d %H:%M:%S')} UTC {trade['side'].upper()} @ {trade['entry']:.2f}")
        print(f"    价格差异: {abs(trade['entry'] - 591.97):.2f} USDT")
        print(f"    方向{'一致' if trade['side'].lower() == 'short' else '不一致！'}")
else:
    print("\n43服务器没有10-14的交易记录")
    print("但用户反馈说14号有单子，可能是交易记录API返回不完整")
