# -*- coding: utf-8 -*-
"""ETH 双窗口交叉验证：最新窗口(2026-02~09) vs 历史窗口(2025-09~2026-03)。
对比候选配置在两个窗口的稳定性。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _live_cfg_backtest import fetch_candles, ts_fmt  # noqa: E402
from _sweep_eth import (  # noqa: E402
    SYM, TF, make_cfg, make_exit, fetch_window, parse_ms,
)
from backtest import run_backtest  # noqa: E402

BARS = 4500
W_START, W_END = "2025-09-01", "2026-03-02"

CANDIDATES = [
    ("7×4  er0.12/0.12/0.3（线上当前）", 7, 4.0, dict(er_min=0.12, er_weak_min=0.12, er_trend=0.3)),
    ("7×4  er0.2/0.12/0.25（最新推荐）",  7, 4.0, dict(er_min=0.2,  er_weak_min=0.12, er_trend=0.25)),
    ("5×3  er0.12/0.12/0.25（历史最优）", 5, 3.0, dict(er_min=0.12, er_weak_min=0.12, er_trend=0.25)),
    ("5×3  er0.2/0.12/0.25",              5, 3.0, dict(er_min=0.2,  er_weak_min=0.12, er_trend=0.25)),
    ("9×3  er0.12/0.12/0.25",             9, 3.0, dict(er_min=0.12, er_weak_min=0.12, er_trend=0.25)),
    ("9×3  er0.2/0.12/0.25",              9, 3.0, dict(er_min=0.2,  er_weak_min=0.12, er_trend=0.25)),
]


def bt(candles, per, mul, over, cbtf):
    p = {"periods": per, "multiplier": mul, "src": "hl2", "change_atr": True,
         "fast_len": 20, "slow_len": 50, "ma_type": "EMA"}
    r = run_backtest(
        candles, p, init_cash=100.0, fee_rate=0.0005, allow_short=True,
        exit_rules=make_exit(), sizing="fixed", margin_usdt=10.0, leverage=10,
        live_gate=make_cfg(**over), gate_tf=TF, candles_by_tf=cbtf,
    )
    if "error" in r:
        return None
    return {"pnl": round(r["final"] - 100.0, 2), "trades": r["trades"],
            "wr": r["win_rate"], "pf": r["profit_factor"],
            "dd": r["max_dd_pct"], "blocked": r["er_blocked"]}


def main():
    # 最新窗口数据
    latest = fetch_candles(SYM, TF, BARS)
    hist = fetch_window(SYM, TF, parse_ms(W_START), parse_ms(W_END), 5000)
    print(f"最新窗口: {len(latest)} 根 {ts_fmt(latest[0]['ts'])}~{ts_fmt(latest[-1]['ts'])}")
    print(f"历史窗口: {len(hist)} 根 {ts_fmt(hist[0]['ts'])}~{ts_fmt(hist[-1]['ts'])}")

    print(f"\n{'配置':<28}{'最新窗口(2月~9月)':<34}{'历史窗口(25/9~26/3)':<34}")
    print("-" * 100)
    for name, per, mul, over in CANDIDATES:
        rl = bt(latest, per, mul, over, {TF: latest})
        rh = bt(hist, per, mul, over, {TF: hist})
        def fmt(r):
            if not r:
                return "error"
            return (f"pnl={r['pnl']:+.1f}U n={r['trades']:3d} wr={r['wr']:.0f}% "
                    f"PF={r['pf']} dd={r['dd']:.1f}%")
        print(f"{name:<28}{fmt(rl):<34}{fmt(rh):<34}")


if __name__ == "__main__":
    main()
