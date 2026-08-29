"""提交 MU/SPCX 方案A配置到线上：
- MU:   er_min=0.12, er_weak_min=0.12, quick_enabled=false, 分数 40→45
- SPCX: er_min=0.12, er_weak_min=0.12, quick_enabled=false, 分数 50 保持
改后 GET 验证。"""
import json
import urllib.request

LIVE = "http://43.108.10.84:5174"
HDR = {"Content-Type": "application/json", "User-Agent": "supertrend-bt/1.0"}


def post(url, data):
    req = urllib.request.Request(url, data=json.dumps(data).encode(),
                                 headers=HDR, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def get(url):
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main():
    updates = [
        {"symbol": "MU-USDT-SWAP", "er_min": 0.12, "er_weak_min": 0.12,
         "quick_enabled": False, "scoring_full_threshold": 45.0,
         "scoring_half_threshold": 45.0},
        {"symbol": "SPCX-USDT-SWAP", "er_min": 0.12, "er_weak_min": 0.12,
         "quick_enabled": False, "scoring_full_threshold": 50.0,
         "scoring_half_threshold": 50.0},
    ]
    for body in updates:
        print("POST /api/trade/symbols ->", json.dumps(body))
        r = post(LIVE + "/api/trade/symbols", body)
        print("resp ok:", r.get("ok"), r.get("error", ""), flush=True)

    syms = get(LIVE + "/api/trade/symbols")["symbols"]
    for want in ("MU", "SPCX"):
        s = next(x for x in syms if x["symbol"].startswith(want))
        print(f"\n=== {want} 改后 ===")
        for k in ("er_min", "er_weak_min", "quick_enabled",
                  "scoring_full_threshold", "enabled"):
            print(f"  {k} = {s.get(k)}")


if __name__ == "__main__":
    main()
