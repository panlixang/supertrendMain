"""XAU-USDT-SWAP 黄金 15m / 1h 超趋参数寻优（本地 live_gate，ER 闸门无叠加过滤）。"""
from __future__ import annotations

import copy
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest import run_backtest
from position_enhanced import EnhancedExitRules
from regime import TradeConfig
from _live_cfg_backtest import BARS, BIAS_TFS, fetch_candles, ts_fmt

SYMBOL = "XAU-USDT-SWAP"
PERIODS = list(range(7, 22, 2))
MULTS = list(range(2, 11))
MIN_TRADES = 8

# 黄金默认脚本参数（对照组）
DEFAULT_P, DEFAULT_M = 15, 9.1
SUGGEST_P, SUGGEST_M = 13, 6.0  # 上次 15m 建议


def gold_sym(gate_tf: str) -> dict:
    return {
        "symbol": SYMBOL,
        "margin_usdt": 10.0,
        "leverage": 10,
        "allow_tfs": [gate_tf],
        "er_hide_below": 0.12,
        "er_weak_min": 0.12,
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
            "periods": DEFAULT_P,
            "multiplier": DEFAULT_M,
            "src": "hl2",
            "change_atr": True,
            "fast_len": 20,
            "slow_len": 50,
            "ma_type": "EMA",
        },
    }


def gold_cfg(sym: dict) -> TradeConfig:
    return TradeConfig(
        enabled=True,
        leverage=sym["leverage"],
        amount_usdt=sym["margin_usdt"],
        er_hide_below=sym["er_hide_below"],
        er_weak_min=sym["er_weak_min"],
        er_min=sym["er_min"],
        er_trend=sym["er_trend"],
        quick_enabled=sym["quick_enabled"],
        allow_grades=list(sym["allow_grades"]),
        min_score=sym["min_score"],
        allow_tfs=list(sym["allow_tfs"]),
        cooldown_sec=sym["cooldown_sec"],
        atr_filter_enabled=sym["atr_filter_enabled"],
        atr_vol_min=sym["atr_vol_min"],
        range_filter_enabled=sym["range_filter_enabled"],
        range_size_max=sym["range_size_max"],
        range_touches_min=sym["range_touches_min"],
        mtf_filter_enabled=sym["mtf_filter_enabled"],
        mtf_consistency_min=sym["mtf_consistency_min"],
        mtf_flip_max=sym["mtf_flip_max"],
        adx_filter_enabled=sym["adx_filter_enabled"],
        adx_min=sym["adx_min"],
        adx_period=sym["adx_period"],
    )


def gold_exit() -> EnhancedExitRules:
    return EnhancedExitRules(
        enabled=True,
        tp1_pct=1.5,
        tp1_ratio=70.0,
        move_sl_to_entry=True,
        sl_mode="st",
        sl_pct=2.0,
        trail_with_st=True,
        tp2_pct=999.0,
        tp2_ratio=0.0,
        tp3_pct=999.0,
        tp3_ratio=0.0,
        sl_buffer_atr=0.5,
        sl_min_pct=1.2,
        protect_profit_at=999.0,
        protect_trail_pct=0.0,
        max_loss_enabled=False,
        max_loss_pct=10.0,
    )


def score_row(pnl: float, dd: float, trades: int) -> float:
    if trades < MIN_TRADES or dd <= 0:
        return -999.0
    return pnl / dd


def run_grid(sym: dict, gate_tf: str, candles: list, cbtf: dict) -> list[dict]:
    cfg = gold_cfg(sym)
    rules = gold_exit()
    base_p = sym["params"]
    rows = []
    total = len(PERIODS) * len(MULTS)
    n = 0
    t0 = time.time()
    for pe in PERIODS:
        for m in MULTS:
            n += 1
            p = {**base_p, "periods": pe, "multiplier": float(m)}
            r = run_backtest(
                candles,
                p,
                init_cash=100.0,
                fee_rate=0.0005,
                allow_short=True,
                exit_rules=rules,
                sizing="fixed",
                margin_usdt=sym["margin_usdt"],
                leverage=sym["leverage"],
                live_gate=cfg,
                gate_tf=gate_tf,
                candles_by_tf=cbtf,
            )
            if "error" in r:
                continue
            pnl = round(r["final"] - 100, 2)
            rows.append({
                "periods": pe,
                "multiplier": float(m),
                "pnl_u": pnl,
                "margin_roi_pct": round(pnl / sym["margin_usdt"] * 100, 1),
                "max_dd_pct": r["max_dd_pct"],
                "trades": r["trades"],
                "win_rate": r["win_rate"],
                "profit_factor": r["profit_factor"],
                "blocked": r["er_blocked"],
                "score": round(score_row(pnl, r["max_dd_pct"], r["trades"]), 3),
                "is_default": pe == DEFAULT_P and float(m) == float(DEFAULT_M),
                "is_prev_suggest": pe == SUGGEST_P and float(m) == float(SUGGEST_M),
            })
            if n % 18 == 0:
                print(f"  {gate_tf} {n}/{total} …", flush=True)
    rows.sort(key=lambda x: x["score"], reverse=True)
    print(f"  {gate_tf} done {len(rows)} combos in {time.time()-t0:.0f}s", flush=True)
    return rows


def pack(gate_tf: str, rows: list[dict], start: int, end: int, bars: int) -> dict:
    qualified = [r for r in rows if r["trades"] >= MIN_TRADES]
    default = next((r for r in rows if r["is_default"]), None)
    prev = next((r for r in rows if r["is_prev_suggest"]), None)
    top8 = qualified[:8]
    return {
        "gate_tf": gate_tf,
        "start": ts_fmt(start),
        "end": ts_fmt(end),
        "bars": bars,
        "default_15x9": default,
        "prev_suggest_13x6": prev,
        "best_score": top8[0] if top8 else None,
        "best_pnl": max(rows, key=lambda r: r["pnl_u"]) if rows else None,
        "top8": top8,
        "all": rows,
    }


def cross_best(a: dict, b: dict) -> dict | None:
    def idx(rows):
        return {
            f"{r['periods']}×{r['multiplier']}": r
            for r in rows if r["trades"] >= MIN_TRADES
        }
    ia, ib = idx(a["all"]), idx(b["all"])
    common = set(ia) & set(ib)
    if not common:
        return None
    best = max(common, key=lambda k: ia[k]["pnl_u"] + ib[k]["pnl_u"])
    return {
        "p": best,
        "15m": ia[best],
        "1h": ib[best],
        "combined_pnl": round(ia[best]["pnl_u"] + ib[best]["pnl_u"], 2),
    }


def main():
    out: dict = {"symbol": SYMBOL, "filters": "ER 0.15 + grade/score 闸门，无 ATR/区间/ADX"}
    all_by_tf: dict = {}

    for gate_tf, bar_n in (("15m", BARS["15m"]), ("1h", 8000)):
        sym = gold_sym(gate_tf)
        print(f"\n=== XAU fetch {gate_tf} ===", flush=True)
        candles = fetch_candles(SYMBOL, gate_tf, bar_n)
        cbtf = {gate_tf: candles}
        for tf in BIAS_TFS:
            if tf == gate_tf:
                continue
            extra = fetch_candles(SYMBOL, tf, min(len(candles), BARS.get(tf, 4500)))
            if extra:
                cbtf[tf] = extra
        rows = run_grid(sym, gate_tf, candles, cbtf)
        all_by_tf[gate_tf] = pack(
            gate_tf, rows, candles[0]["ts"], candles[-1]["ts"], len(candles)
        )
        d = all_by_tf[gate_tf]["default_15x9"]
        b = all_by_tf[gate_tf]["best_score"]
        if d:
            print(
                f"  default {DEFAULT_P}×{DEFAULT_M}: {d['pnl_u']}U dd={d['max_dd_pct']}% "
                f"trades={d['trades']}",
                flush=True,
            )
        if b:
            print(
                f"  best {b['periods']}×{b['multiplier']}: {b['pnl_u']}U dd={b['max_dd_pct']}% "
                f"PF={b['profit_factor']}",
                flush=True,
            )

    cross = cross_best(all_by_tf["15m"], all_by_tf["1h"])
    out["15m"] = {k: v for k, v in all_by_tf["15m"].items() if k != "all"}
    out["1h"] = {k: v for k, v in all_by_tf["1h"].items() if k != "all"}
    if cross:
        out["cross_best_pnl"] = cross

    path = os.path.join(os.path.dirname(__file__), "_xau_opt.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    with open(path.replace(".json", "_full.json"), "w", encoding="utf-8") as f:
        json.dump(
            {"symbol": SYMBOL, "15m": all_by_tf["15m"], "1h": all_by_tf["1h"], "cross": cross},
            f,
            ensure_ascii=False,
        )
    print(f"\nWrote {path}", flush=True)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
