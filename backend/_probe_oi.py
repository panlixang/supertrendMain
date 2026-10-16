# -*- coding: utf-8 -*-
import urllib.request, json


def get(u):
    try:
        return json.load(urllib.request.urlopen(u, timeout=10))
    except Exception as e:
        return {"err": str(e)}


oi = get("https://www.okx.com/api/v3/markets/open-interest-history?symbol=SNDK-USDT-SWAP&range=1h")
print("OI type:", type(oi).__name__)
d = oi.get("data") if isinstance(oi, dict) else oi
print("OI len:", len(d) if d else 0)
if d:
    print("OI first:", d[0])
    print("OI last:", d[-1])

fr = get("https://www.okx.com/api/v3/markets/funding-history?symbol=SNDK-USDT-SWAP&limit=20")
fd = fr.get("data") if isinstance(fr, dict) else fr
print("FR len:", len(fd) if fd else 0)
if fd:
    print("FR first:", fd[0])
print("FR raw head:", str(fr)[:300])
