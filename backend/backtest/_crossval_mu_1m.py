# -*- coding: utf-8 -*-
"""MU 月度交叉验证：从 2026-03 起，逐月对比两套参数。

A. 推荐 (periods=16, mult=3.0, er 0.15/0.10/0.25)
B. 线上   (periods=18, mult=3.0, er 0.20/0.15/0.25)

数据用本地 _live_data/MU.json（_refresh_data.py 刷新到最新）；
每个月窗口用该自然月的 1h K线，bias 周期保留到月末的全部历史（无未来泄漏）。
出场规则固定为线上 MU 增强档。
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest  # noqa: E402
from position_enhanced import EnhancedExitRules  # noqa: E402
from regime import TradeConfig  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data", "MU.json")
TF = "1h"
BIAS_TFS = ["15m", "1h", "4h", "1d"]
YEAR = 2026
MONTHS = [3, 4, 5, 6, 7, 8]     # 数据自 03-04 起；09 月不满月跳过

# 线上 MU 增强档出场规则（固定）
ER = dict(
    enabled=True, tp1_pct=1.5, tp1_ratio=30.0, tp2_pct=2.5, tp2_ratio=40.0,
    tp3_pct=4.0, tp3_ratio=100.0, tp3_mode="reverse_signal",
    move_sl_to_entry=True, sl_mode="st", sl_pct=3.0, trail_with_st=True,
    sl_buffer_atr=0.5, sl_min_pct=1.2, protect_profit_at=1.5,
    protect_trail_pct=0.8, max_loss_enabled=True, max_loss_pct=3.0,
)

BASE_CFG = dict(
    enabled=True, leverage=10, amount_usdt=10.0, er_hide_below=0.1,
    er_weak_min=0.15, er_min=0.2, er_trend=0.25, quick_enabled=False,
    allow_grades=["A", "B", "C"], min_score=0, allow_tfs=[TF],
    cooldown_sec=300, atr_filter_enabled=False, atr_vol_min=0.75,
    range_filter_enabled=False, range_size_max=0.15, range_touches_min=2,
    mtf_filter_enabled=False, mtf_consistency_min=0.6, mtf_flip_max=5,
    adx_filter_enabled=False, adx_min=20.0, adx_period=14,
    use_scoring=True, scoring_full_threshold=45.0,
    scoring_half_threshold=45.0, scoring_alert_threshold=35.0,
    use_dynamic_threshold=True,
)

P_BASE = {"src": "hl2", "change_atr": True, "fast_len": 20, "slow_len": 50,
          "ma_type": "EMA"}

CONFIGS = [
    ("A 推荐 16x3 er.15/.10/.25",
     {**P_BASE, "periods": 16, "multiplier": 3.0},
     dict(er_min=0.15, er_weak_min=0.10, er_trend=0.25)),
    ("B 线上 18x3 er.20/.15/.25",
     {**P_BASE, "periods": 18, "multiplier": 3.0},
     dict(er_min=0.20, er_weak_min=0.15, er_trend=0.25)),
]


def make_exit() -> EnhancedExitRules:
    return EnhancedExitRules(**ER)


def make_cfg(**over) -> TradeConfig:
    c = dict(BASE_CFG)
    c.update(over)
    return TradeConfig(**c)


def ts_fmt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def one(p: dict, cfg: TradeConfig, candles, cbtf) -> dict | None:
    r = run_backtest(
        candles, p, init_cash=100.0, fee_rate=0.0005, allow_short=True,
        exit_rules=make_exit(), sizing="fixed", margin_usdt=10.0, leverage=10,
        live_gate=cfg, gate_tf=TF, candles_by_tf=cbtf,
    )
    if "error" in r:
        return None
    return {
        "pnl": round(r["final"] - 100.0, 2),
        "trades": r["trades"], "wr": r["win_rate"], "pf": r["profit_factor"],
        "dd": r["max_dd_pct"], "blocked": r["er_blocked"],
    }


def month_bounds(ym: int, m: int) -> tuple[int, int]:
    start = datetime(ym, m, 1, tzinfo=timezone.utc)
    if m == 12:
        end = datetime(ym + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(ym, m + 1, 1, tzinfo=timezone.utc)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def main():
    t0 = time.time()
    raw = json.load(open(DATA, encoding="utf-8"))
    all_1h = raw.get(TF) or []
    if not all_1h:
        print("no 1h data", flush=True)
        return
    print(f"data 1h={len(all_1h)}  {ts_fmt(all_1h[0]['ts'])} ~ "
          f"{ts_fmt(all_1h[-1]['ts'])}", flush=True)

    all_rows = []
    for m in MONTHS:
        s_ms, e_ms = month_bounds(YEAR, m)
        candles = [c for c in all_1h if s_ms <= c["ts"] < e_ms]
        if not candles:
            print(f"  {YEAR}-{m:02d}: no bars, skip", flush=True)
            continue
        end_ts = candles[-1]["ts"]
        cbtf: dict[str, list] = {TF: candles}
        for t in BIAS_TFS:
            if t == TF:
                continue
            arr = [c for c in (raw.get(t) or []) if c["ts"] <= end_ts]
            if arr:
                cbtf[t] = arr
        label = f"{YEAR}-{m:02d} ({ts_fmt(candles[0]['ts'])[5:]}~{ts_fmt(end_ts)[5:]} {len(candles)}b)"
        print(f"\n=== {label} ===", flush=True)
        print(f"{'config':<28} | {'pnl':>7} | {'n':>3} | {'wr':>6} | {'PF':>6} | {'dd':>6} | {'blk':>3}", flush=True)
        print("-" * 78, flush=True)
        for name, p, over in CONFIGS:
            r = one(p, make_cfg(**over), candles, cbtf)
            if not r:
                print(f"{name:<28} | backtest error", flush=True)
                continue
            row = {"month": f"{YEAR}-{m:02d}", "name": name, "bars": len(candles), **r}
            all_rows.append(row)
            print(f"{name:<28} | {r['pnl']:>7.2f} | {r['trades']:>3} | "
                  f"{r['wr']:>5.1f}% | {r['pf'] if r['pf'] else 999:>6.2f} | "
                  f"{r['dd']:>5.2f}% | {r['blocked']:>3}", flush=True)

    # 汇总
    print("\n=== SUMMARY ===", flush=True)
    for name, _, _ in CONFIGS:
        rs = [r for r in all_rows if r["name"] == name]
        if not rs:
            continue
        tot = sum(r["pnl"] for r in rs)
        wins = sum(1 for r in rs if r["pnl"] > 0)
        avg_pf = sum(r["pf"] if r["pf"] else 999 for r in rs) / len(rs)
        n_tr = sum(r["trades"] for r in rs)
        print(f"{name:<28} | months={len(rs)} win_months={wins}/{len(rs)} "
              f"total_pnl={tot:.2f}U avg_pf={avg_pf:.2f} total_trades={n_tr}",
              flush=True)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_crossval_mu_1m.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(all_rows, f, ensure_ascii=False, indent=1)
    print(f"\nWrote {out} | {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
