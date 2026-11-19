"""
测试 Bitget 数据拉取功能
"""

import sys
sys.path.insert(0, '.')

from history_bitget import fetch_candles_bitget

def test_bitget():
    print("=== 测试 Bitget K线数据拉取 ===\n")

    # 测试拉取 AMD 1h K线
    symbol = "AMD-USDT-SWAP"
    tf = "1h"
    limit = 10

    print(f"拉取 {symbol} {tf} K线，最近 {limit} 根...")
    candles = fetch_candles_bitget(tf, limit, symbol)

    if candles:
        print(f"✅ 成功拉取 {len(candles)} 根K线\n")
        print("最近 3 根K线：")
        for c in candles[-3:]:
            from datetime import datetime
            dt = datetime.fromtimestamp(c.ts / 1000)
            print(f"  {dt.strftime('%Y-%m-%d %H:%M')} | "
                  f"O:{c.o:.2f} H:{c.h:.2f} L:{c.l:.2f} C:{c.c:.2f} V:{c.vol:.2f}")
    else:
        print("❌ 未能拉取到数据")
        return False

    return True

if __name__ == "__main__":
    success = test_bitget()
    sys.exit(0 if success else 1)
