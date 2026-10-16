# -*- coding: utf-8 -*-
"""探测 43 服务器当前开启的品种 + 本地数据/配置可用性，为 flow 双因子验证做准备。"""
import os
import sys
import json
import sqlite3
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
HOST = "http://43.108.10.84:5174"

try:
    with urllib.request.urlopen(HOST + "/api/trade/symbols", timeout=25) as r:
        data = json.loads(r.read().decode())
    syms = data.get("symbols", [])
except Exception as e:
    print(f"[43 不可达] {HOST} -> {e}")
    sys.exit(0)

print(f"43 服务器共 {len(syms)} 个品种")
on = [s for s in syms if s.get("enabled")]
print(f"当前开启(enabled=True): {len(on)} 个 -> {[s['symbol'] for s in on]}")
print(f"当前关闭: {[s['symbol'] for s in syms if not s.get('enabled')]}")

# 本地 1h 数据品种
con = sqlite3.connect(os.path.join(HERE, "candle_data.db"))
have = {row[0] for row in con.execute("SELECT DISTINCT symbol FROM candles WHERE tf='1h'")}
con.close()
print(f"\n本地 1h 数据品种: {sorted(have)}")

# 本地 live_config
cfg_dir = os.path.join(HERE, "backtest")
local_cfg = {}
if os.path.isdir(cfg_dir):
    for root, _, files in os.walk(cfg_dir):
        for f in files:
            if f.endswith("_live_config.json"):
                local_cfg[f.replace("_live_config.json", "").lower()] = os.path.join(root, f)

print(f"\n本地 *_live_config.json: {sorted(local_cfg)}")


def match_key(sym):
    base = sym.upper().replace("-USDT-SWAP", "").replace("-USDT", "").lower()
    return base


print("\n==== 各开启品种可验证性 ====")
for s in on:
    sym = s["symbol"]
    p = s.get("params", {})
    key = match_key(sym)
    local_data = any(sym in h or h in sym for h in have)
    cfg_path = local_cfg.get(key)
    print(f"\n{sym}  ST={p.get('periods')}x{p.get('multiplier')}  "
          f"tfs={s.get('allow_tfs')}  margin={s.get('margin_usdt')}U lev={s.get('leverage')}x")
    print(f"  本地1h数据: {'有' if local_data else '无'}   本地live_config: {cfg_path or '无'}")
    print(f"  flow_enabled(43线上)= {s.get('flow_enabled', '<旧版无此字段>')}")
