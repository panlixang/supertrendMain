"""BTC / ETH 1h 超趋参数寻优：线上闸门 + 三级止盈，网格 period×mult。"""
from __future__ import annotations

import copy
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest import run_backtest
from _live_cfg_backtest import (
    BARS, BIAS_TFS, LIVE_URL, exit_rules, fetch_candles, trade_cfg, ts_fmt, _get,
)

GATE_TF = "1h"
PERIODS = list(range(7, 22, 2))
MULTS = list(range(2, 11))
MIN_TRADES = 5


def score_row(pnl: float, dd: float, trades: int) -> float:
    if trades < MIN_TRADES or dd <= 0:
        return -999.0
    return pnl / dd


def sym_1h(sym: dict) -> dict:
    s = copy.deepcopy(sym)
    s["allow_tfs"] = [GATE_TF]
    return s


def run_grid(sym: dict, candles: list, cbtf: dict) -> list[dict]:
    s1h = sym_1h(sym)
    cfg = trade_cfg(s1h)
    rules = exit_rules(sym)
    base_p = sym["params"]
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
                live_gate=cfg, gate_tf=GATE_TF, candles_by_tf=cbtf,
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


def cross_best(btc_rows: list[dict], eth_rows: list[dict]) -> dict | None:
    btc_ok = {f"{r['periods']}×{r['multiplier']}": r for r in btc_rows if r["trades"] >= MIN_TRADES}
    eth_ok = {f"{r['periods']}×{r['multiplier']}": r for r in eth_rows if r["trades"] >= MIN_TRADES}
    common = set(btc_ok) & set(eth_ok)
    if not common:
        return None
    best_key = max(common, key=lambda k: btc_ok[k]["pnl_u"] + eth_ok[k]["pnl_u"])
    return {
        "p": best_key,
        "btc": btc_ok[best_key],
        "eth": eth_ok[best_key],
        "combined_pnl": round(btc_ok[best_key]["pnl_u"] + eth_ok[best_key]["pnl_u"], 2),
    }


def main():
    live = _get(LIVE_URL)
    by_name = {}
    for s in live["symbols"]:
        n = s["symbol"].replace("-USDT-SWAP", "").replace("-USDT", "")
        if n in ("BTC", "ETH"):
            by_name[n] = s

    bars = BARS[GATE_TF]
    out = {}
    all_rows = {}
    for name in ("BTC", "ETH"):
        sym = by_name[name]
        print(f"\n=== {name} fetch {GATE_TF} ===", flush=True)
        candles = fetch_candles(sym["symbol"], GATE_TF, bars)
        cbtf = {GATE_TF: candles}
        for tf in BIAS_TFS:
            if tf == GATE_TF:
                continue
            extra = fetch_candles(sym["symbol"], tf, min(bars, BARS.get(tf, 4500)))
            if extra:
                cbtf[tf] = extra
        start, end = candles[0]["ts"], candles[-1]["ts"]
        rows = run_grid(sym, candles, cbtf)
        all_rows[name] = rows
        export_rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
        cur = next((r for r in export_rows if r["is_current"]), None)
        top8 = [r for r in export_rows if r["trades"] >= MIN_TRADES][:8]
        out[name] = {
            "start": ts_fmt(start),
            "end": ts_fmt(end),
            "bars": len(candles),
            "gate_tf": GATE_TF,
            "filters": "线上 ER/区间/ATR 等不变，allow_tfs→1h",
            "current_15m_params_on_1h": cur,
            "best_score": top8[0] if top8 else None,
            "best_pnl": max(export_rows, key=lambda r: r["pnl_u"]),
            "top8": top8,
            "all": export_rows,
        }
        if cur:
            print(
                f"  沿用15m参数 {cur['periods']}×{cur['multiplier']}: "
                f"{cur['pnl_u']}U dd={cur['max_dd_pct']}% trades={cur['trades']}",
                flush=True,
            )
        if top8:
            b = top8[0]
            print(
                f"  best score {b['periods']}×{b['multiplier']}: "
                f"{b['pnl_u']}U dd={b['max_dd_pct']}% PF={b['profit_factor']}",
                flush=True,
            )

    cross = cross_best(all_rows["BTC"], all_rows["ETH"])
    if cross:
        out["cross_best_pnl"] = cross

    path = os.path.join(os.path.dirname(__file__), "_btc_eth_opt_1h.json")
    slim = {k: {kk: vv for kk, vv in v.items() if kk != "all"} for k, v in out.items() if k != "cross_best_pnl"}
    if cross:
        slim["cross_best_pnl"] = cross
    with open(path, "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False, indent=2)
    with open(path.replace(".json", "_full.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"\nWrote {path}", flush=True)
    print(json.dumps(slim, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
