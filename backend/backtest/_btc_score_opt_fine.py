"""BTC 15m / 1h 精细扫描：在粗网格最优区附近细扫 ST period/multiplier × 评分阈值。

目标：其他过滤全关，只按 0-100 评分开仓，找收益最高（兼顾 pnl/dd 平衡）的设置。
"""
from __future__ import annotations

import copy
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest
from regime import TradeConfig
from _live_cfg_backtest import (
    BARS, BIAS_TFS, LIVE_URL, exit_rules, fetch_candles, ts_fmt, _get,
)
from _btc_score_opt import score_only_cfg, btc_sym, score_row

MIN_TRADES = 8

# 粗网格最优：15m 13×7.0 / score≥60；1h 11×4.0 / score≥50（best_pnl）
FINE = {
    "15m": {
        "periods": list(range(11, 16)),                       # 11..15
        "mults": [round(x / 10, 1) for x in range(60, 81, 5)],  # 6.0..8.0
        "scores": list(range(50, 71, 5)),                     # 50..70
    },
    "1h": {
        "periods": list(range(9, 14)),                        # 9..13
        "mults": [round(x / 10, 1) for x in range(30, 56, 5)],  # 3.0..5.5
        "scores": list(range(40, 61, 5)),                     # 40..60
    },
}


def run_fine(sym: dict, gate_tf: str, candles: list, cbtf: dict) -> list[dict]:
    cfg_base = score_only_cfg(gate_tf)
    rules = exit_rules(sym)
    base_p = sym["params"]
    cur_p, cur_m = base_p["periods"], base_p["multiplier"]
    grid = FINE[gate_tf]
    rows = []
    total = len(grid["periods"]) * len(grid["mults"]) * len(grid["scores"])
    n = 0
    t0 = time.time()
    for pe in grid["periods"]:
        for m in grid["mults"]:
            p = {**base_p, "periods": pe, "multiplier": m}
            for thr in grid["scores"]:
                n += 1
                r = run_backtest(
                    candles, p,
                    init_cash=100.0, fee_rate=0.0005, allow_short=True,
                    exit_rules=rules,
                    sizing="fixed", margin_usdt=sym["margin_usdt"],
                    leverage=sym["leverage"],
                    live_gate=cfg_base, gate_tf=gate_tf, candles_by_tf=cbtf,
                    score_only_gate=True, min_total_score=float(thr),
                )
                if "error" in r:
                    continue
                pnl = round(r["final"] - 100, 2)
                rows.append({
                    "periods": pe,
                    "multiplier": m,
                    "min_score_100": thr,
                    "pnl_u": pnl,
                    "margin_roi_pct": round(pnl / sym["margin_usdt"] * 100, 1),
                    "max_dd_pct": r["max_dd_pct"],
                    "trades": r["trades"],
                    "win_rate": r["win_rate"],
                    "profit_factor": r["profit_factor"],
                    "blocked": r["er_blocked"],
                    "score": round(score_row(pnl, r["max_dd_pct"], r["trades"]), 3),
                    "is_current_st": pe == cur_p and m == float(cur_m),
                })
                if n % 100 == 0:
                    print(f"  {gate_tf} {n}/{total} …", flush=True)
    rows.sort(key=lambda x: (x["score"], x["pnl_u"]), reverse=True)
    print(f"  {gate_tf} fine done {len(rows)} combos in {time.time()-t0:.0f}s", flush=True)
    return rows


def main():
    live = _get(LIVE_URL)
    sym = btc_sym(live)
    out = {"symbol": sym["symbol"], "filters": "仅0-100打分，无ATR/区间/MTF/ADX/等级/强度硬闸",
           "exit_rules": "线上BTC三级止盈", "by_tf": {}}

    for gate_tf in FINE:
        bars = BARS.get(gate_tf, 4500)
        print(f"\n=== BTC {gate_tf} score-only FINE ===", flush=True)
        candles = fetch_candles(sym["symbol"], gate_tf, bars)
        cbtf = {gate_tf: candles}
        for tf in BIAS_TFS:
            if tf == gate_tf:
                continue
            extra = fetch_candles(sym["symbol"], tf, min(bars, BARS.get(tf, 4500)))
            if extra:
                cbtf[tf] = extra
        rows = run_fine(sym, gate_tf, candles, cbtf)
        ok = [r for r in rows if r["trades"] >= MIN_TRADES]
        best = ok[0] if ok else None
        best_pnl = max(ok, key=lambda r: r["pnl_u"]) if ok else None
        out["by_tf"][gate_tf] = {
            "start": ts_fmt(candles[0]["ts"]),
            "end": ts_fmt(candles[-1]["ts"]),
            "bars": len(candles),
            "best_score": best,
            "best_pnl": best_pnl,
            "top8": ok[:8],
        }
        for label, b in (("best_score", best), ("best_pnl", best_pnl)):
            if b and (label != "best_pnl" or b is not best):
                print(
                    f"  {label}: ST {b['periods']}×{b['multiplier']} score≥{b['min_score_100']} "
                    f"→ {b['pnl_u']}U dd={b['max_dd_pct']}% trades={b['trades']} "
                    f"wr={b['win_rate']}% PF={b['profit_factor']}",
                    flush=True,
                )

    path = os.path.join(os.path.dirname(__file__), "_btc_score_opt_fine.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {path}", flush=True)
    for tf, v in out["by_tf"].items():
        b = v.get("best_pnl")
        if b:
            print(
                f"[{tf}] 收益最高: ST {b['periods']}×{b['multiplier']}, 评分≥{b['min_score_100']} "
                f"→ {b['pnl_u']}U / dd {b['max_dd_pct']}% / {b['trades']}笔 / PF {b['profit_factor']}",
                flush=True,
            )


if __name__ == "__main__":
    main()
