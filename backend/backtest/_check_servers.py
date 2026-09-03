# -*- coding: utf-8 -*-
"""查看 47.84.106.154 上的品种与 SKHYNIX/CL 状态。"""
import json
import urllib.request

for host in ("http://47.84.106.154:5174", "http://43.108.10.84:5174"):
    url = host + "/api/trade/symbols"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "supertrend-bt/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        print(f"=== {host} ===")
        for s in data["symbols"]:
            p = s.get("params", {})
            er = s.get("exit_rules", {})
            print(f"  {s['symbol']:<20} enabled={s.get('enabled')} "
                  f"p={p.get('periods')}x{p.get('multiplier')} "
                  f"er={s.get('er_min')}/{s.get('er_weak_min')}/{s.get('er_trend')} "
                  f"score={s.get('scoring_full_threshold')}/{s.get('scoring_half_threshold')}/{s.get('scoring_alert_threshold')} "
                  f"m{s.get('min_score')} "
                  f"sl={er.get('sl_pct')} ml={er.get('max_loss_pct')}")
    except Exception as e:
        print(f"=== {host} === ERROR: {e}")
