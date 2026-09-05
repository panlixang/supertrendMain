# -*- coding: utf-8 -*-
"""把 SKHYNIX、CL 以校准参数配置到 47.84.106.154 并启用。

SKHYNIX：8-31 TP 寻优对齐口径 —— 9×3.0 / ER 0.11/0.12/0.12/0.3 /
打分制 70/70/60 动态、A/B min_score=2、过滤器全关、10U×10x 1h、
exit_rules 现盘模板 sl 3.0/极端止损关 + tp 决策 1.2/1.5/3.5。

CL：寻优最优（_apply_cl.py 全套）—— 19×2.5 / ER 0.25/0.12/0.25 /
打分制 50/50/40 min_score=0 动态、range 过滤器、tp 1.2/3/3.5、sl 2.0/极端止损关。
"""
import json
import urllib.request

LIVE = "http://47.84.106.154:5174"
HDR = {"Content-Type": "application/json", "User-Agent": "supertrend-bt/1.0"}


def post(url, data, timeout=180):
    req = urllib.request.Request(url, data=json.dumps(data).encode(),
                                 headers=HDR, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def get(url):
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def skhynix_body():
    return {
        "symbol": "SKHYNIX-USDT-SWAP",
        "enabled": True,
        "margin_usdt": 10.0,
        "leverage": 10,
        "allow_tfs": ["1h"],
        "sizing_mode": "fixed",
        # ER 闸门（8-31 线上口径）
        "er_hide_below": 0.11,
        "er_weak_min": 0.12,
        "er_min": 0.12,
        "er_trend": 0.3,
        "quick_enabled": False,
        "allow_grades": ["A", "B"],
        "min_score": 2,
        "cooldown_sec": 300,
        # 指标参数（寻优 9×3.0）
        "periods": 9,
        "multiplier": 3.0,
        # 过滤器全关
        "atr_filter_enabled": False,
        "atr_vol_min": 0.7,
        "range_filter_enabled": False,
        "range_size_max": 0.15,
        "range_touches_min": 2,
        "mtf_filter_enabled": False,
        "mtf_consistency_min": 0.6,
        "mtf_flip_max": 5,
        "adx_filter_enabled": False,
        "adx_min": 20.0,
        "adx_period": 14,
        # 打分制 70/70/60 动态
        "use_scoring": True,
        "scoring_full_threshold": 70.0,
        "scoring_half_threshold": 70.0,
        "scoring_alert_threshold": 60.0,
        "use_dynamic_threshold": True,
        # 出场：tp 决策 1.2/1.5/3.5 + 现盘 sl 3.0 / 极端止损关
        "exit_rules": {
            "enabled": True,
            "tp1_pct": 1.2, "tp1_ratio": 30.0,
            "tp2_pct": 1.5, "tp2_ratio": 40.0,
            "tp3_pct": 3.5, "tp3_ratio": 100.0,
            "tp3_mode": "reverse_signal",
            "move_sl_to_entry": True,
            "sl_mode": "st", "sl_pct": 3.0, "trail_with_st": True,
            "sl_buffer_atr": 0.5, "sl_min_pct": 1.2,
            "protect_profit_at": 1.5, "protect_trail_pct": 0.8,
            "max_loss_enabled": False, "max_loss_pct": 3.0,
        },
    }


def cl_body():
    return {
        "symbol": "CL-USDT-SWAP",
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
        # 过滤器
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
        # 打分制 50/50/40
        "use_scoring": True,
        "scoring_full_threshold": 50.0,
        "scoring_half_threshold": 50.0,
        "scoring_alert_threshold": 40.0,
        "use_dynamic_threshold": True,
        # 出场：tp 1.2/3/3.5, sl 2.0, 极端止损关
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
            "max_loss_enabled": False, "max_loss_pct": 2.0,
        },
    }


def show(sym, name):
    print(f"\n=== {name} 上线后配置 ===")
    for k in ("enabled", "margin_usdt", "leverage", "er_hide_below",
              "er_weak_min", "er_min", "er_trend", "quick_enabled",
              "allow_grades", "min_score", "scoring_full_threshold",
              "scoring_half_threshold", "scoring_alert_threshold",
              "use_dynamic_threshold", "range_filter_enabled"):
        print(f"  {k} = {sym.get(k)}")
    p = sym["params"]
    print(f"  params.periods = {p.get('periods')}  params.multiplier = {p.get('multiplier')}")
    er = sym.get("exit_rules") or {}
    for k in ("tp1_pct", "tp2_pct", "tp3_pct", "tp3_mode", "sl_mode",
              "sl_pct", "max_loss_enabled", "max_loss_pct", "enabled"):
        print(f"  exit.{k} = {er.get(k)}")


def main():
    for name, body in (("SKHYNIX", skhynix_body()), ("CL", cl_body())):
        print(f"\n>>> POST {name}", flush=True)
        print("  ", json.dumps(body, ensure_ascii=False)[:200], "...", flush=True)
        r = post(LIVE + "/api/trade/symbols", body)
        print("  resp ok:", r.get("ok"), r.get("error", ""), flush=True)

    syms = get(LIVE + "/api/trade/symbols")["symbols"]
    for want in ("SKHYNIX", "CL"):
        s = next(x for x in syms if x["symbol"].startswith(want))
        show(s, want)


if __name__ == "__main__":
    main()
