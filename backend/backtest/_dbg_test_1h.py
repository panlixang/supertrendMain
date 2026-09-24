# -*- coding: utf-8 -*-
import urllib.request, json, time
after = 1661990400000  # 1h 现有最旧
url = f"https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT&bar=1H&limit=5&after={after}"
for attempt in range(3):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "st-signals/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read())
        print("attempt", attempt, "code=", d.get("code"), "msg=", d.get("msg"),
              "n=", len(d.get("data", [])), "first=", d.get("data", [])[0] if d.get("data") else None)
        break
    except Exception as e:
        print("attempt", attempt, "ERR:", repr(e))
        time.sleep(2)
