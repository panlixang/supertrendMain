# -*- coding: utf-8 -*-
"""对照实测：同一份本地数据，分别用 47 与 43 的线上配置回测，确认差异来源。"""
import os
import sys
import json
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BACKEND, "backtest"))
from engine import run_core, _extract, _load_tf

HOST47 = "http://47.84.106.154:5174"
HOST43 = "http://43.108.10.84:5174"
TS_2026 = 1767225600000


def load(host):
    d = json.loads(urllib.request.urlopen(host + "/api/trade/symbols", timeout=25).read().decode())
    return {s["symbol"]: s for s in d["symbols"]}


m47, m43 = load(HOST47), load(HOST43)
common = [s for s in m47 if s in m43]

for sym in common:
    db = sym.replace("-USDT-SWAP", "-USDT")
    cd = {tf: [c for c in _load_tf(db, tf) if c["ts"] >= TS_2026] for tf in ["15m", "1h", "4h"]}
    lens = {tf: len(v) for tf, v in cd.items()}
    sc47, pr47, er47 = _extract(m47[sym])
    sc43, pr43, er43 = _extract(m43[sym])
    try:
        r47 = run_core(db, sc47, pr47, er47, tf="1h", candles=cd)
        n47 = r47["all"]["n"] if r47["all"] else 0
        ret47 = r47["all"]["ret_pct"] if r47["all"] else 0
    except Exception as e:
        n47, ret47 = -1, str(e)
    try:
        r43 = run_core(db, sc43, pr43, er43, tf="1h", candles=cd)
        n43 = r43["all"]["n"] if r43["all"] else 0
        ret43 = r43["all"]["ret_pct"] if r43["all"] else 0
    except Exception as e:
        n43, ret43 = -1, str(e)
    flag = "OK" if n47 == n43 else "<<< 不一致"
    print(f"{sym.replace('-USDT-SWAP',''):<9} 数据 {lens} | "
          f"47: n={n47} ret={ret47 if isinstance(ret47, str) else round(ret47,2)}% | "
          f"43: n={n43} ret={ret43 if isinstance(ret43, str) else round(ret43,2)}%  {flag}")
    # 关键：找出 _extract 结果的差异
    s47, s43 = {}, {}
    for name, a, b in (("sc", sc47, sc43), ("pr", pr47, pr43), ("er", er47, er43)):
        for k in set(a) | set(b):
            if a.get(k) != b.get(k):
                print(f"          [差异] {name}.{k}: 47={a.get(k)} 43={b.get(k)}")
