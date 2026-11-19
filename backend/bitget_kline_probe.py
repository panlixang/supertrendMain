# -*- coding: utf-8 -*-
"""验证 bitget 公共 K 线接口格式（INTC-USDT-SWAP -> INTCUSDT）。只读，不写 db。"""
import json, urllib.request

def probe(tf, gran):
    url = (f"https://api.bitget.com/api/v2/mix/market/candles"
           f"?symbol=INTCUSDT&productType=usdt-futures&granularity={gran}&limit=3")
    with urllib.request.urlopen(url, timeout=15) as r:
        d = json.loads(r.read())
    print(f"=== {tf} (gran={gran}) code={d.get('code')} ===")
    print(json.dumps(d, ensure_ascii=False)[:800])

probe("1h", 3600)
probe("15m", 900)
probe("4h", 14400)
