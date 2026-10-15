# -*- coding: utf-8 -*-
"""查 47 服务器（47.84.106.154:5174）SNDK/MU/SKHYNIX 的启用状态 + 最近成交。"""
import json
import urllib.request

BASE = "http://47.84.106.154:5174"


def get(ep, timeout=15):
    try:
        return json.loads(urllib.request.urlopen(BASE + ep, timeout=timeout).read())
    except Exception as e:
        return {"_error": repr(e)}


d = get("/api/trade/symbols")
syms = d.get("symbols", [])
import json as _j
for s in syms:
    if s["symbol"] in ("SNDK-USDT-SWAP", "MU-USDT-SWAP", "SKHYNIX-USDT-SWAP"):
        print(f"=== {s['symbol']} (47) ===")
        print("  params:", s.get("params"))
        print("  allow_tfs:", s.get("allow_tfs"), " allow_grades:", s.get("allow_grades"),
              " min_score:", s.get("min_score"), " filter_v3:", s.get("filter_v3"))
        print("  er_min:", s.get("er_min"), " er_trend:", s.get("er_trend"),
              " er_hide_below:", s.get("er_hide_below"))
        print("  含allow_short字段:", "allow_short" in s)
        print("  全部非params字段:", {k: v for k, v in s.items()
                                   if k not in ("symbol", "params", "exit_rules", "exit_rules_quick")})

# 重新查 SKHYNIX 完整成交（months=12），确认 10-14 08:00 空单
h = get("/api/trade/history?sym=SKHYNIX-USDT-SWAP&months=12")
print("\n=== SKHYNIX 完整成交 (47, months=12) ===")
print("  total_trades=", h.get("total_trades"), "files=", h.get("files"))
for t in (h.get("recent") or []):
    print("   ", t.get("logged_at"), t.get("side"), "entry=", t.get("entry"),
          "exit=", t.get("price"), "pnl%=", t.get("pnl_pct"),
          "reason=", t.get("close_reason"), "events=",
          [(e.get("kind"), e.get("msg")) for e in t.get("events", [])])
