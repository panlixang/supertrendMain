#!/usr/bin/env python
"""检查47服务器 SNDK/MU/AMD 配置和交易情况"""
import sys
import json
from urllib.request import urlopen, Request

HOST47 = "http://47.84.106.154:5174"

def fetch(path):
    try:
        req = Request(f"{HOST47}{path}")
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}

print("=== 47 Config Check ===\n")
configs = fetch("/api/trade/config")
if "error" in configs:
    print(f"ERROR: Cannot connect to 47 server: {configs['error']}")
    sys.exit(1)

for symbol in ["SNDK-USDT-SWAP", "MU-USDT-SWAP", "AMD-USDT-SWAP"]:
    cfg = configs.get(symbol, {})
    print(f"\n[{symbol}]")
    print(f"  enabled: {cfg.get('enabled')}")
    print(f"  use_scoring: {cfg.get('use_scoring')}")
    print(f"  scoring_full_threshold: {cfg.get('scoring_full_threshold')}")
    print(f"  er_min: {cfg.get('er_min')}")

print("\n\n=== 47 Recent Trades ===\n")
for symbol in ["SNDK-USDT-SWAP", "MU-USDT-SWAP", "AMD-USDT-SWAP"]:
    data = fetch(f"/api/trade/history?symbol={symbol}")
    total = data.get("total_trades", 0)
    trades = data.get("recent", [])

    print(f"\n[{symbol}] Total: {total}")

    if trades:
        for t in trades[-3:]:
            entry = t.get("entry_ts", 0)
            exit_ts = t.get("exit_ts", 0)
            side = t.get("side", "?")
            pnl = t.get("realized", 0)
            reason = t.get("close_reason", "")

            from datetime import datetime
            entry_str = datetime.fromtimestamp(entry/1000).strftime("%m-%d %H:%M") if entry else "?"
            exit_str = datetime.fromtimestamp(exit_ts/1000).strftime("%m-%d %H:%M") if exit_ts else "?"

            print(f"  {entry_str} -> {exit_str}: {side}, PNL {pnl:.2f}")
            if reason:
                print(f"    Reason: {reason}")
    else:
        print("  No trades")

print("\n\n=== 47 Current Positions ===\n")
positions = fetch("/api/trade/positions")
pos_dict = positions.get("positions", {})

if pos_dict:
    for symbol, pos in pos_dict.items():
        print(f"  {symbol}: {pos.get('side')} @ {pos.get('entry')}")
else:
    print("  No positions")
