# -*- coding: utf-8 -*-
"""探测 43 / 47 当前开启品种 + 确认是否已是含 flow 字段的新版。"""
import os
import sys
import json
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

HOSTS = {
    "43": "http://43.108.10.84:5174",
    "47": "http://47.84.106.154:5174",
}


def get(host, path):
    with urllib.request.urlopen(host + path, timeout=25) as r:
        return json.loads(r.read().decode())


for tag, host in HOSTS.items():
    try:
        data = get(host, "/api/trade/symbols")
        syms = data.get("symbols", [])
        on = [s for s in syms if s.get("enabled")]
        print(f"\n===== {tag} ({host})  共 {len(syms)} 品种，开启 {len(on)} =====")
        has_flow = any("flow_enabled" in s for s in syms)
        print(f"新版(含 flow 字段): {'是' if has_flow else '否 ← 仍是旧版，需先部署'}")
        for s in on:
            p = s.get("params", {})
            print(f"  {s['symbol']:20s} ST={p.get('periods')}x{p.get('multiplier')}  "
                  f"flow_enabled={s.get('flow_enabled', '<无字段>')} "
                  f"mode={s.get('flow_mode', '-')} "
                  f"theta={s.get('flow_theta', '-')} veto={s.get('flow_veto', '-')} "
                  f"lb={s.get('flow_lookback', '-')} scale={s.get('flow_scale', '-')}")
    except Exception as e:
        print(f"\n===== {tag} ({host}) 不可达: {e} =====")
