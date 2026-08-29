"""MU-USDT-SWAP 15m / 1h 超趋参数寻优（线上 live_gate + 三级止盈）。"""
from __future__ import annotations

import copy
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

SYMBOL = "MU-USDT-SWAP"
PERIODS = list(range(7, 22, 2))
MULTS = list(range(2, 11))
MIN_TRADES = {"15m": 8, "1h": 5}


def score_row(pnl: float, dd: float, trades: int, min_t: int) -> float:
    if trades < min_t or dd <= 0:
        return -999.0
    return pnl / dd


def mu_sym() -> dict:
    live = _get(LIVE_URL)
    return copy.deepcopy(next(s for s in live["symbols"] if "MU" in s["symbol"]))


def run_grid(sym: dict, gate_tf: str, candles: list, cbtf: dict) -> list[dict]:
    s = copy.deepcopy(sym)
    s["allow_tfs"] = [gate_tf]
    cfg = trade_cfg(s)
    rules = exit_rules(s)
    base_p = sym["params"]
    cur_p, cur_m = base_p["periods"], base_p["multiplier"]
    min_t = MIN_TRADES[gate_tf]
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
                "score": round(score_row(pnl, r["max_dd_pct"], r["trades"], min_t), 3),
                "is_current": pe == cur_p and float(m) == float(cur_m),
            })
            if n % 18 == 0:
                print(f"  {gate_tf} {n}/{total} …", flush=True)
    rows.sort(key=lambda x: x["score"], reverse=True)
    print(f"  {gate_tf} done {len(rows)} combos in {time.time()-t0:.0f}s", flush=True)
    return rows


def pack(gate_tf: str, rows: list, start: int, end: int, bars: int) -> dict:
    min_t = MIN_TRADES[gate_tf]
    qualified = [r for r in rows if r["trades"] >= min_t]
    return {
        "gate_tf": gate_tf,
        "start": ts_fmt(start),
        "end": ts_fmt(end),
        "bars": bars,
        "min_trades": min_t,
        "current": next((r for r in rows if r["is_current"]), None),
        "best_score": qualified[0] if qualified else None,
        "best_pnl": max(rows, key=lambda r: r["pnl_u"]) if rows else None,
        "top8": qualified[:8],
        "all": rows,
    }


def cross_best(a: dict, b: dict) -> dict | None:
    def idx(rows, min_t):
        return {f"{r['periods']}×{r['multiplier']}": r for r in rows if r["trades"] >= min_t}
    ia = idx(a["all"], MIN_TRADES["15m"])
    ib = idx(b["all"], MIN_TRADES["1h"])
    common = set(ia) & set(ib)
    if not common:
        return None
    best = max(common, key=lambda k: ia[k]["pnl_u"] + ib[k]["pnl_u"])
    return {"p": best, "15m": ia[best], "1h": ib[best],
            "combined_pnl": round(ia[best]["pnl_u"] + ib[best]["pnl_u"], 2)}


def main():
    sym = mu_sym()
    out = {
        "symbol": SYMBOL,
        "filters": "线上 MU：ER/ATR/区间/ADX + A/B/C score≥1 + 三级止盈 10U×10x",
    }
    all_by_tf = {}

    for gate_tf, bar_n in (("15m", BARS["15m"]), ("1h", BARS["1h"])):
        print(f"\n=== MU fetch {gate_tf} ===", flush=True)
        candles = fetch_candles(SYMBOL, gate_tf, bar_n)
        cbtf = {gate_tf: candles}
        for tf in BIAS_TFS:
            if tf == gate_tf:
                continue
            extra = fetch_candles(SYMBOL, tf, min(len(candles), BARS.get(tf, 4500)))
            if extra:
                cbtf[tf] = extra
        rows = run_grid(sym, gate_tf, candles, cbtf)
        all_by_tf[gate_tf] = pack(
            gate_tf, rows, candles[0]["ts"], candles[-1]["ts"], len(candles)
        )
        cur = all_by_tf[gate_tf]["current"]
        b = all_by_tf[gate_tf]["best_score"]
        if cur:
            print(f"  当前 {cur['periods']}×{cur['multiplier']}: {cur['pnl_u']}U dd={cur['max_dd_pct']}% "
                  f"trades={cur['trades']} PF={cur['profit_factor']}", flush=True)
        if b:
            print(f"  best {b['periods']}×{b['multiplier']}: {b['pnl_u']}U dd={b['max_dd_pct']}% "
                  f"PF={b['profit_factor']} trades={b['trades']}", flush=True)

    cross = cross_best(all_by_tf["15m"], all_by_tf["1h"])
    out["15m"] = {k: v for k, v in all_by_tf["15m"].items() if k != "all"}
    out["1h"] = {k: v for k, v in all_by_tf["1h"].items() if k != "all"}
    if cross:
        out["cross_best_pnl"] = cross

    path = os.path.join(os.path.dirname(__file__), "_mu_opt.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    with open(path.replace(".json", "_full.json"), "w", encoding="utf-8") as f:
        json.dump({**out, "15m": all_by_tf["15m"], "1h": all_by_tf["1h"]}, f, ensure_ascii=False)
    print(f"\nWrote {path}", flush=True)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
