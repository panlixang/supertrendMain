# -*- coding: utf-8 -*-
"""同时 dump 两个模块在两个页上的真实内容，厘清「首页策略」到底指哪个。"""
import json, urllib.request

HOSTS = {"43": "http://43.108.10.84:5174", "47": "http://47.84.106.154:5174"}
HDR = {"User-Agent": "probe/1.0", "Content-Type": "application/json"}


def _http(host, path):
    try:
        req = urllib.request.Request(host + path, headers=HDR, method="GET")
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"__err__": repr(e)}


for tag, host in HOSTS.items():
    for mod in ("trade", "pattern/trade"):
        d = _http(host, f"/api/{mod}/symbols")
        if "__err__" in d:
            print(f"[{tag}] /api/{mod}/symbols -> {d['__err__']}")
            continue
        syms = d.get("symbols") or []
        print(f"\n##### [{tag}] /api/{mod}/symbols  ({len(syms)} 个) #####")
        for s in syms:
            p = s.get("params") or {}
            st = f"{p.get('periods')}×{p.get('multiplier')}/{p.get('src')}" if p else "无params"
            gate = (f"se={s.get('score_engine')!r} f={s.get('scoring_full_threshold')} "
                    f"rng={s.get('range_filter_enabled')}") if s.get('use_scoring') is not None else ""
            er = s.get("exit_rules") or {}
            ex = f"tp1={er.get('tp1_pct')}/{er.get('tp1_ratio')} sl={er.get('sl_pct')}({er.get('sl_mode')}) tr={er.get('trail_with_st')}"
            print(f"  {s.get('symbol'):<20} en={s.get('enabled')} ST={st:<14} {gate} {ex}")
