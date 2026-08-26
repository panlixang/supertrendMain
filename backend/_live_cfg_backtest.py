"""按线上 /api/trade/symbols 配置回测，闸门与 feed 一致（evaluate_enhanced）。"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest import run_backtest
from position_enhanced import EnhancedExitRules
from regime import TradeConfig

LIVE_URL = "http://43.108.10.84:5174/api/trade/symbols"
OKX = ["https://www.okx.com", "https://aws.okx.com"]
OKX_BAR = {"15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D"}
BIAS_TFS = ["15m", "1h", "4h", "1d"]
BARS = {"15m": 9000, "1h": 4500}


def _get(url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers={"User-Agent": "supertrend-bt/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def fetch_candles(symbol: str, tf: str, limit: int) -> list[dict]:
    collected: dict[int, dict] = {}
    bar = OKX_BAR[tf]
    after = None
    while len(collected) < limit:
        page_n = min(300, limit - len(collected))
        endpoint = "history-candles" if after else "candles"
        qs = f"instId={symbol}&bar={bar}&limit={page_n}"
        if after:
            qs += f"&after={after}"
        data = None
        for base in OKX:
            try:
                data = _get(f"{base}/api/v5/market/{endpoint}?{qs}")
                if data and data.get("code") == "0":
                    break
            except Exception:
                continue
        if not data or data.get("code") != "0" or not data.get("data"):
            break
        rows = data["data"]
        if not rows:
            break
        for row in rows:
            try:
                ts = int(row[0])
                collected[ts] = {
                    "ts": ts, "o": float(row[1]), "h": float(row[2]),
                    "l": float(row[3]), "c": float(row[4]), "vol": float(row[5]),
                }
            except (IndexError, ValueError):
                continue
        after = rows[-1][0]
        if len(rows) < page_n:
            break
        time.sleep(0.06)
    out = [collected[k] for k in sorted(collected)]
    return out[-limit:]


def ts_fmt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def trade_cfg(sym: dict) -> TradeConfig:
    return TradeConfig(
        enabled=True,
        leverage=sym["leverage"],
        amount_usdt=sym["margin_usdt"],
        er_hide_below=sym["er_hide_below"],
        er_weak_min=sym["er_weak_min"],
        er_min=sym["er_min"],
        er_trend=sym["er_trend"],
        quick_enabled=sym["quick_enabled"],
        allow_grades=list(sym["allow_grades"]),
        min_score=sym["min_score"],
        allow_tfs=list(sym["allow_tfs"]),
        cooldown_sec=sym["cooldown_sec"],
        atr_filter_enabled=sym["atr_filter_enabled"],
        atr_vol_min=sym["atr_vol_min"],
        range_filter_enabled=sym["range_filter_enabled"],
        range_size_max=sym["range_size_max"],
        range_touches_min=sym["range_touches_min"],
        mtf_filter_enabled=sym["mtf_filter_enabled"],
        mtf_consistency_min=sym["mtf_consistency_min"],
        mtf_flip_max=sym["mtf_flip_max"],
        adx_filter_enabled=sym["adx_filter_enabled"],
        adx_min=sym["adx_min"],
        adx_period=sym["adx_period"],
    )


def exit_rules(sym: dict) -> EnhancedExitRules:
    r = sym["exit_rules"]
    return EnhancedExitRules(
        enabled=r.get("enabled", True),
        tp1_pct=r["tp1_pct"], tp1_ratio=r["tp1_ratio"],
        tp2_pct=r.get("tp2_pct", 2.0), tp2_ratio=r.get("tp2_ratio", 40.0),
        tp3_pct=r.get("tp3_pct", 3.5), tp3_ratio=r.get("tp3_ratio", 100.0),
        tp3_mode=r.get("tp3_mode", "pct"),
        move_sl_to_entry=r.get("move_sl_to_entry", True),
        sl_mode=r.get("sl_mode", "st"), sl_pct=r.get("sl_pct", 2.0),
        trail_with_st=r.get("trail_with_st", True),
        sl_buffer_atr=r.get("sl_buffer_atr", 0.5),
        sl_min_pct=r.get("sl_min_pct", 1.2),
        protect_profit_at=r.get("protect_profit_at", 1.5),
        protect_trail_pct=r.get("protect_trail_pct", 0.8),
        max_loss_enabled=r.get("max_loss_enabled", False),
        max_loss_pct=r.get("max_loss_pct", 10.0),
    )


def main():
    live = _get(LIVE_URL)
    symbols = live["symbols"]
    results = []

    for sym in symbols:
        name = sym["symbol"].replace("-USDT-SWAP", "").replace("-USDT", "")
        gate_tf = sym["allow_tfs"][0]
        bars = BARS.get(gate_tf, 4500)
        print(f"\n=== {name} {gate_tf} live gate ===", flush=True)

        candles = fetch_candles(sym["symbol"], gate_tf, bars)
        cbtf: dict[str, list] = {gate_tf: candles}
        for tf in BIAS_TFS:
            if tf == gate_tf:
                continue
            extra = fetch_candles(sym["symbol"], tf, min(bars, BARS.get(tf, 4500)))
            if extra:
                cbtf[tf] = extra

        p = sym["params"]
        r = run_backtest(
            candles, p,
            init_cash=100.0, fee_rate=0.0005, allow_short=True,
            exit_rules=exit_rules(sym),
            sizing="fixed", margin_usdt=sym["margin_usdt"],
            leverage=sym["leverage"],
            live_gate=trade_cfg(sym),
            gate_tf=gate_tf,
            candles_by_tf=cbtf,
        )
        if "error" in r:
            results.append({"name": name, "error": r["error"]})
            print("  error:", r["error"], flush=True)
            continue

        pnl = round(r["final"] - r["init_cash"], 2)
        row = {
            "name": name,
            "symbol": sym["symbol"],
            "tf": gate_tf,
            "params": f"{p['periods']}×{p['multiplier']}",
            "bars": r["bars"],
            "start": ts_fmt(r["start_ts"]),
            "end": ts_fmt(r["end_ts"]),
            "pnl_u": pnl,
            "margin_roi_pct": round(pnl / sym["margin_usdt"] * 100, 1),
            "hold_pct": r["hold_pct"],
            "max_dd_pct": r["max_dd_pct"],
            "trades": r["trades"],
            "win_rate": r["win_rate"],
            "avg_win_pct": r["avg_win"],
            "avg_loss_pct": r["avg_loss"],
            "profit_factor": r["profit_factor"],
            "payoff": round(abs(r["avg_win"] / r["avg_loss"]), 2)
            if r["avg_loss"] else None,
            "blocked": r["er_blocked"],
            "tp1": r["tp1_count"], "tp2": r.get("tp2_count", 0),
            "tp3": r.get("tp3_count", 0),
            "stops": r["stop_count"], "reverses": r["reverse_count"],
        }
        results.append(row)
        print(
            f"  pnl={pnl}U trades={r['trades']} wr={r['win_rate']}% "
            f"PF={r['profit_factor']} blocked={r['er_blocked']}",
            flush=True,
        )

    out_path = os.path.join(os.path.dirname(__file__), "_live_cfg_backtest.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {out_path}", flush=True)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
