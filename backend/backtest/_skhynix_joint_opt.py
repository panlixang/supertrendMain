"""SKHYNIX-USDT-SWAP 海力士 1h 联合寻优：闸门 + 超趋。

不在线上交易列表，出场对齐现盘模板：
  10U×10x、TP1 1%/30 → TP2 2%/40 → 反向可下单信号全平、
  保本、ST 跟踪、极端止损 3%。
起点超趋 15×5（与 BTC 同档，网格会再搜）。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _joint_opt import GATE_TF, run_symbol
from _live_cfg_backtest import BARS, BIAS_TFS, fetch_candles

SYMBOL = "SKHYNIX-USDT-SWAP"

LIVE_EXIT = {
    "enabled": True,
    "tp1_pct": 1.0, "tp1_ratio": 30.0,
    "tp2_pct": 2.0, "tp2_ratio": 40.0,
    "tp3_pct": 3.5, "tp3_ratio": 100.0,
    "tp3_mode": "reverse_signal",
    "move_sl_to_entry": True,
    "sl_mode": "st", "sl_pct": 3.0,
    "trail_with_st": True,
    "max_loss_enabled": True, "max_loss_pct": 3.0,
    "sl_buffer_atr": 0.5, "sl_min_pct": 1.2,
    "protect_profit_at": 1.5, "protect_trail_pct": 0.8,
}


def stock_sym() -> dict:
    return {
        "symbol": SYMBOL,
        "margin_usdt": 10.0,
        "leverage": 10,
        "allow_tfs": [GATE_TF],
        "er_hide_below": 0.11,
        "er_weak_min": 0.11,
        "er_min": 0.15,
        "er_trend": 0.3,
        "quick_enabled": False,
        "allow_grades": ["A", "B", "C"],
        "min_score": 1,
        "cooldown_sec": 300,
        "atr_filter_enabled": False,
        "atr_vol_min": 0.7,
        "range_filter_enabled": False,
        "range_size_max": 0.15,
        "range_touches_min": 3,
        "mtf_filter_enabled": False,
        "mtf_consistency_min": 0.6,
        "mtf_flip_max": 5,
        "adx_filter_enabled": False,
        "adx_min": 20.0,
        "adx_period": 14,
        "params": {
            "periods": 15,
            "multiplier": 5.0,
            "src": "hl2",
            "change_atr": True,
            "fast_len": 20,
            "slow_len": 50,
            "ma_type": "EMA",
        },
        "exit_rules": LIVE_EXIT,
    }


def main():
    sym = stock_sym()
    print(f"### fetch SKHYNIX {GATE_TF} ###", flush=True)
    candles = fetch_candles(SYMBOL, GATE_TF, BARS[GATE_TF])
    if len(candles) < 80:
        print(f"K线不足: {len(candles)}", flush=True)
        return
    cbtf = {GATE_TF: candles}
    for tf in BIAS_TFS:
        if tf == GATE_TF:
            continue
        extra = fetch_candles(SYMBOL, tf, min(BARS[GATE_TF], BARS.get(tf, 4500)))
        if extra:
            cbtf[tf] = extra

    out = run_symbol("SKHYNIX", sym, candles, cbtf)
    out["symbol"] = SYMBOL
    out["exit"] = "tp1 1%/30 + tp2 2%/40 + tp3 reverse_signal + ST trail + max_loss 3%"
    path = os.path.join(os.path.dirname(__file__), "_skhynix_joint_opt_1h.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {path}", flush=True)
    slim = {
        "start": out.get("start"), "end": out.get("end"), "bars": out.get("bars"),
        "live": out.get("live"), "best": out.get("best"),
        "best_gate_at_live_st": out.get("best_gate_at_live_st"),
        "top_st_best_gate": out.get("top_st_best_gate"),
        "top_gates_live_st": out.get("top_gates_live_st"),
    }
    print(json.dumps(slim, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
