"""BTC / ETH 15m 超趋参数寻优：线上闸门 + 三级止盈，网格 period×mult。"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest
from _live_cfg_backtest import (
    BARS, BIAS_TFS, LIVE_URL, exit_rules, fetch_candles, trade_cfg, ts_fmt, _get,
)

PERIODS = list(range(7, 22, 2))          # 7,9,...,21
MULTS = [round(x * 0.5, 1) for x in range(4, 21)]  # 2.0 .. 10.0 step 0.5 → 17 values
# 或整数步：list(range(2, 11))  # 2..10 共 9 个 → 8*9=72
MULTS = list(range(2, 11))
MIN_TRADES = 8


def score_row(pnl: float, dd: float, trades: int) -> float:
    if trades < MIN_TRADES or dd <= 0:
        return -999.0
    return pnl / dd


def run_grid(sym: dict, candles: list, cbtf: dict) -> list[dict]:
    gate_tf = sym["allow_tfs"][0]
    base_p = sym["params"]
    cfg = trade_cfg(sym)
    rules = exit_rules(sym)
    name = sym["symbol"].replace("-USDT-SWAP", "")
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
                live_gate=cfg, gate_tf=gate_tf, candles_by_tf=cbtf,
            )
            if "error" in r:
                continue
            pnl = round(r["final"] - 100, 2)
            row = {
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
            }
            rows.append(row)
            if n % 12 == 0:
                print(f"  {name} {n}/{total} …", flush=True)
    rows.sort(key=lambda x: x["score"], reverse=True)
    print(f"  {name} done {len(rows)} combos in {time.time()-t0:.0f}s", flush=True)
    return rows


def summarize(name: str, rows: list[dict], sym: dict) -> dict:
    cur = next((r for r in rows if r["is_current"]), None)
    top = [r for r in rows if r["trades"] >= MIN_TRADES][:5]
    best_pnl = max(rows, key=lambda r: r["pnl_u"])
    return {
        "name": name,
        "symbol": sym["symbol"],
        "window": f"{ts_fmt(rows[0]['_start'])} → {ts_fmt(rows[0]['_end'])}"
        if rows and "_start" in rows[0] else "",
        "current": cur,
        "best_score": top[0] if top else None,
        "best_pnl": best_pnl,
        "top5": top,
        "grid_size": len(rows),
    }


def main():
    live = _get(LIVE_URL)
    by_name = {}
    for s in live["symbols"]:
        n = s["symbol"].replace("-USDT-SWAP", "").replace("-USDT", "")
        if n in ("BTC", "ETH"):
            by_name[n] = s

    out = {}
    for name in ("BTC", "ETH"):
        sym = by_name[name]
        gate_tf = "15m"
        print(f"\n=== {name} fetch {gate_tf} ===", flush=True)
        candles = fetch_candles(sym["symbol"], gate_tf, BARS["15m"])
        cbtf = {gate_tf: candles}
        for tf in BIAS_TFS:
            if tf == gate_tf:
                continue
            extra = fetch_candles(sym["symbol"], tf, min(BARS["15m"], BARS.get(tf, 4500)))
            if extra:
                cbtf[tf] = extra
        start, end = candles[0]["ts"], candles[-1]["ts"]
        rows = run_grid(sym, candles, cbtf)
        for r in rows:
            r["_start"], r["_end"] = start, end
        # strip internal keys from export
        export_rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
        cur = next((r for r in export_rows if r["is_current"]), None)
        top5 = [r for r in export_rows if r["trades"] >= MIN_TRADES][:8]
        out[name] = {
            "start": ts_fmt(start),
            "end": ts_fmt(end),
            "bars": len(candles),
            "current": cur,
            "best_score": top5[0] if top5 else None,
            "best_pnl": max(export_rows, key=lambda r: r["pnl_u"]),
            "top8": top5,
            "all": export_rows,
        }
        print(f"  current {cur['periods']}×{cur['multiplier']}: "
              f"{cur['pnl_u']}U dd={cur['max_dd_pct']}% trades={cur['trades']}", flush=True)
        if top5:
            b = top5[0]
            print(f"  best score {b['periods']}×{b['multiplier']}: "
                  f"{b['pnl_u']}U dd={b['max_dd_pct']}% PF={b['profit_factor']}", flush=True)

    path = os.path.join(os.path.dirname(__file__), "_btc_eth_opt.json")
    slim = {k: {kk: vv for kk, vv in v.items() if kk != "all"} for k, v in out.items()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False, indent=2)
    # full grid separately (large)
    with open(path.replace(".json", "_full.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"\nWrote {path}", flush=True)
    print(json.dumps(slim, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
