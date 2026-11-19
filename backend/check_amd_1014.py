# -*- coding: utf-8 -*-
"""检查 43 服务器 AMD 10-14 为何没有空信号"""
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

# 1. 获取 AMD 当前配置
print("=" * 80)
print("1. 43服务器 AMD 当前配置")
print("=" * 80)
symbols_data = get43("/api/trade/symbols")
amd_cfg = None
for s in symbols_data.get("symbols", []):
    if s.get("symbol") == "AMD-USDT-SWAP":
        amd_cfg = s
        break

if amd_cfg:
    print(f"enabled: {amd_cfg.get('enabled')}")
    print(f"margin_usdt: {amd_cfg.get('margin_usdt')}")
    print(f"leverage: {amd_cfg.get('leverage')}")
    print(f"use_scoring: {amd_cfg.get('use_scoring')}")
    print(f"scoring_full_threshold: {amd_cfg.get('scoring_full_threshold')}")
    print(f"scoring_half_threshold: {amd_cfg.get('scoring_half_threshold')}")
    print(f"scoring_alert_threshold: {amd_cfg.get('scoring_alert_threshold')}")
    print(f"use_dynamic_threshold: {amd_cfg.get('use_dynamic_threshold')}")
    print(f"cooldown_sec: {amd_cfg.get('cooldown_sec')}")
    print(f"er_min: {amd_cfg.get('er_min')}")
    print(f"er_trend: {amd_cfg.get('er_trend')}")
    print(f"filter_v3: {amd_cfg.get('filter_v3')}")
else:
    print("❌ 未找到 AMD-USDT-SWAP 配置")

# 2. 获取交易历史
print("\n" + "=" * 80)
print("2. AMD 最近交易历史")
print("=" * 80)
history = get43("/api/trade/history?sym=AMD-USDT-SWAP&recent=10")
if "recent" in history:
    for trade in history["recent"]:
        entry_time = datetime.fromtimestamp(trade["entry_ts"]/1000, tz=timezone.utc)
        exit_time = ""
        if trade.get("exit_ts"):
            exit_time = datetime.fromtimestamp(trade["exit_ts"]/1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

        print(f"\n交易时间: {entry_time.strftime('%Y-%m-%d %H:%M')} -> {exit_time}")
        print(f"  方向: {trade['side']}")
        print(f"  开仓价: {trade['entry']}")
        print(f"  平仓价: {trade.get('exit', 'N/A')}")
        print(f"  盈亏: {trade.get('realized', 0):.2f} USDT")
        print(f"  平仓原因: {trade.get('close_reason', 'N/A')}")

# 3. 检查当前持仓
print("\n" + "=" * 80)
print("3. AMD 当前持仓")
print("=" * 80)
positions = get43("/api/trade/positions")
amd_pos = positions.get("positions", {}).get("AMD-USDT-SWAP")
if amd_pos:
    print(f"持仓方向: {amd_pos['side']}")
    print(f"开仓价: {amd_pos['entry']}")
    print(f"数量: {amd_pos['qty']}")
    print(f"当前价: {amd_pos['price']}")
    print(f"浮动盈亏: {amd_pos.get('float_pnl', 0):.2f} USDT ({amd_pos.get('pnl_pct', 0):.2f}%)")
else:
    print("当前无持仓")

# 4. 尝试获取系统日志（如果有API）
print("\n" + "=" * 80)
print("4. 检查信号判定日志")
print("=" * 81)
# 尝试获取可能的日志端点
for endpoint in ["/api/logs", "/api/signals", "/api/debug/signals", "/api/trade/signals"]:
    result = get43(endpoint)
    if "error" not in result and "detail" not in result:
        print(f"\n{endpoint} 返回:")
        print(json.dumps(result, indent=2, ensure_ascii=False)[:1000])

print("\n" + "=" * 80)
print("5. 对比本地配置")
print("=" * 80)
import os
local_cfg_path = os.path.join(os.path.dirname(__file__), "backtest", "AMD", "amd_config.json")
if os.path.exists(local_cfg_path):
    with open(local_cfg_path, 'r', encoding='utf-8') as f:
        local_cfg = json.load(f)

    print("\n关键配置对比:")
    keys_to_check = ["enabled", "use_scoring", "scoring_full_threshold",
                     "scoring_half_threshold", "scoring_alert_threshold",
                     "use_dynamic_threshold", "er_min", "er_trend", "filter_v3",
                     "cooldown_sec", "margin_usdt", "leverage"]

    for key in keys_to_check:
        local_val = local_cfg.get(key)
        remote_val = amd_cfg.get(key) if amd_cfg else None
        if local_val != remote_val:
            print(f"[X] {key}: 本地={local_val}, 线上={remote_val}")
        else:
            print(f"[OK] {key}: {local_val}")
