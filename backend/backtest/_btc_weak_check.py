"""BTC 弱档问题核查：er_min=0.15 vs 0.12 的差异 + 弱档单用什么规则出场。

1. 检查线上 BTC exit_rules / exit_rules_quick 配置。
2. 对候选 8×3.5（score 45~65）和当前 11×4 score60，
   分别用 er_min=0.15 和 er_min=0.12 跑，看笔数/弱档单/盈亏差异。
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest
from _btc_score_opt import score_only_cfg
from _live_cfg_backtest import LIVE_URL, exit_rules, _get

from position_enhanced import EnhancedExitRules


def exit_rules_quick(sym: dict) -> EnhancedExitRules:
    r = sym["exit_rules_quick"]
    return EnhancedExitRules(
        enabled=r.get("enabled", True),
        tp1_pct=r["tp1_pct"], tp1_ratio=r["tp1_ratio"],
        tp2_pct=r.get("tp2_pct", 100.0), tp2_ratio=r.get("tp2_ratio", 0.0),
        tp3_pct=r.get("tp3_pct", 100.0), tp3_ratio=r.get("tp3_ratio", 0.0),
        tp3_mode=r.get("tp3_mode", "pct"),
        move_sl_to_entry=r.get("move_sl_to_entry", False),
        sl_mode=r.get("sl_mode", "st"), sl_pct=r.get("sl_pct", 1.0),
        trail_with_st=r.get("trail_with_st", False),
        sl_buffer_atr=r.get("sl_buffer_atr", 0.3),
        sl_min_pct=r.get("sl_min_pct", 1.0),
        protect_profit_at=r.get("protect_profit_at", 50.0),
        protect_trail_pct=r.get("protect_trail_pct", 0.0),
        max_loss_enabled=r.get("max_loss_enabled", False),
        max_loss_pct=r.get("max_loss_pct", 10.0),
    )

GATE_TF = "1h"
DATA_DIR = Path(__file__).parent / "_live_data"

CASES = [
    # (label, periods, mult, score)
    ("8×3.5 s60", 8, 3.5, 60),
    ("8×3.5 s55", 8, 3.5, 55),
    ("8×3.5 s50", 8, 3.5, 50),
    ("8×3.5 s45", 8, 3.5, 45),
    ("8×3.5 s65", 8, 3.5, 65),
    ("11×4.0 s60(当前)", 11, 4.0, 60),
]


def btc_sym(live: dict) -> dict:
    for s in live["symbols"]:
        if "BTC" in s["symbol"]:
            return copy.deepcopy(s)
    raise RuntimeError("no BTC")


def load_cbtf() -> dict:
    with open(DATA_DIR / "BTC.json", "r") as f:
        data = json.load(f)
    if isinstance(data, list):
        return {GATE_TF: data}
    return {k: v for k, v in data.items() if isinstance(v, list) and k in ("15m", "1h", "4h", "1d")}


def run_one(sym: dict, cbtf: dict, pe: int, m: float, thr: float,
            er_min: float, er_weak_min: float, with_quick: bool) -> dict:
    p = {**sym["params"], "periods": pe, "multiplier": float(m)}
    cfg = score_only_cfg(GATE_TF)
    cfg.er_min = er_min
    cfg.er_weak_min = er_weak_min
    cfg.quick_enabled = False
    cfg.use_dynamic_threshold = sym.get("use_dynamic_threshold", True)
    cfg.er_hide_below = sym.get("er_hide_below", 0.0)
    kwargs = dict(
        candles=cbtf[GATE_TF], p=p,
        init_cash=100.0, fee_rate=0.0005, allow_short=True,
        exit_rules=exit_rules(sym),
        sizing="fixed", margin_usdt=sym["margin_usdt"], leverage=sym["leverage"],
        live_gate=cfg, gate_tf=GATE_TF, candles_by_tf=cbtf,
        score_only_gate=True, min_total_score=float(thr),
    )
    if with_quick:
        kwargs["exit_rules_quick"] = exit_rules_quick(sym)
    return run_backtest(**kwargs)


def main():
    live = _get(LIVE_URL)
    sym = btc_sym(live)
    cbtf = load_cbtf()
    print("=== BTC 线上 exit_rules / exit_rules_quick ===", flush=True)
    print("exit_rules:", json.dumps(sym.get("exit_rules"), ensure_ascii=False), flush=True)
    print("exit_rules_quick:", json.dumps(sym.get("exit_rules_quick"), ensure_ascii=False), flush=True)
    print(f"er_min={sym['er_min']} er_weak_min={sym['er_weak_min']} quick={sym['quick_enabled']}", flush=True)

    print(f"\n=== 弱档三口径：裸奔 vs quick规则(真实线上) vs 并入正常档 ===", flush=True)
    print(f"{'参数':18s} {'口径':16s} {'pnl':>7s} {'trades':>4s} {'weak':>4s} {'wr':>6s} {'PF':>6s} {'dd':>6s}", flush=True)
    for label, pe, m, thr in CASES:
        for er_min, with_quick, tag in (
                (0.15, False, "ER0.15 裸奔"),
                (0.15, True,  "ER0.15 quick规则(真实)"),
                (0.12, False, "ER0.12 并入正常档")):
            r = run_one(sym, cbtf, pe, m, thr, er_min, 0.12, with_quick)
            if "error" in r:
                print(f"{label:18s} {tag:16s} ERROR: {r['error']}", flush=True)
                continue
            pnl = r["final"] - r["init_cash"]
            pf = "inf" if r["profit_factor"] is None else f"{r['profit_factor']:.2f}"
            print(f"{label:18s} {tag:16s} {pnl:7.2f} {r['trades']:4d} {r.get('quick_trades', 0):4d} "
                  f"{r['win_rate']:5.1f}% {pf:>6s} {r['max_dd_pct']:6.1f}", flush=True)
        print(flush=True)


if __name__ == "__main__":
    main()
