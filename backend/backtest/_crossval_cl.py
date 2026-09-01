# -*- coding: utf-8 -*-
"""CL 分段稳健性验证：前 3 个月 vs 后 3 个月，对比基线/最优/候选配置。
数据来自 _live_data/cl_half_cache.json。"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest  # noqa: E402
from position_enhanced import EnhancedExitRules  # noqa: E402
from regime import TradeConfig  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "_live_data", "cl_half_cache.json")

ER = dict(
    enabled=True, tp1_pct=1.2, tp1_ratio=30.0, tp2_pct=3.0, tp2_ratio=40.0,
    tp3_pct=3.5, tp3_ratio=100.0, tp3_mode="reverse_signal",
    move_sl_to_entry=True, sl_mode="st", sl_pct=2.0, trail_with_st=True,
    sl_buffer_atr=0.5, sl_min_pct=1.2, protect_profit_at=1.5,
    protect_trail_pct=0.8, max_loss_enabled=True, max_loss_pct=2.0,
)

BASE_CFG = dict(
    enabled=True, leverage=10, amount_usdt=10.0, er_hide_below=0.1,
    er_weak_min=0.12, er_min=0.12, er_trend=0.3, quick_enabled=False,
    allow_grades=["A", "B", "C"], min_score=1, allow_tfs=["1h"],
    cooldown_sec=300, atr_filter_enabled=False, atr_vol_min=0.7,
    range_filter_enabled=True, range_size_max=0.15, range_touches_min=2,
    mtf_filter_enabled=False, mtf_consistency_min=0.6, mtf_flip_max=5,
    adx_filter_enabled=False, adx_min=20.0, adx_period=14,
    use_scoring=True, scoring_full_threshold=50.0,
    scoring_half_threshold=50.0, scoring_alert_threshold=40.0,
    use_dynamic_threshold=True,
)


def ts_fmt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def make_exit(**over) -> EnhancedExitRules:
    return EnhancedExitRules(**{**ER, **over})


def make_cfg(**over) -> TradeConfig:
    c = dict(BASE_CFG)
    c.update(over)
    return TradeConfig(**c)


def run_seg(candles, cbtf, p: dict, cfg: TradeConfig, er_over: dict | None = None) -> dict:
    r = run_backtest(
        candles, p, init_cash=100.0, fee_rate=0.0005, allow_short=True,
        exit_rules=make_exit(**(er_over or {})), sizing="fixed",
        margin_usdt=10.0, leverage=10,
        live_gate=cfg, gate_tf="1h", candles_by_tf=cbtf,
    )
    if "error" in r:
        return {"err": r["error"]}
    return {
        "pnl": round(r["final"] - 100.0, 2),
        "trades": r["trades"], "wr": r["win_rate"], "pf": r["profit_factor"],
        "dd": r["max_dd_pct"],
    }


def main():
    cache = json.load(open(CACHE, encoding="utf-8"))
    cbtf = {t: cache[t] for t in ("15m", "1h", "4h", "1d") if t in cache}
    candles = cbtf["1h"]
    mid = candles[len(candles) // 2]["ts"]
    seg_a = [c for c in candles if c["ts"] < mid]
    seg_b = [c for c in candles if c["ts"] >= mid]
    cbtf_a = {t: [c for c in v if c["ts"] < mid] for t, v in cbtf.items()}
    cbtf_b = {t: [c for c in v if c["ts"] >= mid] for t, v in cbtf.items()}
    print(f"全窗 {ts_fmt(candles[0]['ts'])}~{ts_fmt(candles[-1]['ts'])} "
          f"{len(candles)}根 | A段 {ts_fmt(seg_a[0]['ts'])}~{ts_fmt(seg_a[-1]['ts'])} "
          f"{len(seg_a)}根 | B段 {ts_fmt(seg_b[0]['ts'])}~{ts_fmt(seg_b[-1]['ts'])} "
          f"{len(seg_b)}根", flush=True)

    P = {"periods": 11, "multiplier": 4.0, "src": "hl2", "change_atr": True,
         "fast_len": 20, "slow_len": 50, "ma_type": "EMA"}

    cfgs = {
        # name: (params_over, cfg_over, er_over)
        "A 基线11×4/er0.12/0.12/0.3/score50-50-55m1/tp0.5-1.5-3/sl3ml3":
            (dict(P), dict(er_weak_min=0.12, er_min=0.12, er_trend=0.3,
                           min_score=1, scoring_full_threshold=50.0,
                           scoring_half_threshold=50.0, scoring_alert_threshold=55.0),
             dict(tp1_pct=0.5, tp2_pct=1.5, tp3_pct=3.0, sl_pct=3.0, max_loss_pct=3.0)),
        "B 最优19×2.5/er0.25/0.12/0.25/score50-50-40m0/tp1.2-3-3.5/sl2ml2":
            (dict(P, periods=19, multiplier=2.5),
             dict(er_weak_min=0.12, er_min=0.25, er_trend=0.25,
                  min_score=0, scoring_full_threshold=50.0,
                  scoring_half_threshold=50.0, scoring_alert_threshold=40.0),
             dict()),
        "C 7×6/er0.25/0.12/0.25/score50-50-40m0/tp1.2-3-3.5":
            (dict(P, periods=7, multiplier=6.0),
             dict(er_weak_min=0.12, er_min=0.25, er_trend=0.25,
                  min_score=0, scoring_full_threshold=50.0,
                  scoring_half_threshold=50.0, scoring_alert_threshold=40.0),
             dict()),
        "D 9×6/er0.25/0.12/0.25/score50-50-40m0/tp1.2-3-3.5":
            (dict(P, periods=9, multiplier=6.0),
             dict(er_weak_min=0.12, er_min=0.25, er_trend=0.25,
                  min_score=0, scoring_full_threshold=50.0,
                  scoring_half_threshold=50.0, scoring_alert_threshold=40.0),
             dict()),
        "E 19×2.5/er0.15/0.12/0.25/score50-50-40m0/tp1.2-3-3.5":
            (dict(P, periods=19, multiplier=2.5),
             dict(er_weak_min=0.12, er_min=0.15, er_trend=0.25,
                  min_score=0, scoring_full_threshold=50.0,
                  scoring_half_threshold=50.0, scoring_alert_threshold=40.0),
             dict()),
        "F 17×2.5/er0.25/0.12/0.25/score50-50-40m0/tp1.2-3-3.5":
            (dict(P, periods=17, multiplier=2.5),
             dict(er_weak_min=0.12, er_min=0.25, er_trend=0.25,
                  min_score=0, scoring_full_threshold=50.0,
                  scoring_half_threshold=50.0, scoring_alert_threshold=40.0),
             dict()),
    }

    print(f"\n{'配置':<58} | {'全窗pnl/tr/pf':<20} | {'A段pnl/tr/pf':<20} | {'B段pnl/tr/pf':<20}", flush=True)
    for name, (p, cfg_over, er_over) in cfgs.items():
        cfg = make_cfg(**cfg_over)
        full = run_seg(candles, cbtf, p, cfg, er_over)
        ra = run_seg(seg_a, cbtf_a, p, cfg, er_over)
        rb = run_seg(seg_b, cbtf_b, p, cfg, er_over)
        f = f"{full.get('pnl','-')}U/{full.get('trades','-')}笔/PF{full.get('pf','-')}"
        a = f"{ra.get('pnl','-')}U/{ra.get('trades','-')}笔/PF{ra.get('pf','-')}"
        b = f"{rb.get('pnl','-')}U/{rb.get('trades','-')}笔/PF{rb.get('pf','-')}"
        print(f"{name:<58} | {f:<20} | {a:<20} | {b:<20}", flush=True)

    out = os.path.join(BASE, "_crossval_cl.json")
    rows = []
    for name, (p, cfg_over, er_over) in cfgs.items():
        cfg = make_cfg(**cfg_over)
        rows.append({
            "name": name,
            "full": run_seg(candles, cbtf, p, cfg, er_over),
            "seg_a": run_seg(seg_a, cbtf_a, p, cfg, er_over),
            "seg_b": run_seg(seg_b, cbtf_b, p, cfg, er_over),
        })
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {out}", flush=True)


if __name__ == "__main__":
    main()
