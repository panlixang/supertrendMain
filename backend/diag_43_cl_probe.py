# -*- coding: utf-8 -*-
"""直接远程查 43(43.108.10.84:5174)的 pattern 配置与状态，定位 CL 9-27 多单来源。
只读 GET 查询，不改任何东西。
"""
import json, urllib.request

HOST = "http://43.108.10.84:5174"
UA = {"User-Agent": "diag/1.0"}

def _get(path, timeout=20):
    with urllib.request.urlopen(urllib.request.Request(HOST + path, headers=UA), timeout=timeout) as r:
        return json.loads(r.read())

def show(name, path, limit=4000):
    print("\n=== 43 %s ===" % path)
    try:
        d = _get(path)
    except Exception as e:
        print("  ERROR: %r" % (e,)); return None
    s = json.dumps(d, ensure_ascii=False, indent=1)
    print(s[:limit])
    if len(s) > limit:
        print("  ...(截断，总长 %d)" % len(s))
    return d

cfg = show("config", "/api/pattern/trade/config")
st = show("state", "/api/pattern/trade/state", limit=6000)
