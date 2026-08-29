"""BTC 15m / 1h：关掉组合过滤器，只按 0-100 置信度打分开仓，网格寻优 ST×分数阈值。"""
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

GATE_TFS = ("15m", "1h")
PERIODS = list(range(7, 22, 2))
MULTS = list(range(2, 11))
SCORE_THRESHOLDS = list(range(35, 85, 5))  # 35,40,...,80
MIN_TRADES = 8


def score_row(pnl: float, dd: float, trades: int) -> float:
    if trades < MIN_TRADES or dd <= 0:
        return -999.0
    return pnl / dd


def score_only_cfg(gate_tf: str) -> TradeConfig:
    """只保留 0-100 打分；ATR/区间/MTF/ADX/等级/强度硬闸全关。"""
    return TradeConfig(
        enabled=True,
        allow_grades=["A", "B", "C"],
        min_score=0,
        allow_tfs=[gate_tf],
        quick_enabled=True,
        atr_filter_enabled=False,
        range_filter_enabled=False,
        mtf_filter_enabled=False,
        adx_filter_enabled=False,
        use_dynamic_threshold=False,
        use_scoring=True,
    )


def btc_sym(live: dict) -> dict:
    for s in live["symbols"]:
        if "BTC" in s["symbol"]:
            return copy.deepcopy(s)
    raise RuntimeError("live symbols 里没有 BTC")


def run_grid(sym: dict, gate_tf: str, candles: list, cbtf: dict) -> list[dict]:
    cfg_base = score_only_cfg(gate_tf)
    rules = exit_rules(sym)
    base_p = sym["params"]
    cur_p, cur_m = base_p["periods"], base_p["multiplier"]
    rows = []
    total = len(PERIODS) * len(MULTS) * len(SCORE_THRESHOLDS)
    n = 0
    t0 = time.time()
    for pe in PERIODS:
        for m in MULTS:
            p = {**base_p, "periods": pe, "multiplier": float(m)}
            for thr in SCORE_THRESHOLDS:
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
                    "multiplier": float(m),
                    "min_score_100": thr,
                    "pnl_u": pnl,
                    "margin_roi_pct": round(pnl / sym["margin_usdt"] * 100, 1),
                    "max_dd_pct": r["max_dd_pct"],
                    "trades": r["trades"],
                    "win_rate": r["win_rate"],
                    "profit_factor": r["profit_factor"],
                    "blocked": r["er_blocked"],
                    "score": round(score_row(pnl, r["max_dd_pct"], r["trades"]), 3),
                    "is_current_st": pe == cur_p and float(m) == float(cur_m),
                })
                if n % 50 == 0:
                    print(f"  {gate_tf} {n}/{total} …", flush=True)
    rows.sort(key=lambda x: (x["score"], x["pnl_u"]), reverse=True)
    print(f"  {gate_tf} done {len(rows)} combos in {time.time()-t0:.0f}s", flush=True)
    return rows


def main():
    live = _get(LIVE_URL)
    sym = btc_sym(live)
    out = {"symbol": sym["symbol"], "filters": "仅0-100打分，无ATR/区间/MTF/ADX/等级/强度硬闸",
           "exit_rules": "线上BTC三级止盈", "by_tf": {}}

    for gate_tf in GATE_TFS:
        bars = BARS.get(gate_tf, 4500)
        print(f"\n=== BTC {gate_tf} score-only ===", flush=True)
        candles = fetch_candles(sym["symbol"], gate_tf, bars)
        cbtf = {gate_tf: candles}
        for tf in BIAS_TFS:
            if tf == gate_tf:
                continue
            extra = fetch_candles(sym["symbol"], tf, min(bars, BARS.get(tf, 4500)))
            if extra:
                cbtf[tf] = extra
        rows = run_grid(sym, gate_tf, candles, cbtf)
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
        if best:
            b = best
            print(
                f"  best score {b['periods']}×{b['multiplier']} score≥{b['min_score_100']}: "
                f"{b['pnl_u']}U dd={b['max_dd_pct']}% trades={b['trades']} PF={b['profit_factor']}",
                flush=True,
            )
        if best_pnl and best_pnl is not best:
            b = best_pnl
            print(
                f"  best pnl   {b['periods']}×{b['multiplier']} score≥{b['min_score_100']}: "
                f"{b['pnl_u']}U dd={b['max_dd_pct']}% trades={b['trades']}",
                flush=True,
            )

    path = os.path.join(os.path.dirname(__file__), "_btc_score_opt.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {path}", flush=True)
    print(json.dumps({k: v for k, v in out.items() if k != "by_tf"}, ensure_ascii=False, indent=2))
    for tf, v in out["by_tf"].items():
        b = v.get("best_score")
        if b:
            print(f"\n[{tf}] 推荐: ST {b['periods']}×{b['multiplier']}, 评分≥{b['min_score_100']} "
                  f"→ {b['pnl_u']}U / dd {b['max_dd_pct']}% / {b['trades']}笔", flush=True)


if __name__ == "__main__":
    main()
