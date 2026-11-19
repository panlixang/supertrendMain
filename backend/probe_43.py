# -*- coding: utf-8 -*-
"""探针：调 43 页交易接口，看 INTC/AMD 实盘成交与持仓。"""
import urllib.request, json
BASE = "http://43.108.10.84:5174"


def get(ep):
    try:
        return json.loads(urllib.request.urlopen(BASE + ep, timeout=15).read())
    except Exception as e:
        return {"__err__": repr(e)}


d = get("/api/trade/positions")
print("=== /api/trade/positions ===")
print(json.dumps(d, ensure_ascii=False)[:2500])

for sym in ["INTC-USDT-SWAP", "AMD-USDT-SWAP"]:
    d = get(f"/api/trade/history?sym={sym}&recent=300")
    print(f"\n=== /api/trade/history {sym} ===")
    print(json.dumps(d, ensure_ascii=False)[:3500])
