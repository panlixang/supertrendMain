# -*- coding: utf-8 -*-
"""查看两台服务的品种上限、当前品种清单（用于确认 MAX_SYMBOLS 改动是否生效）。"""
import sys
import json
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

HOSTS = [("43", "http://43.108.10.84:5174"), ("47", "http://47.84.106.154:5174")]

for tag, h in HOSTS:
    d = json.loads(urllib.request.urlopen(h + "/api/trade/symbols", timeout=25).read().decode())
    syms = [s["symbol"].replace("-USDT-SWAP", "") for s in d["symbols"]]
    print(f"服务 {tag}: 上限 max={d['max']}  品种数={len(syms)}")
    print(f"         {syms}")
    for s in d["symbols"]:
        p = s.get("params", {})
        print(f"         {s['symbol'].replace('-USDT-SWAP',''):<9} "
              f"ST {p.get('periods')}x{p.get('multiplier')} {p.get('src'):<5} "
              f"gate {s.get('scoring_full_threshold')}/{s.get('scoring_alert_threshold')} "
              f"ER {s.get('er_trend')}/{s.get('er_min')} margin={s.get('margin_usdt')} "
              f"enabled={s.get('enabled')}")
    print()
