# -*- coding: utf-8 -*-
"""全字段比对 47 与 43 的品种配置（不限 SymbolCfgIn 白名单），定位回测差异真凶。"""
import sys
import json
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

HOST47 = "http://47.84.106.154:5174"
HOST43 = "http://43.108.10.84:5174"


def load(host):
    d = json.loads(urllib.request.urlopen(host + "/api/trade/symbols", timeout=25).read().decode())
    return {s["symbol"]: s for s in d["symbols"]}


def flat(obj, prefix=""):
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(flat(v, f"{prefix}{k}."))
    else:
        out[prefix[:-1]] = obj
    return out


m47, m43 = load(HOST47), load(HOST43)
common = [s for s in m47 if s in m43]
print(f"47: {len(m47)} 个 | 43: {len(m43)} 个 | 共同: {len(common)}")
print("=" * 96)

for sym in common:
    a, b = flat(m47[sym]), flat(m43[sym])
    keys = sorted(set(a) | set(b))
    diffs = []
    for k in keys:
        va, vb = a.get(k, "<缺失>"), b.get(k, "<缺失>")
        if isinstance(va, float) and isinstance(vb, float):
            if abs(va - vb) > 1e-9:
                diffs.append(f"{k}: 47={va} 43={vb}")
        elif va != vb:
            diffs.append(f"{k}: 47={va} 43={vb}")
    name = sym.replace("-USDT-SWAP", "")
    if not diffs:
        print(f"{name:<9} OK 完全一致")
    else:
        print(f"{name:<9} {len(diffs)} 处差异:")
        for d in diffs:
            print(f"          {d}")
    print("-" * 96)
