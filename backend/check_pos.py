# -*- coding: utf-8 -*-
"""检查两台服务所有品种的启用状态与当前持仓（禁用不会自动平仓，需人工确认）。"""
import sys
import json
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

HOSTS = [("43", "http://43.108.10.84:5174"), ("47", "http://47.84.106.154:5174")]

for tag, host in HOSTS:
    d = json.loads(urllib.request.urlopen(host + "/api/trade/symbols", timeout=25).read().decode())
    print(f"===== 服务 {tag} ({host})  上限={d['max']}  品种数={len(d['symbols'])} =====")
    for s in d["symbols"]:
        name = s["symbol"].replace("-USDT-SWAP", "")
        pos = s.get("position")
        if pos and (pos.get("qty") or 0) != 0:
            p = f"持仓 {pos.get('side')} qty={pos.get('qty')} entry={pos.get('entry_price')}"
        else:
            p = "无持仓"
        print(f"  {name:<9} enabled={str(s.get('enabled')):<5} margin={s.get('margin_usdt')}U  {p}")
    print()
