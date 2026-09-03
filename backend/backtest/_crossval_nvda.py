# -*- coding: utf-8 -*-
"""NVDA 候选方案交叉验证：全窗 + 前后半段稳定性 + range 过滤开关对比。
候选来自 _sweep_nvda 各阶段的代表性解，用于防过拟合后最终决策。"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest
from position_enhanced import EnhancedExitRules
from regime import TradeConfig
from _live_cfg_backtest import ts_fmt

SYM = "NVDA-USDT-SWAP"
TF = "1h"
BIAS_TFS = ["15m", "1h", "4h", "1d"]
DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "_live_data", "NVDA.json")

ER = dict(
    enabled=True, tp1_pct=0.5, tp1_ratio=30.0, tp2_pct=3.0, tp2_ratio=40.0,
    tp3_pct=3.5, tp3_ratio=100.0, tp3_mode="reverse_signal",
    move_sl_to_entry=True, sl_mode="st", sl_pct=3.0, trail_with_st=True,
    sl_buffer_atr=0.5, sl_min_pct=1.2, protect_profit_at=1.5,
    protect_trail_pct=0.8, max_loss_enabled=True, max_loss_pct=3.0,
)

BASE_CFG = dict(
    enabled=True, leverage=10, amount_usdt=10.0, er_hide_below=0.1,
    er_weak_min=0.1, er_min=0.12, er_trend=0.35, quick_enabled=False,
    allow_grades=["A", "B", "C"], min_score=0, allow_tfs=[TF],
    cooldown_sec=300, atr_filter_enabled=False, atr_vol_min=0.7,
    range_filter_enabled=True, range_size_max=0.15, range_touches_min=2,
    mtf_filter_enabled=False, mtf_consistency_min=0.6, mtf_flip_max=5,
    adx_filter_enabled=False, adx_min=20.0, adx_period=14,
    use_scoring=True, scoring_full_threshold=40.0,
    scoring_half_threshold=40.0, scoring_alert_threshold=40.0,
    use_dynamic_threshold=True,
)

CANDIDATES = [
    # name, params, er_cfg_over, score_over, exit_over
    ("A 最优 9x2.5 全放行", {"periods": 9, "multiplier": 2.5}, {}, {}, {}),
    ("B 13x2.5 全放行", {"periods": 13, "multiplier": 2.5}, {}, {}, {}),
    ("C 17x4 高PF", {"periods": 17, "multiplier": 4.0}, {}, {}, {}),
    ("D 9x2.5 m1 严分", {"periods": 9, "multiplier": 2.5},
     {}, {"min_score": 1, "scoring_full_threshold": 50.0,
          "scoring_half_threshold": 50.0, "scoring_alert_threshold": 55.0}, {}),
    ("E 9x2.5 er0.2", {"periods": 9, "multiplier": 2.5},
     {"er_min": 0.2}, {}, {}),
    ("F 9x2.5 er0.25", {"periods": 9, "multiplier": 2.5},
     {"er_min": 0.25}, {}, {}),
]


def make_exit(**over) -> EnhancedExitRules:
    return EnhancedExitRules(**{**ER, **over})


def make_cfg(score_over=None, er_over=None, range_on=True) -> TradeConfig:
    c = dict(BASE_CFG)
    c.update(er_over or {})
    c.update(score_over or {})
    c["range_filter_enabled"] = range_on
    return TradeConfig(**c)


def run_one(p, cfg, candles, cbtf, exit_over) -> dict | None:
    r = run_backtest(candles, p, init_cash=100.0, fee_rate=0.0005,
                     allow_short=True, exit_rules=make_exit(**exit_over),
                     sizing="fixed", margin_usdt=10.0, leverage=10,
                     live_gate=cfg, gate_tf=TF, candles_by_tf=cbtf)
    if "error" in r:
        return None
    return {"pnl": round(r["final"] - 100.0, 2), "trades": r["trades"],
            "wr": r["win_rate"], "pf": r["profit_factor"],
            "dd": r["max_dd_pct"]}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        cbtf = {t: v for t, v in json.load(f).items() if t in BIAS_TFS}
    candles = cbtf[TF]
    mid = len(candles) // 2
    h1 = {k: [c for c in v if c["ts"] <= candles[mid - 1]["ts"]]
          for k, v in cbtf.items()}
    h2 = {k: [c for c in v if c["ts"] > candles[mid - 1]["ts"]]
          for k, v in cbtf.items()}
    print(f"NVDA 1h {len(candles)} 根 "
          f"{ts_fmt(candles[0]['ts'])} ~ {ts_fmt(candles[-1]['ts'])}", flush=True)
    print(f"  H1 {len(h1[TF])} 根 ~ H2 {len(h2[TF])} 根", flush=True)

    header = f"{'方案':<22}{'range':<6} {'full':>9} {'H1':>9} {'H2':>9} 稳定"
    print("\n" + header)
    for name, p, er_over, score_over, exit_over in CANDIDATES:
        for range_on, rtag in ((True, "on"), (False, "off")):
            cfg = make_cfg(score_over, er_over, range_on)
            full = run_one(p, cfg, candles, cbtf, exit_over)
            r1 = run_one(p, cfg, h1[TF], h1, exit_over)
            r2 = run_one(p, cfg, h2[TF], h2, exit_over)
            if not full:
                print(f"  {name:<22}{rtag:<6} error")
                continue
            ok = bool(r1 and r2 and r1["pnl"] > 0 and r2["pnl"] > 0)
            fmt = (f"{name:<22}{rtag:<6} "
                   f"{full['pnl']:>6.1f}U/{full['trades']:>3} "
                   f"{r1['pnl']:>6.1f}U/{r1['trades']:>3} "
                   f"{r2['pnl']:>6.1f}U/{r2['trades']:>3}  "
                   f"{'Y' if ok else 'N'}")
            print(fmt, flush=True)


if __name__ == "__main__":
    main()
