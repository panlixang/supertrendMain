"""SPCX 1h 超趋参数寻优：线上闸门 + 三级止盈，网格 period×mult。"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest import run_backtest
from _live_cfg_backtest import (
    BIAS_TFS, LIVE_URL, exit_rules, fetch_candles, trade_cfg, ts_fmt, _get,
)

GATE_TF = "1h"
PERIODS = list(range(7, 22, 2))
MULTS = list(range(2, 11))
MIN_TRADES = 4  # 品种上市短，样本笔数少


def score_row(pnl: float, dd: float, trades: int) -> float:
    if trades < MIN_TRADES or dd <= 0:
        return -999.0
    return pnl / dd


def run_grid(sym: dict, candles: list, cbtf: dict) -> list[dict]:
    cfg = trade_cfg(sym)
    rules = exit_rules(sym)
    base_p = sym["params"]
    cur_p, cur_m = base_p["periods"], base_p["multiplier"]
    rows = []
    total = len(PERIODS) * len(MULTS)
    n = 0
    t0 = time.time()
    for pe in PERIODS:
        for m in MULTS:
            n += 1
            p = {**base_p, "periods": pe, "multiplier": float(m)}
            r = run_backtest(
                candles, p,
                init_cash=100.0, fee_rate=0.0005, allow_short=True,
                exit_rules=rules,
                sizing="fixed", margin_usdt=sym["margin_usdt"],
                leverage=sym["leverage"],
                live_gate=cfg, gate_tf=GATE_TF, candles_by_tf=cbtf,
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
                "is_current": pe == cur_p and float(m) == float(cur_m),
            })
            if n % 18 == 0:
                print(f"  SPCX {n}/{total} …", flush=True)
    rows.sort(key=lambda x: x["score"], reverse=True)
    print(f"  SPCX done {len(rows)} combos in {time.time()-t0:.0f}s", flush=True)
    return rows


def main():
    live = _get(LIVE_URL)
    sym = next(s for s in live["symbols"] if "SPCX" in s["symbol"])

    # 尽量拉满上市以来的 1h K 线
    bars = 5000
    print(f"\n=== SPCX fetch {GATE_TF} ===", flush=True)
    candles = fetch_candles(sym["symbol"], GATE_TF, bars)
    cbtf = {GATE_TF: candles}
    for tf in BIAS_TFS:
        if tf == GATE_TF:
            continue
        extra = fetch_candles(sym["symbol"], tf, min(len(candles), 4500))
        if extra:
            cbtf[tf] = extra

    start, end = candles[0]["ts"], candles[-1]["ts"]
    rows = run_grid(sym, candles, cbtf)
    export = [{k: v for k, v in r.items()} for r in rows]
    cur = next((r for r in export if r["is_current"]), None)
    qualified = [r for r in export if r["trades"] >= MIN_TRADES]
    top8 = qualified[:8]

    out = {
        "symbol": sym["symbol"],
        "start": ts_fmt(start),
        "end": ts_fmt(end),
        "bars": len(candles),
        "gate_tf": GATE_TF,
        "filters": "线上 ER/ATR/区间/ADX + A/B grade score≥2",
        "min_trades": MIN_TRADES,
        "current": cur,
        "best_score": top8[0] if top8 else None,
        "best_pnl": max(export, key=lambda r: r["pnl_u"]),
        "top8": top8,
        "all": export,
    }

    path = os.path.join(os.path.dirname(__file__), "_spcx_opt.json")
    slim = {k: v for k, v in out.items() if k != "all"}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False, indent=2)
    with open(path.replace(".json", "_full.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)

    if cur:
        print(
            f"  当前 {cur['periods']}×{cur['multiplier']}: "
            f"{cur['pnl_u']}U dd={cur['max_dd_pct']}% trades={cur['trades']} PF={cur['profit_factor']}",
            flush=True,
        )
    if top8:
        b = top8[0]
        print(
            f"  best score {b['periods']}×{b['multiplier']}: "
            f"{b['pnl_u']}U dd={b['max_dd_pct']}% PF={b['profit_factor']} trades={b['trades']}",
            flush=True,
        )
    print(f"\nWrote {path}", flush=True)
    print(json.dumps(slim, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
