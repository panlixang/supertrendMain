# -*- coding: utf-8 -*-
"""查 43 页 SKHYNIX/MU/SNDK 的真实启用状态 + 最近成交（确认昨天是否有单）。"""
import json
import urllib.request

BASE = "http://43.108.10.84:5174"


def get(ep, timeout=15):
    return json.loads(urllib.request.urlopen(BASE + ep, timeout=timeout).read())


d = get("/api/trade/symbols")
print("=== 线上全部品种 enabled 状态 ===")
for s in d["symbols"]:
    print(f"  {s['symbol']:20s} enabled={str(s.get('enabled')):5s} "
          f"ST={s.get('periods')}x{s.get('multiplier')} "
          f"range={s.get('range_filter_enabled')} mtf={s.get('mtf_filter_enabled')}")

for sym in ["SKHYNIX-USDT-SWAP", "MU-USDT-SWAP", "SNDK-USDT-SWAP"]:
    try:
        h = get(f"/api/trade/history?sym={sym}&months=3")
        print(f"\n=== {sym} ===")
        print(f"  total_trades={h.get('total_trades')}  files={h.get('files')}")
        for t in (h.get("recent") or [])[:8]:
            print("  ", t)
    except Exception as e:
        print(f"\n{sym} ERR", repr(e))
