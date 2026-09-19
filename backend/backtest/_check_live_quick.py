# -*- coding: utf-8 -*-
import json, urllib.request

def _req(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "x"})) as r:
        return json.loads(r.read())

for node, base in [("47", "http://47.84.106.154:5174")]:
    d = _req(f"{base}/api/trade/positions")
    closed = d.get("closed", [])
    print(f"=== node {node}: 共 {len(closed)} 笔已平仓 ===")
    for sym in ("CL-USDT-SWAP", "NVDA-USDT-SWAP"):
        rows = [c for c in closed if c.get("symbol") == sym]
        quick = [c for c in rows if c.get("profile") == "quick"]
        print(f"  {node} {sym}: 总={len(rows)} 弱档(quick)={len(quick)}")
        for c in quick[-6:]:
            print(f"      ts={c.get('entry_ts')} side={c.get('side')} "
                  f"pnl%={c.get('pnl_pct')} reason={c.get('reason')}")
