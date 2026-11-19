# -*- coding: utf-8 -*-
"""对比43实盘和本地回测的关键时刻K线数据"""
import urllib.request
import json
from datetime import datetime, timezone

def pull_bitget(symbol, gran, limit=500):
    """从Bitget拉取K线"""
    url = (f"https://api.bitget.com/api/v2/mix/market/candles"
           f"?symbol={symbol}&productType=usdt-futures&granularity={gran}&limit={limit}")
    rows = json.loads(urllib.request.urlopen(url, timeout=20).read()).get("data", [])
    return [{"ts": int(x[0]), "o": float(x[1]), "h": float(x[2]),
             "l": float(x[3]), "c": float(x[4]), "vol": float(x[5])} for x in rows]

print("=" * 80)
print("对比10-14关键时刻的K线数据")
print("=" * 80)

# 拉取1h数据
candles_1h = pull_bitget("AMDUSDT", 3600, limit=500)

# 43实盘开仓时间：2026-10-14 04:00:01 UTC
# 本地回测开仓时间：2026-10-14 12:00:00 UTC

t_0400 = int(datetime(2026, 10, 14, 4, 0, tzinfo=timezone.utc).timestamp() * 1000)
t_1200 = int(datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)

print("\n查找关键时刻的K线:")
print("-" * 80)

for candle in candles_1h:
    ts = candle["ts"]
    dt = datetime.fromtimestamp(ts/1000, tz=timezone.utc)

    # 10-14 00:00 到 23:00的所有1h K线
    if datetime(2026, 10, 14, 0, 0, tzinfo=timezone.utc).timestamp() * 1000 <= ts < \
       datetime(2026, 10, 15, 0, 0, tzinfo=timezone.utc).timestamp() * 1000:

        marker = ""
        if ts == t_0400:
            marker = " <<< 43实盘开仓时刻"
        elif ts == t_1200:
            marker = " <<< 本地回测信号时刻"

        print(f"{dt.strftime('%Y-%m-%d %H:%M')} | O:{candle['o']:7.2f} H:{candle['h']:7.2f} "
              f"L:{candle['l']:7.2f} C:{candle['c']:7.2f}{marker}")

print("\n" + "=" * 80)
print("分析")
print("=" * 80)

# 找到04:00和12:00的K线
k_0400 = next((c for c in candles_1h if c["ts"] == t_0400), None)
k_1200 = next((c for c in candles_1h if c["ts"] == t_1200), None)

if k_0400:
    print(f"\n04:00 K线（43实盘开仓时刻）:")
    print(f"  开盘: {k_0400['o']:.2f}")
    print(f"  最高: {k_0400['h']:.2f}")
    print(f"  最低: {k_0400['l']:.2f}")
    print(f"  收盘: {k_0400['c']:.2f}")
    print(f"\n  43实盘开仓价: 606.93")
    print(f"  接近此K线的: ", end="")
    if abs(k_0400['o'] - 606.93) < 2:
        print(f"开盘价 (差{abs(k_0400['o'] - 606.93):.2f})")
    elif abs(k_0400['h'] - 606.93) < 2:
        print(f"最高价 (差{abs(k_0400['h'] - 606.93):.2f})")
    elif abs(k_0400['c'] - 606.93) < 2:
        print(f"收盘价 (差{abs(k_0400['c'] - 606.93):.2f})")
    else:
        print(f"无明显匹配")

if k_1200:
    print(f"\n12:00 K线（本地回测信号时刻）:")
    print(f"  开盘: {k_1200['o']:.2f}")
    print(f"  最高: {k_1200['h']:.2f}")
    print(f"  最低: {k_1200['l']:.2f}")
    print(f"  收盘: {k_1200['c']:.2f}")
    print(f"\n  本地回测开仓价: 591.97")
    print(f"  接近此K线的: ", end="")
    if abs(k_1200['o'] - 591.97) < 2:
        print(f"开盘价 (差{abs(k_1200['o'] - 591.97):.2f})")
    elif abs(k_1200['h'] - 591.97) < 2:
        print(f"最高价 (差{abs(k_1200['h'] - 591.97):.2f})")
    elif abs(k_1200['c'] - 591.97) < 2:
        print(f"收盘价 (差{abs(k_1200['c'] - 591.97):.2f})")
    else:
        print(f"无明显匹配")

print("\n" + "=" * 80)
print("结论")
print("=" * 80)
print("\n可能的问题:")
print("1. 43实盘在04:00就有信号，比本地12:00提前了8小时")
print("2. 04:00和12:00的价格相差15美元，可能是不同信号")
print("3. 需要检查43服务器上04:00为什么会有信号")
print("4. 需要确认12:00的空信号在43上是否被触发但被某些过滤器拒绝")
