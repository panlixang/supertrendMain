import json, urllib.request
for port in [5174, 5175]:
    try:
        url = f"http://43.108.10.84:{port}/api/pattern/trade/config"
        r = urllib.request.urlopen(url, timeout=20)
        d = json.loads(r.read())
        print(f"=== port {port} keys: {list(d.keys())}")
        print(json.dumps(d, indent=2, ensure_ascii=False)[:4000])
    except Exception as e:
        print(f"port {port} error: {e}")
