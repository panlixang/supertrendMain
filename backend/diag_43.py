# -*- coding: utf-8 -*-
"""诊断 43 服务异常：逐个端点探测，打印状态码与响应正文（截断）。"""
import sys
import json
import urllib.request
import urllib.error

sys.stdout.reconfigure(encoding="utf-8")

HOST = "http://43.108.10.84:5174"
ENDPOINTS = [
    ("GET", "/api/symbol"),
    ("GET", "/api/params"),
    ("GET", "/api/trade/symbols"),
    ("GET", "/api/config"),
    ("GET", "/api/status"),
    ("GET", "/"),
]

for method, path in ENDPOINTS:
    try:
        req = urllib.request.Request(HOST + path, method=method)
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode("utf-8", "replace")
            print(f"[{r.status}] {method} {path}  ({len(body)} bytes)")
            print(f"        {body[:400]}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace") if e.fp else ""
        print(f"[{e.code}] {method} {path}  <-- 异常")
        print(f"        {body[:800]}")
    except Exception as e:
        print(f"[ERR] {method} {path}  {type(e).__name__}: {e}")
    print("-" * 90)
