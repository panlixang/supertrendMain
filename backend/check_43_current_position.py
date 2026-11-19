# -*- coding: utf-8 -*-
"""检查43服务器当前持仓和最近订单"""
import urllib.request
import json
from datetime import datetime, timezone

BASE43 = "http://43.108.10.84:5174"

def get43(endpoint):
    try:
        resp = urllib.request.urlopen(BASE43 + endpoint, timeout=15)
        return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        return {"error": str(e)}

print("=" * 80)
print("检查43服务器AMD的所有状态")
print("=" * 80)

# 1. 当前持仓
print("\n1. 当前持仓状态")
print("-" * 80)
positions = get43("/api/trade/positions")
all_positions = positions.get("positions", {})
amd_pos = all_positions.get("AMD-USDT-SWAP")

if amd_pos:
    entry_time = datetime.fromtimestamp(amd_pos['entry_ts']/1000, tz=timezone.utc)
    print(f"\nAMD 当前持仓:")
    print(f"  方向: {amd_pos['side'].upper()}")
    print(f"  开仓时间: {entry_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  开仓价: {amd_pos['entry']:.2f}")
    print(f"  数量: {amd_pos['qty']}")
    print(f"  当前价: {amd_pos.get('price', 0):.2f}")
    print(f"  浮盈: {amd_pos.get('float_pnl', 0):.2f} USDT")

    # 检查事件记录
    print(f"\n  事件记录:")
    for event in amd_pos.get('events', []):
        evt_time = datetime.fromtimestamp(event['ts']/1000, tz=timezone.utc)
        print(f"    [{evt_time.strftime('%m-%d %H:%M')}] {event['kind']}: {event['msg']}")
else:
    print("\n当前无AMD持仓")

# 2. 查看所有持仓（包括其他品种）
print("\n2. 所有品种当前持仓")
print("-" * 80)
for symbol, pos in all_positions.items():
    entry_time = datetime.fromtimestamp(pos['entry_ts']/1000, tz=timezone.utc)
    print(f"{symbol:20s} {pos['side']:5s} @ {pos['entry']:8.2f} | "
          f"开仓: {entry_time.strftime('%m-%d %H:%M')}")

# 3. 查看已平仓列表（可能有未写入日志文件的）
print("\n3. 已平仓但未落盘的交易")
print("-" * 80)
closed = positions.get("closed", [])
if closed:
    print(f"找到 {len(closed)} 笔未落盘的已平仓交易:")
    for trade in closed:
        entry_time = datetime.fromtimestamp(trade['entry_ts']/1000, tz=timezone.utc)
        exit_time = datetime.fromtimestamp(trade.get('exit_ts', 0)/1000, tz=timezone.utc) if trade.get('exit_ts') else None
        print(f"\n{trade['symbol']}")
        print(f"  {trade['side']:5s} @ {trade['entry']:.2f}")
        print(f"  开仓: {entry_time.strftime('%Y-%m-%d %H:%M:%S')}")
        if exit_time:
            print(f"  平仓: {exit_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  盈亏: {trade.get('realized', 0):.2f} USDT")
else:
    print("无未落盘的已平仓交易")

print("\n" + "=" * 80)
print("分析")
print("=" * 80)

# 分析
if amd_pos:
    entry_ts = amd_pos['entry_ts']
    t_1012 = int(datetime(2026, 10, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    t_1013 = int(datetime(2026, 10, 13, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    t_1014 = int(datetime(2026, 10, 14, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

    if entry_ts < t_1012:
        print("\n此持仓开仓于10-12之前，可能就是日志中记录的那笔")
    elif t_1012 <= entry_ts < t_1013:
        print("\n此持仓开仓于10-12当天，是日志平仓后重新开的")
    elif t_1013 <= entry_ts < t_1014:
        print("\n此持仓开仓于10-13当天")
    elif entry_ts >= t_1014:
        print("\n此持仓开仓于10-14当天或之后")

    print(f"\n如果这笔持仓在10-14 12:00还存在，那空信号会因为【已有持仓】被拒绝！")
    print(f"这就解释了为什么12:00的空信号没有开仓。")
