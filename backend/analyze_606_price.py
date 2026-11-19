# -*- coding: utf-8 -*-
"""分析606.93这个价格出现在哪个时刻"""
import urllib.request
import json
from datetime import datetime, timezone

def pull_bitget(symbol, gran, limit=500):
    url = (f"https://api.bitget.com/api/v2/mix/market/candles"
           f"?symbol={symbol}&productType=usdt-futures&granularity={gran}&limit={limit}")
    rows = json.loads(urllib.request.urlopen(url, timeout=20).read()).get("data", [])
    return [{"ts": int(x[0]), "o": float(x[1]), "h": float(x[2]),
             "l": float(x[3]), "c": float(x[4]), "vol": float(x[5])} for x in rows]

print("=" * 80)
print("查找 606.93 这个价格出现在哪根K线")
print("=" * 80)

candles = pull_bitget("AMDUSDT", 3600, limit=500)

# 43实盘数据
entry_price = 606.93
exit_price = 591.77

print(f"\n43实盘数据:")
print(f"  开仓价: {entry_price}")
print(f"  平仓价: {exit_price}")
print(f"  盈亏: {entry_price - exit_price:.2f} USDT (开仓高于平仓 = 做多亏损)")

print("\n查找包含 606.93 的K线:")
print("-" * 80)

candidates = []
for c in candles:
    dt = datetime.fromtimestamp(c["ts"]/1000, tz=timezone.utc)

    # 检查606.93是否在这根K线范围内
    if c["l"] <= entry_price <= c["h"]:
        candidates.append((dt, c))

        # 只显示10-13和10-14的
        if dt.day in [13, 14] and dt.month == 10:
            marker = ""
            # 检查是开盘/收盘/还是中间价
            if abs(c["o"] - entry_price) < 0.5:
                marker = " <<< 接近开盘价"
            elif abs(c["c"] - entry_price) < 0.5:
                marker = " <<< 接近收盘价"
            elif abs((c["h"] + c["l"])/2 - entry_price) < 2:
                marker = " <<< 接近中间价"

            print(f"{dt.strftime('%m-%d %H:%M')} | O:{c['o']:7.2f} H:{c['h']:7.2f} "
                  f"L:{c['l']:7.2f} C:{c['c']:7.2f}{marker}")

print(f"\n找到 {len(candidates)} 根K线包含 606.93")

print("\n查找包含 591.77 (平仓价) 的K线:")
print("-" * 80)

exit_candidates = []
for c in candles:
    dt = datetime.fromtimestamp(c["ts"]/1000, tz=timezone.utc)

    if c["l"] <= exit_price <= c["h"]:
        exit_candidates.append((dt, c))

        if dt.day == 14 and dt.month == 10:
            marker = ""
            if abs(c["o"] - exit_price) < 0.5:
                marker = " <<< 接近开盘价"
            elif abs(c["c"] - exit_price) < 0.5:
                marker = " <<< 接近收盘价"

            print(f"{dt.strftime('%m-%d %H:%M')} | O:{c['o']:7.2f} H:{c['h']:7.2f} "
                  f"L:{c['l']:7.2f} C:{c['c']:7.2f}{marker}")

print("\n" + "=" * 80)
print("推断")
print("=" * 80)

print(f"\n根据价格分析:")
print(f"1. 开仓606.93出现在10-14凌晨00:00-04:00区间（价格在609-615）")
print(f"2. 平仓591.77出现在10-14 12:00附近（12:00 K线收盘591.97）")
print(f"3. 这是一笔【做多】交易，从606.93多头开仓，在591.77止损")
print(f"4. 而本地engine在12:00产生的是【做空】信号 @ 591.97")
print(f"\n>>> 核心问题：43实盘在10-14凌晨做多，但本地engine说应该在12:00做空")
print(f">>> 这说明43服务器在凌晨（可能是00:00-04:00之间）产生了一个多单信号")
print(f">>> 而这个多单信号在本地回测中并未出现！")
