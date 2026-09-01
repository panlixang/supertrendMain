# -*- coding: utf-8 -*-
"""按线上最新代码与配置计算 MU 的回测收益。"""
import json
import urllib.request

BASE = "http://43.108.10.84:5174/api"


def _post(url: str, body: dict) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": "supertrend-bt/1.0",
                      "Content-Type": "application/json"},
        data=json.dumps(body).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "supertrend-bt/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def main():
    syms = _get(f"{BASE}/trade/symbols")["symbols"]
    mu = [s for s in syms if "MU" in s["symbol"].upper()]
    print("[MU 品种列表]", flush=True)
    for s in mu:
        print("  " + json.dumps(s, ensure_ascii=False), flush=True)

    if not mu:
        print("线上无 MU 品种", flush=True)
        return

    for s in mu:
        print(f"\n[回测 {s['symbol']} →]", flush=True)
        bt = _post(f"{BASE}/backtest?symbol={s['symbol']}", {
            "tf": "1h", "bars": 4500, "init_cash": 100.0, "fee_rate": 0.0005,
            "allow_short": True, "use_exit_rules": True,
            "sizing": "fixed", "margin_usdt": 10.0, "leverage": 10,
        })
        for k in ("trades", "win", "loss", "win_rate", "profit_factor",
                  "final", "init", "max_dd_pct", "er_blocked"):
            if k in bt:
                print(f"  {k} = {bt[k]}", flush=True)
        if "error" in bt:
            print("  error =", bt["error"], flush=True)


if __name__ == "__main__":
    main()
