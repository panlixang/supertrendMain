"""对照：BTC/ETH 关掉区间过滤（其余参数同实盘）。"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")
from _live_cfg_backtest import fetch_candles, EXIT, PARAMS_BASE, ts_fmt
from backtest import run_backtest

JOBS = [
    {"name": "BTC", "symbol": "BTC-USDT-SWAP", "tf": "15m", "bars": 9000,
     "periods": 15, "multiplier": 4.0, "er_min": 0.15,
     "atr": False, "atr_min": 0.7, "adx": False},
    {"name": "ETH", "symbol": "ETH-USDT-SWAP", "tf": "15m", "bars": 9000,
     "periods": 17, "multiplier": 3.0, "er_min": 0.16,
     "atr": True, "atr_min": 0.7, "adx": False},
]

out = []
for j in JOBS:
    print(f"=== {j['name']} no-range ===", flush=True)
    c = fetch_candles(j["symbol"], j["tf"], j["bars"])
    r = run_backtest(
        c, {**PARAMS_BASE, "periods": j["periods"], "multiplier": j["multiplier"]},
        init_cash=100, fee_rate=0.0005, allow_short=True,
        er_min=j["er_min"], exit_rules=EXIT, sizing="fixed",
        margin_usdt=10, leverage=10,
        atr_filter_enabled=j["atr"], atr_vol_min=j["atr_min"],
        range_filter_enabled=False,
        adx_filter_enabled=j["adx"],
    )
    pnl = round(r["final"] - 100, 2)
    payoff = None
    if r["avg_loss"] and r["avg_loss"] != 0:
        payoff = round(abs(r["avg_win"] / r["avg_loss"]), 2) if r["avg_win"] else 0
    row = {
        "name": j["name"], "tf": j["tf"],
        "params": f"{j['periods']}×{j['multiplier']}",
        "bars": r["bars"], "start": ts_fmt(r["start_ts"]), "end": ts_fmt(r["end_ts"]),
        "pnl_u": pnl, "margin_roi_pct": round(pnl / 10 * 100, 1),
        "hold_pct": r["hold_pct"], "max_dd_pct": r["max_dd_pct"],
        "trades": r["trades"], "win_rate": r["win_rate"],
        "avg_win_pct": r["avg_win"], "avg_loss_pct": r["avg_loss"],
        "payoff": payoff, "profit_factor": r["profit_factor"],
        "blocked": r["er_blocked"],
        "tp1": r["tp1_count"], "tp2": r.get("tp2_count", 0), "tp3": r.get("tp3_count", 0),
        "reverses": r["reverse_count"],
        "equity": r["equity"],
    }
    out.append(row)
    print(row["name"], "pnl", pnl, "trades", r["trades"], "PF", r["profit_factor"], flush=True)

path = os.path.join(os.path.dirname(__file__), "_live_cfg_norange.json")
with open(path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print("wrote", path)
