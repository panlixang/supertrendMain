# -*- coding: utf-8 -*-
"""CL-USDT-SWAP 寻优推荐部署到 47.84.106.154。
最优：params 19×2.5 / ER 0.25/0.12/0.25 / 打分制 50/50/40 min_score=0 动态 /
range 过滤器 / tp 1.2/3/3.5 reverse / sl 2.0 / max_loss 2.0 / 10U×10x 1h。
推送后 GET 验证 + /api/backtest 线上复核。"""
import json
import urllib.request

LIVE = "http://47.84.106.154:5174"
HDR = {"Content-Type": "application/json", "User-Agent": "supertrend-bt/1.0"}
SYM = "CL-USDT-SWAP"


def post(url, data, timeout=180):
    req = urllib.request.Request(url, data=json.dumps(data).encode(),
                                 headers=HDR, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def get(url):
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main():
    body = {
        "symbol": SYM,
        "enabled": True,
        "margin_usdt": 10.0,
        "leverage": 10,
        "allow_tfs": ["1h"],
        "sizing_mode": "fixed",
        # ER 闸门（CL 寻优最优）
        "er_hide_below": 0.1,
        "er_weak_min": 0.12,
        "er_min": 0.25,
        "er_trend": 0.25,
        "quick_enabled": False,
        "allow_grades": ["A", "B", "C"],
        "min_score": 0,
        "cooldown_sec": 300,
        # 指标参数
        "periods": 19,
        "multiplier": 2.5,
        # 过滤器（与寻优 BASE_CFG 一致）
        "atr_filter_enabled": False,
        "atr_vol_min": 0.7,
        "range_filter_enabled": True,
        "range_size_max": 0.15,
        "range_touches_min": 2,
        "mtf_filter_enabled": False,
        "mtf_consistency_min": 0.6,
        "mtf_flip_max": 5,
        "adx_filter_enabled": False,
        "adx_min": 20.0,
        "adx_period": 14,
        # 打分制
        "use_scoring": True,
        "scoring_full_threshold": 50.0,
        "scoring_half_threshold": 50.0,
        "scoring_alert_threshold": 40.0,
        "use_dynamic_threshold": True,
        # 出场规则（寻优最优 tp 1.2/3/3.5, sl 2.0, ml 2.0）
        "exit_rules": {
            "enabled": True,
            "tp1_pct": 1.2, "tp1_ratio": 30.0,
            "tp2_pct": 3.0, "tp2_ratio": 40.0,
            "tp3_pct": 3.5, "tp3_ratio": 100.0,
            "tp3_mode": "reverse_signal",
            "move_sl_to_entry": True,
            "sl_mode": "st", "sl_pct": 2.0, "trail_with_st": True,
            "sl_buffer_atr": 0.5, "sl_min_pct": 1.2,
            "protect_profit_at": 1.5, "protect_trail_pct": 0.8,
            "max_loss_enabled": True, "max_loss_pct": 2.0,
        },
    }
    print("POST /api/trade/symbols ->", json.dumps(body))
    r = post(LIVE + "/api/trade/symbols", body)
    print("resp ok:", r.get("ok"), r.get("error", ""), flush=True)
    if not r.get("ok"):
        return

    # GET 验证 CL 配置
    syms = get(LIVE + "/api/trade/symbols")["symbols"]
    s = next(x for x in syms if x["symbol"] == SYM)
    print("\n=== CL 上线后配置 ===")
    for k in ("enabled", "margin_usdt", "leverage", "er_hide_below",
              "er_weak_min", "er_min", "er_trend", "quick_enabled",
              "min_score", "scoring_full_threshold", "scoring_half_threshold",
              "scoring_alert_threshold", "range_filter_enabled",
              "range_touches_min"):
        print(f"  {k} = {s.get(k)}")
    p = s["params"]
    print(f"  params.periods = {p.get('periods')}  params.multiplier = {p.get('multiplier')}")
    er = s["exit_rules"]
    for k in ("tp1_pct", "tp2_pct", "tp3_pct", "tp3_mode", "sl_pct",
              "max_loss_enabled", "max_loss_pct", "enabled"):
        print(f"  exit.{k} = {er.get(k)}")

    # 切品种 + 线上回测复核
    r2 = post(LIVE + "/api/symbol", {"instId": SYM})
    print("\nswitch symbol:", r2.get("ok"), r2.get("symbol", ""), flush=True)

    bt = post(LIVE + "/api/backtest", {
        "tf": "1h", "bars": 4380, "init_cash": 100.0, "fee_rate": 0.0005,
        "allow_short": True, "use_exit_rules": True, "quick_enabled": False,
        "sizing": "fixed", "margin_usdt": 10.0, "leverage": 10,
        "periods": 19, "multiplier": 2.5,
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
