# -*- coding: utf-8 -*-
"""BTC ER 优化部署：er_weak_min 0.12→0.08（er_min 0.12、er_trend 0.3、scoring、
params、TP/SL 全部保持线上）。
依据：半年寻优+前半年交叉验证，er_weak=0.08 两段窗口均显著优于线上。
推送后切品种并用 /api/backtest 线上验证。"""
import json
import urllib.request

LIVE = "http://43.108.10.84:5174"
HDR = {"Content-Type": "application/json", "User-Agent": "supertrend-bt/1.0"}


def post(url, data):
    req = urllib.request.Request(url, data=json.dumps(data).encode(),
                                 headers=HDR, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def get(url):
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main():
    # 1) 推送 ER 修改（局部更新，其余字段不动）
    body = {"symbol": "BTC-USDT-SWAP", "er_weak_min": 0.08}
    print("POST /api/trade/symbols ->", json.dumps(body))
    r = post(LIVE + "/api/trade/symbols", body)
    print("resp ok:", r.get("ok"), r.get("error", ""), flush=True)

    # 2) GET 验证 BTC 配置
    s = next(x for x in get(LIVE + "/api/trade/symbols")["symbols"]
             if x["symbol"] == "BTC-USDT-SWAP")
    print("\n=== BTC 改后配置 ===")
    for k in ("er_hide_below", "er_weak_min", "er_min", "er_trend",
              "scoring_full_threshold", "scoring_half_threshold",
              "scoring_alert_threshold", "min_score", "enabled"):
        print(f"  {k} = {s.get(k)}")
    p = s["params"]
    print(f"  params.periods = {p.get('periods')}  params.multiplier = {p.get('multiplier')}")

    # 3) 切品种到 BTC 并线上回测验证
    r2 = post(LIVE + "/api/symbol", {"instId": "BTC-USDT-SWAP"})
    print("\nswitch symbol:", r2.get("ok"), r2.get("symbol", ""), flush=True)

    bt = post(LIVE + "/api/backtest", {
        "tf": "1h", "bars": 4500, "init_cash": 100.0, "fee_rate": 0.0005,
        "allow_short": True, "use_exit_rules": True,
        "sizing": "fixed", "margin_usdt": 10.0, "leverage": 10,
        "periods": 11, "multiplier": 4.0,
    })
    print("\n=== 线上 /api/backtest 验证 ===")
    for k in ("symbol", "params", "trades", "win", "loss", "win_rate",
              "profit_factor", "final", "init", "max_dd_pct", "er_blocked"):
        if k in bt:
            print(f"  {k} = {bt[k]}")
    if "error" in bt:
        print("  error =", bt["error"], flush=True)


if __name__ == "__main__":
    main()
