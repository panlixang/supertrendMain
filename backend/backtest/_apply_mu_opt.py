# -*- coding: utf-8 -*-
"""MU 寻优推荐A 部署：periods 22→18, multiplier 保持 3.0,
ER 0.12/0.12/0.3 → 0.2/0.15/0.25。改后 GET 验证。"""
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
    body = {"symbol": "MU-USDT-SWAP", "periods": 18, "multiplier": 3.0,
            "er_min": 0.2, "er_weak_min": 0.15, "er_trend": 0.25}
    print("POST /api/trade/symbols ->", json.dumps(body))
    r = post(LIVE + "/api/trade/symbols", body)
    print("resp ok:", r.get("ok"), r.get("error", ""), flush=True)

    s = next(x for x in get(LIVE + "/api/trade/symbols")["symbols"]
             if x["symbol"] == "MU-USDT-SWAP")
    print("\n=== MU 改后 ===")
    for k in ("periods", "multiplier", "er_min", "er_weak_min", "er_trend",
              "scoring_full_threshold", "scoring_half_threshold",
              "scoring_alert_threshold", "enabled"):
        print(f"  {k} = {s.get(k)}")
    p = s["params"]
    print(f"  params.periods = {p.get('periods')}  params.multiplier = {p.get('multiplier')}")


if __name__ == "__main__":
    main()
