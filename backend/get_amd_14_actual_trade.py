# -*- coding: utf-8 -*-
"""获取43服务器AMD 10-14的实际交易记录"""
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
print("获取43服务器AMD所有交易记录（包括已平仓和当前持仓）")
print("=" * 80)

# 1. 获取历史交易
history = get43("/api/trade/history?sym=AMD-USDT-SWAP&recent=50")

print("\nAMD 历史交易（最近50笔）:")
if "recent" in history:
    print(f"总交易数: {history.get('total_trades', 0)}")
    print(f"总盈亏: {history.get('total_realized', 0):.2f} USDT\n")

    # 过滤出10-14相关的交易
    t_1014_start = int(datetime(2026, 10, 14, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    t_1014_end = int(datetime(2026, 10, 15, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

    trades_1014 = []
    all_trades = []

    for i, trade in enumerate(history["recent"], 1):
        entry_ts = trade.get("entry_ts", 0)
        exit_ts = trade.get("exit_ts", 0)

        entry_time = datetime.fromtimestamp(entry_ts/1000, tz=timezone.utc)
        exit_time = datetime.fromtimestamp(exit_ts/1000, tz=timezone.utc) if exit_ts else None

        trade_info = {
            "index": i,
            "entry_time": entry_time,
            "exit_time": exit_time,
            "side": trade.get("side"),
            "entry_price": trade.get("entry"),
            "exit_price": trade.get("exit"),
            "pnl": trade.get("realized", 0),
            "close_reason": trade.get("close_reason", ""),
            "entry_ts": entry_ts,
            "exit_ts": exit_ts
        }

        all_trades.append(trade_info)

        # 检查是否在10-14当天
        is_1014 = False
        if t_1014_start <= entry_ts < t_1014_end:
            is_1014 = True
        elif exit_ts and t_1014_start <= exit_ts < t_1014_end:
            is_1014 = True

        if is_1014:
            trades_1014.append(trade_info)

    print(f"找到 10-14 相关的交易: {len(trades_1014)} 笔\n")

    if trades_1014:
        print("10-14 相关交易详情:")
        for t in trades_1014:
            print(f"\n#{t['index']} {t['side'].upper()}")
            print(f"  开仓: {t['entry_time'].strftime('%Y-%m-%d %H:%M:%S')} @ {t['entry_price']:.2f}")
            if t['exit_time']:
                print(f"  平仓: {t['exit_time'].strftime('%Y-%m-%d %H:%M:%S')} @ {t['exit_price']:.2f}")
                print(f"  盈亏: {t['pnl']:.2f} USDT")
                print(f"  原因: {t['close_reason']}")
            else:
                print(f"  状态: 持仓中")
    else:
        print("10-14 当天没有交易记录")
        print("\n最近的交易:")
        for t in all_trades[:5]:
            print(f"\n{t['side'].upper()}")
            print(f"  开仓: {t['entry_time'].strftime('%Y-%m-%d %H:%M:%S')} @ {t['entry_price']:.2f}")
            if t['exit_time']:
                print(f"  平仓: {t['exit_time'].strftime('%Y-%m-%d %H:%M:%S')} @ {t['exit_price']:.2f}")

# 2. 获取当前持仓
print("\n" + "=" * 80)
print("当前持仓状态")
print("=" * 80)

positions = get43("/api/trade/positions")
amd_pos = positions.get("positions", {}).get("AMD-USDT-SWAP")

if amd_pos:
    entry_time = datetime.fromtimestamp(amd_pos['entry_ts']/1000, tz=timezone.utc)
    print(f"\nAMD 当前持仓:")
    print(f"  方向: {amd_pos['side'].upper()}")
    print(f"  开仓时间: {entry_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  开仓价: {amd_pos['entry']:.2f}")
    print(f"  数量: {amd_pos['qty']} AMD")
    print(f"  当前价: {amd_pos['price']:.2f}")
    print(f"  浮动盈亏: {amd_pos.get('float_pnl', 0):.2f} USDT ({amd_pos.get('pnl_pct', 0):.2f}%)")
    print(f"  止损: {amd_pos.get('stop', 0):.2f}")
    print(f"  TP1完成: {amd_pos.get('tp1_done', False)}")

    # 检查是否是10-14开的仓
    if t_1014_start <= amd_pos['entry_ts'] < t_1014_end:
        print(f"\n  >>> 此持仓在 10-14 开仓！")
else:
    print("\n当前无AMD持仓")

print("\n" + "=" * 80)
print("对比分析")
print("=" * 80)
print("\n实际情况 vs 本地回测:")
print(f"  本地回测: 10-14 12:00 开空 @ 591.97")
print(f"  43实盘: {'见上' if trades_1014 or (amd_pos and t_1014_start <= amd_pos['entry_ts'] < t_1014_end) else '10-14无交易'}")
