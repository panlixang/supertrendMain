"""五币 1h 最优参数的样本外验证：窗口 2025-09-03 ~ 2026-03-03。

1h 最优参数都是在 2026-02/03 ~ 08-29 数据上寻优得到的。
本脚本把各币 1h 最优参数放到更早的 2025-09-03 ~ 2026-03-03（完全样本外）验证，
并加测阈值邻域评估稳健性。次新币（MU/SNDK/SPCX）若该窗口无数据则如实标注。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest
from _live_cfg_backtest import (
    BIAS_TFS, LIVE_URL, exit_rules, fetch_candles, ts_fmt, _get,
)
from _btc_score_opt import score_only_cfg

GATE_TF = "1h"
W_START = datetime(2025, 9, 3, tzinfo=timezone.utc)
W_END = datetime(2026, 3, 4, tzinfo=timezone.utc)  # 半开区间，含 03-03 全天
MIN_BARS = 1000

# 各币 1h 最优（来自之前寻优结果）
BEST = {
    "BTC":  (11, 4.0, 50),
    "ETH":  (7, 4.0, 35),
}
# 各币阈值邻域（验证门槛稳健性）
THR_NEIGHBORHOOD = {
    "BTC":  (40, 45, 55, 60),
    "ETH":  (25, 30, 40, 45),
}


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def slice_window(candles: list, a_ms: int, b_ms: int) -> list:
    return [c for c in candles if a_ms <= c["ts"] < b_ms]


def fetch_window(symbol: str, tf: str, bars: int, a_ms: int, b_ms: int) -> list:
    raw = fetch_candles(symbol, tf, bars)
    return slice_window(raw, a_ms, b_ms)


def run_one(sym: dict, candles: list, cbtf: dict, pe: int, m: float, thr: int) -> dict | None:
    base_p = sym["params"]
    p = {**base_p, "periods": pe, "multiplier": float(m)}
    r = run_backtest(
        candles, p,
        init_cash=100.0, fee_rate=0.0005, allow_short=True,
        exit_rules=exit_rules(sym),
        sizing="fixed", margin_usdt=sym["margin_usdt"], leverage=sym["leverage"],
        live_gate=score_only_cfg(GATE_TF), gate_tf=GATE_TF, candles_by_tf=cbtf,
        score_only_gate=True, min_total_score=float(thr),
    )
    if "error" in r:
        return None
    return {
        "periods": pe, "multiplier": float(m), "min_score_100": thr,
        "pnl_u": round(r["final"] - 100, 2),
        "max_dd_pct": r["max_dd_pct"],
        "trades": r["trades"],
        "win_rate": r["win_rate"],
        "profit_factor": r["profit_factor"],
        "blocked": r["er_blocked"],
    }


def main():
    live = _get(LIVE_URL)
    out = {"note": "五币 1h 最优参数样本外验证 2025-09-03~2026-03-03", "by_symbol": {}}
    a_ms, b_ms = _ms(W_START), _ms(W_END)
    print(f"验证窗口: {W_START.date()} ~ {W_END.date()} (UTC)", flush=True)

    for s in live["symbols"]:
        name = s["symbol"].replace("-USDT-SWAP", "").replace("-USDT", "")
        if name not in BEST:
            continue
        best = BEST[name]
        print(f"\n=== {name} ===", flush=True)
        entry = {"best_params": best, "results": [], "no_data": False}

        candles = fetch_candles(s["symbol"], GATE_TF, 9000)
        w = slice_window(candles, a_ms, b_ms)
        if len(w) < MIN_BARS:
            print(f"  !! {name} 在 2025-09-03~2026-03-03 无足够 1h 数据 "
                  f"({len(w)} 根，最早 {ts_fmt(candles[0]['ts'])} 起)", flush=True)
            entry["no_data"] = True
            entry["data_start"] = ts_fmt(candles[0]["ts"])
            entry["window_bars"] = len(w)
            out["by_symbol"][name] = entry
            continue
        print(f"  窗口 {len(w)} 根 {ts_fmt(w[0]['ts'])}~{ts_fmt(w[-1]['ts'])}", flush=True)

        cbtf: dict[str, list] = {GATE_TF: w}
        for tf in BIAS_TFS:
            if tf == GATE_TF:
                continue
            bars_needed = {"15m": 18000, "4h": 1500, "1d": 300}.get(tf, 1500)
            extra = fetch_window(s["symbol"], tf, bars_needed, a_ms, b_ms)
            if extra:
                cbtf[tf] = extra

        pe, m, thr = best
        # 最优参数
        r = run_one(s, w, cbtf, pe, m, thr)
        if r:
            entry["results"].append(r)
            print(
                f"  BEST {pe}×{m}/≥{thr}: {r['pnl_u']}U / dd {r['max_dd_pct']}% / "
                f"{r['trades']}笔 / wr {r['win_rate']}% / PF {r['profit_factor']}",
                flush=True,
            )
        # 阈值邻域（同 ST，不同门槛）
        for t in THR_NEIGHBORHOOD[name]:
            r = run_one(s, w, cbtf, pe, m, t)
            if r:
                entry["results"].append(r)
        # ST 邻域（阈值保持不变）
        for dp in (-2, -1, 1, 2):
            r = run_one(s, w, cbtf, pe + dp, m, thr)
            if r:
                entry["results"].append(r)
        for dm in (-1.0, -0.5, 0.5, 1.0):
            r = run_one(s, w, cbtf, pe, m + dm, thr)
            if r:
                entry["results"].append(r)

        entry["results"].sort(key=lambda x: x["pnl_u"], reverse=True)
        out["by_symbol"][name] = entry

    path = os.path.join(os.path.dirname(__file__), "_oos_1h_validate.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {path}", flush=True)


if __name__ == "__main__":
    main()
