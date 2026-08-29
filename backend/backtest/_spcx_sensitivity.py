"""SPCX 参数灵敏度：验证 8×5.0 是否"太迟钝"。

对比：固定 periods 8 扫 multiplier(3.0/4.0/5.0/5.5/6.0)，
     以及固定 multiplier 5.0 扫 periods(6/8/10/12)，
     看笔数/持仓/胜率/PF/回撤。
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

GATE_TF = "1h"
DATA_DIR = Path(__file__).parent / "_live_data"
ER_MIN = 0.12
THR = 45.0

CASES = [
    # (label, periods, multiplier)
    ("8×3.0", 8, 3.0),
    ("8×4.0", 8, 4.0),
    ("8×5.0  ← 新线上", 8, 5.0),
    ("8×5.5", 8, 5.5),
    ("8×6.0", 8, 6.0),
    ("6×5.0", 6, 5.0),
    ("10×5.0", 10, 5.0),
    ("12×5.0", 12, 5.0),
    ("14×3.0  ← 旧线上", 14, 3.0),
]


def spcx_sym(live: dict) -> dict:
    for s in live["symbols"]:
        if "SPCX" in s["symbol"]:
            return copy.deepcopy(s)
    raise RuntimeError("no SPCX")


def load_cbtf() -> dict:
    with open(DATA_DIR / "SPCX.json", "r") as f:
        data = json.load(f)
    if isinstance(data, list):
        return {GATE_TF: data}
    return {k: v for k, v in data.items() if isinstance(v, list) and k in ("15m", "1h", "4h", "1d")}


def run_one(sym: dict, cbtf: dict, pe: int, m: float) -> dict:
    p = {**sym["params"], "periods": pe, "multiplier": float(m)}
    cfg = score_only_cfg(GATE_TF)
    cfg.er_min = ER_MIN
    cfg.er_weak_min = ER_MIN
    cfg.quick_enabled = False
    cfg.use_dynamic_threshold = sym.get("use_dynamic_threshold", True)
    cfg.er_hide_below = sym.get("er_hide_below", 0.0)
    r = run_backtest(
        cbtf[GATE_TF], p,
        init_cash=100.0, fee_rate=0.0005, allow_short=True,
        exit_rules=exit_rules(sym),
        sizing="fixed", margin_usdt=sym["margin_usdt"], leverage=sym["leverage"],
        live_gate=cfg, gate_tf=GATE_TF, candles_by_tf=cbtf,
        score_only_gate=True, min_total_score=THR,
    )
    if "error" in r:
        return {"error": r["error"]}
    return r


def main():
    live = _get(LIVE_URL)
    sym = spcx_sym(live)
    cbtf = load_cbtf()
    print(f"=== SPCX score≥{THR} 参数灵敏度（1h，{len(cbtf[GATE_TF])} 根）===", flush=True)
    print(f"{'参数':14s} {'pnl':>7s} {'trades':>5s} {'hold_h':>7s} {'wr':>6s} "
          f"{'PF':>6s} {'dd':>6s} {'tp1':>4s} {'tp2':>4s} {'tp3':>4s} {'stop':>4s} {'rev':>4s}", flush=True)
    for label, pe, m in CASES:
        r = run_one(sym, cbtf, pe, m)
        if "error" in r:
            print(f"{label:14s} ERROR: {r['error']}", flush=True)
            continue
        pnl = r["final"] - r["init_cash"]
        hold_h = r.get("avg_hold_hours")
        if hold_h is None:
            hold_h = r.get("hold_pct")
            hold_s = "  -"
        else:
            hold_s = f"{hold_h:6.1f}"
        print(f"{label:14s} {pnl:7.2f} {r['trades']:5d} {hold_s:>7s} {r['win_rate']:5.1f} "
              f"{r['profit_factor']:6.2f} {r['max_dd_pct']:6.1f} "
              f"{r.get('tp1_count',0):4d} {r.get('tp2_count',0):4d} {r.get('tp3_count',0):4d} "
              f"{r['stop_count']:4d} {r['reverse_count']:4d}", flush=True)


if __name__ == "__main__":
    main()
