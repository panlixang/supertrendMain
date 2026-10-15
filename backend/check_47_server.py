#!/usr/bin/env python
"""检查47服务器 SNDK/MU/AMD 配置和交易情况"""
import sys
import json
from urllib.request import urlopen, Request
from urllib.error import URLError

HOST47 = "http://47.84.106.154:5174"

def fetch(path):
    try:
        req = Request(f"{HOST47}{path}")
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}

print("=== 47服务器配置检查 ===\n")
configs = fetch("/api/trade/config")
if "error" in configs:
    print(f"❌ 无法连接47服务器: {configs['error']}")
    sys.exit(1)

for symbol in ["SNDK-USDT-SWAP", "MU-USDT-SWAP", "AMD-USDT-SWAP"]:
    cfg = configs.get(symbol, {})
    print(f"\n【{symbol}】")
    print(f"  enabled: {cfg.get('enabled')}")
    print(f"  use_scoring: {cfg.get('use_scoring')}")
    print(f"  scoring_full_threshold: {cfg.get('scoring_full_threshold')}")
    print(f"  er_min: {cfg.get('er_min')}")
    print(f"  cooldown_sec: {cfg.get('cooldown_sec')}")

print("\n\n=== 47服务器最近交易 ===\n")
for symbol in ["SNDK-USDT-SWAP", "MU-USDT-SWAP", "AMD-USDT-SWAP"]:
    data = fetch(f"/api/trade/history?symbol={symbol}")
    trades = data.get("trades", [])
    total = data.get("total_trades", 0)
    print(f"\n【{symbol}】总交易数: {total}")

    if trades:
        # 显示最近3笔
        for t in trades[-3:]:
            entry_time = t.get("entry_time", "").split("T")[0] if t.get("entry_time") else "?"
            exit_time = t.get("exit_time", "").split("T")[0] if t.get("exit_time") else "?"
            side = t.get("side", "?")
            pnl = t.get("pnl", 0)
            print(f"  {entry_time} -> {exit_time}: {side}, 盈亏 {pnl:.2f} USDT")
    else:
        print("  ❌ 无交易记录")

print("\n\n=== 47服务器当前持仓 ===\n")
positions = fetch("/api/trade/positions")
pos_list = positions.get("positions", {})
if pos_list:
    for symbol, pos in pos_list.items():
        print(f"  {symbol}: {pos.get('side')} @ {pos.get('entry_price')} (开仓: {pos.get('entry_time', '').split('T')[0]})")
else:
    print("  当前无持仓")
