"""SPCX 当前线上口径（er_min=0.12 / quick关 / 纯分数制）ST 参数寻优。

数据：本地 _live_data/SPCX.json（1h，含 bias 周期），约 6 个月。
网格：periods 7..19 × mult 2.0..6.0 × score 40..65。
输出：按 pnl/dd 打分排序 top 组合 + 当前标记。
"""
from __future__ import annotations

import copy
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest
from _btc_score_opt import score_only_cfg, score_row
from _live_cfg_backtest import LIVE_URL, exit_rules, _get

GATE_TF = "1h"
DATA_DIR = Path(__file__).parent / "_live_data"
ER_MIN = 0.12
PERIODS = list(range(7, 20))            # 7..19
MULTS = [round(x / 10, 1) for x in range(20, 61, 5)]   # 2.0..6.0 步0.5
SCORES = list(range(40, 70, 5))          # 40..65
MIN_TRADES = 8


def spcx_sym(live: dict) -> dict:
    for s in live["symbols"]:
        if "SPCX" in s["symbol"]:
            return copy.deepcopy(s)
    raise RuntimeError("live symbols 里没有 SPCX")


def load_cbtf() -> dict:
    with open(DATA_DIR / "SPCX.json", "r") as f:
        data = json.load(f)
    if isinstance(data, list):
        return {GATE_TF: data}
    return {k: v for k, v in data.items() if isinstance(v, list) and k in ("15m", "1h", "4h", "1d")}


def run_one(sym: dict, cbtf: dict, pe: int, m: float, thr: int) -> dict | None:
    p = {**sym["params"], "periods": pe, "multiplier": float(m)}
    cfg = score_only_cfg(GATE_TF)
    cfg.er_min = ER_MIN
    cfg.er_weak_min = ER_MIN
    cfg.quick_enabled = False
    cfg.use_dynamic_threshold = sym.get("use_dynamic_threshold", True)
    cfg.er_hide_below = sym.get("er_hide_below", 0.0)
    r = run_backtest(
        cbtf[GATE_TF], p,
        init_cash=100.0, fee_rate=0.0005, allow_short=True,
        exit_rules=exit_rules(sym),
        sizing="fixed", margin_usdt=sym["margin_usdt"], leverage=sym["leverage"],
        live_gate=cfg, gate_tf=GATE_TF, candles_by_tf=cbtf,
        score_only_gate=True, min_total_score=float(thr),
    )
    if "error" in r:
        return None
    pnl = round(r["final"] - 100, 2)
    cur_p, cur_m = sym["params"]["periods"], sym["params"]["multiplier"]
    cur_thr = sym["scoring_full_threshold"]
    return {
        "periods": pe,
        "multiplier": float(m),
        "min_score_100": thr,
        "pnl_u": pnl,
        "max_dd_pct": r["max_dd_pct"],
        "trades": r["trades"],
        "win_rate": r["win_rate"],
        "profit_factor": r["profit_factor"],
        "score": round(score_row(pnl, r["max_dd_pct"], r["trades"]), 3),
        "is_current_st": pe == cur_p and float(m) == float(cur_m) and thr == cur_thr,
    }


def main():
    live = _get(LIVE_URL)
    sym = spcx_sym(live)
    cbtf = load_cbtf()
    candles = cbtf[GATE_TF]
    print(f"=== SPCX {GATE_TF} 当前口径(er_min={ER_MIN}, quick=off) 寻优 ===", flush=True)
    print(f"    数据 {len(candles)} 根，{candles[0]['ts']} ~ {candles[-1]['ts']}", flush=True)

    rows = []
    total = len(PERIODS) * len(MULTS) * len(SCORES)
    n = 0
    t0 = time.time()
    for pe in PERIODS:
        for m in MULTS:
            for s in SCORES:
                n += 1
                r = run_one(sym, cbtf, pe, m, s)
                if r:
                    rows.append(r)
                if n % 100 == 0:
                    print(f"  {n}/{total} …", flush=True)
    print(f"  done {len(rows)} combos in {time.time()-t0:.0f}s", flush=True)

    ok = [r for r in rows if r["trades"] >= MIN_TRADES]
    ok.sort(key=lambda x: (x["score"], x["pnl_u"]), reverse=True)

    cur = next((r for r in rows if r.get("is_current_st")), None)
    if cur:
        print(f"\n  当前  {cur['periods']}×{cur['multiplier']} score≥{cur['min_score_100']}: "
              f"{cur['pnl_u']}U dd={cur['max_dd_pct']}% trades={cur['trades']} "
              f"wr={cur['win_rate']}% PF={cur['profit_factor']}", flush=True)

    print("\n  Top 12（按 pnl/dd 打分）:", flush=True)
    for r in ok[:12]:
        mark = "  ← 当前" if r.get("is_current_st") else ""
        print(f"  {r['periods']:3d}×{r['multiplier']:4.1f} score≥{r['min_score_100']:3d} "
              f"→ {r['pnl_u']:7.2f}U dd={r['max_dd_pct']:5.1f}% trades={r['trades']:3d} "
              f"wr={r['win_rate']:5.1f}% PF={r['profit_factor']:5.2f}{mark}", flush=True)

    best_score = ok[0] if ok else None
    best_pnl = max(ok, key=lambda r: r["pnl_u"]) if ok else None
    for label, b in (("best_score", best_score), ("best_pnl", best_pnl)):
        if b:
            print(f"\n  {label}: ST {b['periods']}×{b['multiplier']} score≥{b['min_score_100']} "
                  f"→ {b['pnl_u']}U dd={b['max_dd_pct']}% trades={b['trades']} "
                  f"wr={b['win_rate']}% PF={b['profit_factor']}", flush=True)

    out_path = Path(__file__).parent / "_spcx_reopt.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "symbol": sym["symbol"], "gate_tf": GATE_TF, "er_min": ER_MIN,
            "quick_enabled": False, "bars": len(candles),
            "top12": ok[:12], "best_score": best_score, "best_pnl": best_pnl,
        }, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {out_path}", flush=True)


if __name__ == "__main__":
    main()
