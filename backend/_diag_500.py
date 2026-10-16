# -*- coding: utf-8 -*-
"""抓取 43/47 的 500 响应体 + 探测多个端点，定位是整站崩还是单接口 bug。"""
import os
import sys
import json
import urllib.request
import urllib.error

sys.stdout.reconfigure(encoding="utf-8")

HOSTS = {
    "43": "http://43.108.10.84:5174",
    "47": "http://47.84.106.154:5174",
}
PATHS = ["/api/trade/symbols", "/api/params", "/", "/docs", "/openapi.json"]


def probe(host, path):
    try:
        with urllib.request.urlopen(host + path, timeout=20) as r:
            return f"200 (len={len(r.read())})"
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")[:600]
        except Exception:
            body = "<no body>"
        return f"{e.code}  body: {body}"
    except Exception as e:
        return f"ERR {e}"


for tag, host in HOSTS.items():
    print(f"\n===== {tag} ({host}) =====")
    for p in PATHS:
        print(f"  GET {p:22s} -> {probe(host, p)}")
