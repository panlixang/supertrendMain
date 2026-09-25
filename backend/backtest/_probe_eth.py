import urllib.request, json
for url in [
    "https://api.binance.com/api/v3/klines?symbol=ETHUSDT&interval=1h&limit=2",
    "https://www.okx.com/api/v5/market/history-kline?instId=ETH-USDT&bar=1H&limit=2",
]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(url.split("//")[1].split("/")[0], "OK", json.loads(resp.read().decode())[:1])
    except Exception as e:
        print(url.split("//")[1].split("/")[0], "失败:", repr(e))
