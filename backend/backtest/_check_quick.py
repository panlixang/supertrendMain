# -*- coding: utf-8 -*-
import json, urllib.request

def _req(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "x"})) as r:
        return json.loads(r.read())

for node, base in [("43", "http://43.108.10.84:5174"), ("47", "http://47.84.106.154:5174")]:
    g = _req(f"{base}/api/trade/config")
    syms = _req(f"{base}/api/trade/symbols").get("symbols", [])
    print(f"=== node {node}  GLOBAL quick_enabled={g.get('quick_enabled')} "
          f"enabled={g.get('enabled')} paper={g.get('paper')}")
    for s in syms:
        n = s["symbol"].replace("-USDT-SWAP", "").replace("-USDT", "")
        if n in ("NVDA", "CL", "BTC", "ETH", "SPCX"):
            eq = (s.get("exit_rules_quick") or {})
            print(f"  {node} {n}: per-sym quick_enabled={s.get('quick_enabled')} "
                  f"er_weak_min={s.get('er_weak_min')} er_min={s.get('er_min')} "
                  f"exit_quick.enabled={eq.get('enabled')} tp1={eq.get('tp1_pct')} "
                  f"sl={eq.get('sl_pct')}/{eq.get('sl_mode')}")
