# -*- coding: utf-8 -*-
"""SKHYNIX-USDT-SWAP 参数寻优：periods×multiplier、ER 档位、分数拦截。
出场规则固定为线上全局 normal 档。分阶段贪心，控制总跑量。"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import replace

from backtest import run_backtest  # noqa: E402
from position_enhanced import EnhancedExitRules  # noqa: E402
from regime import TradeConfig  # noqa: E402

from _live_cfg_backtest import fetch_candles, ts_fmt  # noqa: E402

SYM = "SKHYNIX-USDT-SWAP"
TF = "1h"
BARS = 4500
BIAS_TFS = ["15m", "1h", "4h", "1d"]
BARS_BY_TF = {"15m": 9000, "1h": 4500, "4h": 4500, "1d": 4500}

# 线上全局 normal 出场规则（固定，不参与寻优）
ER = dict(
    enabled=True, tp1_pct=1.0, tp1_ratio=30.0, tp2_pct=2.0, tp2_ratio=40.0,
    tp3_pct=3.5, tp3_ratio=30.0, tp3_mode="pct", move_sl_to_entry=True,
    sl_mode="st", sl_pct=2.0, trail_with_st=True, sl_buffer_atr=0.5,
    sl_min_pct=1.2, protect_profit_at=1.5, protect_trail_pct=0.8,
    max_loss_enabled=True, max_loss_pct=10.0,
)

BASE_CFG = dict(
    enabled=True, leverage=10, amount_usdt=10.0, er_hide_below=0.1,
    er_weak_min=0.12, er_min=0.2, er_trend=0.3, quick_enabled=False,
    allow_grades=["A", "B", "C"], min_score=1, allow_tfs=[TF],
    cooldown_sec=300, atr_filter_enabled=False, atr_vol_min=0.7,
    range_filter_enabled=False, range_size_max=0.15, range_touches_min=3,
    mtf_filter_enabled=False, mtf_consistency_min=0.6, mtf_flip_max=5,
    adx_filter_enabled=False, adx_min=20.0, adx_period=14,
    use_scoring=True, scoring_full_threshold=80.0,
    scoring_half_threshold=60.0, scoring_alert_threshold=40.0,
    use_dynamic_threshold=True,
)


def make_exit() -> EnhancedExitRules:
    return EnhancedExitRules(**ER)


def make_cfg(**over) -> TradeConfig:
    c = dict(BASE_CFG)
    c.update(over)
    return TradeConfig(**c)


def one(p: dict, cfg: TradeConfig, candles, cbtf) -> dict:
    r = run_backtest(
        candles, p, init_cash=100.0, fee_rate=0.0005, allow_short=True,
        exit_rules=make_exit(), sizing="fixed", margin_usdt=10.0, leverage=10,
        live_gate=cfg, gate_tf=TF, candles_by_tf=cbtf,
    )
    if "error" in r:
        return None
    return {
        "pnl": round(r["final"] - 100.0, 2),
        "trades": r["trades"], "wr": r["win_rate"], "pf": r["profit_factor"],
        "dd": r["max_dd_pct"], "blocked": r["er_blocked"],
    }


def show(tag: str, rows: list[dict], n: int = 12) -> None:
    print(f"\n=== {tag} TOP {n} ===", flush=True)
    for i, x in enumerate(sorted(rows, key=lambda r: r["pnl"], reverse=True)[:n], 1):
        print(
            f"  {i:2d}. p={x['params']} er={x['er']} score={x['score']} "
            f"| pnl={x['pnl']}U trades={x['trades']} wr={x['wr']}% "
            f"PF={x['pf']} dd={x['dd']}% blocked={x['blocked']}",
            flush=True,
        )


def _pf(x) -> float:
    return 999.0 if x["pf"] is None else x["pf"]


def rank(rows: list[dict], top: int) -> list[dict]:
    good = [r for r in rows if r["pnl"] > 0 and _pf(r) >= 1.2 and r["dd"] <= 18]
    good.sort(key=lambda r: (r["pnl"], _pf(r)), reverse=True)
    return good[:top] or sorted(rows, key=lambda r: (r["pnl"], _pf(r)),
                                reverse=True)[:top]


def main():
    t0 = time.time()
    candles = fetch_candles(SYM, TF, BARS)
    if not candles:
        print("拉取 K 线失败", flush=True)
        return
    cbtf: dict[str, list] = {TF: candles}
    for t in BIAS_TFS:
        if t == TF:
            continue
        extra = fetch_candles(SYM, t, BARS_BY_TF[t])
        if extra:
            cbtf[t] = extra
    print(f"K线就绪: {TF}={len(candles)} 根, {ts_fmt(candles[0]['ts'])} ~ {ts_fmt(candles[-1]['ts'])}", flush=True)

    # ── 阶段1：periods × multiplier ──
    periods = list(range(7, 25, 2))        # 7,9,11,13,15,17,19,21,23
    mults = [2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0]
    rows1 = []
    for per in periods:
        for mul in mults:
            p = {"periods": per, "multiplier": mul, "src": "hl2",
                 "change_atr": True, "fast_len": 20, "slow_len": 50, "ma_type": "EMA"}
            r = one(p, make_cfg(), candles, cbtf)
            if r:
                r.update(params=f"{per}×{int(mul)}", er="0.2/0.12/0.3",
                         score="d:80/60/40 m1")
                rows1.append(r)
    show("阶段1 params", rows1)
    top1 = rank(rows1, 8)

    # ── 阶段2：ER 档位（er_min / er_weak_min / er_trend）──
    rows2 = []
    for base in top1:
        per, mul = (int(x) for x in base["params"].split("×"))
        p = {"periods": per, "multiplier": mul, "src": "hl2",
             "change_atr": True, "fast_len": 20, "slow_len": 50, "ma_type": "EMA"}
        for er_min in (0.10, 0.15, 0.20, 0.25, 0.30):
            for er_weak in (0.10, 0.15):
                if er_weak > er_min:
                    continue
                for er_trend in (0.30, 0.40):
                    r = one(p, make_cfg(er_min=er_min, er_weak_min=er_weak,
                                        er_trend=er_trend), candles, cbtf)
                    if r:
                        r.update(params=base["params"],
                                 er=f"{er_min}/{er_weak}/{er_trend}",
                                 score="d:80/60/40 m1")
                        rows2.append(r)
    show("阶段2 ER", rows2)
    top2 = rank(rows2, 5)

    # ── 阶段3：分数拦截（min_score + scoring 阈值）──
    rows3 = []
    for base in top2:
        per, mul = (int(x) for x in base["params"].split("×"))
        er_min, er_weak, er_trend = (float(x) for x in base["er"].split("/"))
        p = {"periods": per, "multiplier": mul, "src": "hl2",
             "change_atr": True, "fast_len": 20, "slow_len": 50, "ma_type": "EMA"}
        for m_score in (0, 1, 2):
            for full in (60.0, 80.0):
                for half in (40.0, 60.0):
                    for alert in (30.0, 40.0):
                        r = one(p, make_cfg(er_min=er_min, er_weak_min=er_weak,
                                            er_trend=er_trend, min_score=m_score,
                                            scoring_full_threshold=full,
                                            scoring_half_threshold=half,
                                            scoring_alert_threshold=alert),
                                candles, cbtf)
                        if r:
                            r.update(params=base["params"], er=base["er"],
                                     score=f"d:{full:g}/{half:g}/{alert:g} m{m_score}")
                            rows3.append(r)
    show("阶段3 分数拦截", rows3)

    final = sorted(rows3, key=lambda x: (x["pnl"], _pf(x)), reverse=True)
    print(f"\n=== SKHYNIX 最优 TOP 10 ===", flush=True)
    for i, x in enumerate(final[:10], 1):
        print(
            f"  {i:2d}. p={x['params']} er={x['er']} score={x['score']} "
            f"| pnl={x['pnl']}U trades={x['trades']} wr={x['wr']}% "
            f"PF={x['pf']} dd={x['dd']}%",
            flush=True,
        )

    out = os.path.join(os.path.dirname(__file__), "_sweep_skhynix.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"stage1": rows1, "stage2": rows2, "stage3": rows3},
                  f, ensure_ascii=False, indent=1)
    print(f"\nWrote {out} | 总耗时 {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
