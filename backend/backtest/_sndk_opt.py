"""SNDK 1h：按线上口径（弱档保留，er_min=0.15 / quick=true，带 quick 规则）
网格寻优 ST(periods×multiplier) × 分数阈值。
数据从本地 _live_data/SNDK.json 加载（避免重复拉线上）。"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest
from position_enhanced import EnhancedExitRules
from _btc_score_opt import score_only_cfg
from _live_cfg_backtest import exit_rules, ts_fmt

LIVE_URL = "http://43.108.10.84:5174/api/trade/symbols"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data")
GATE_TF = "1h"
BIAS_TFS = ("15m", "4h", "1d")
# SNDK 当前 19×2.0 太薄，扩宽网格找收益/回撤平衡点
PERIODS = list(range(7, 29, 2))
MULTS = [x / 2 for x in range(4, 13)]  # 2.0~6.0 步进 0.5
SCORE_THRESHOLDS = list(range(30, 80, 5))
MIN_TRADES = 30


def score_row(pnl: float, dd: float, trades: int) -> float:
    if trades < MIN_TRADES or dd <= 0:
        return -999.0
    return pnl / dd


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "supertrend-bt/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _quick_rules(sym: dict) -> EnhancedExitRules:
    q = sym.get("exit_rules_quick") or {}
    valid = {f.name for f in EnhancedExitRules.__dataclass_fields__.values()}
    return EnhancedExitRules(**{k: v for k, v in q.items() if k in valid})


def main():
    live = _get(LIVE_URL)
    sym = next(s for s in live["symbols"] if s["symbol"].startswith("SNDK"))
    data = json.load(open(os.path.join(DATA_DIR, "SNDK.json"), encoding="utf-8"))
    candles = data[GATE_TF]
    cbtf = {tf: data[tf] for tf in BIAS_TFS if data.get(tf)}
    rules = exit_rules(sym)
    qrules = _quick_rules(sym)
    base_p = sym["params"]
    cur_p, cur_m = base_p["periods"], base_p["multiplier"]
    cur_thr = sym["scoring_full_threshold"]

    cfg_base = score_only_cfg(GATE_TF)
    cfg_base.er_min = sym["er_min"]
    cfg_base.er_weak_min = sym["er_weak_min"]
    cfg_base.quick_enabled = sym["quick_enabled"]
    cfg_base.use_dynamic_threshold = sym["use_dynamic_threshold"]
    cfg_base.er_hide_below = sym["er_hide_below"]

    print(f"=== SNDK {GATE_TF} 线上口径寻优 "
          f"(er_min={sym['er_min']}, weak={sym['er_weak_min']}, "
          f"quick={sym['quick_enabled']}, dynamic={sym['use_dynamic_threshold']}) ===",
          flush=True)
    print(f"当前: {cur_p}×{cur_m} score≥{cur_thr}  bars={len(candles)}", flush=True)

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
                    live_gate=cfg_base, gate_tf=GATE_TF, candles_by_tf=cbtf,
                    score_only_gate=True, min_total_score=float(thr),
                    er_min=sym["er_min"], er_weak_min=sym["er_weak_min"],
                    exit_rules_quick=qrules,
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
                    "is_current_st": (pe == cur_p and float(m) == float(cur_m)
                                       and thr == cur_thr),
                })
                if n % 60 == 0:
                    print(f"  {n}/{total} …", flush=True)
    rows.sort(key=lambda x: (x["score"], x["pnl_u"]), reverse=True)
    print(f"  done {len(rows)} combos in {time.time()-t0:.0f}s", flush=True)

    ok = [r for r in rows if r["trades"] >= MIN_TRADES]
    best = ok[0] if ok else None
    best_pnl = max(ok, key=lambda r: r["pnl_u"]) if ok else None
    cur = next((r for r in rows if r["is_current_st"]), None)

    if cur:
        print(f"\n  当前  {cur_p}×{cur_m} score≥{cur_thr}: "
              f"{cur['pnl_u']}U dd={cur['max_dd_pct']}% trades={cur['trades']} "
              f"PF={cur['profit_factor']}", flush=True)
    if best:
        b = best
        print(f"  best score {b['periods']}×{b['multiplier']} score≥{b['min_score_100']}: "
              f"{b['pnl_u']}U dd={b['max_dd_pct']}% trades={b['trades']} "
              f"PF={b['profit_factor']} wr={b['win_rate']}%", flush=True)
    if best_pnl and best_pnl is not best:
        b = best_pnl
        print(f"  best pnl   {b['periods']}×{b['multiplier']} score≥{b['min_score_100']}: "
              f"{b['pnl_u']}U dd={b['max_dd_pct']}% trades={b['trades']} "
              f"PF={b['profit_factor']} wr={b['win_rate']}%", flush=True)
    print(f"  top10:", flush=True)
    for b in ok[:10]:
        mark = " <=" if b["is_current_st"] else ""
        print(f"    {b['periods']}×{b['multiplier']} score≥{b['min_score_100']}: "
              f"{b['pnl_u']}U dd={b['max_dd_pct']}% trades={b['trades']} "
              f"PF={b['profit_factor']}{mark}", flush=True)

    out = {
        "symbol": sym["symbol"],
        "gate_tf": GATE_TF,
        "start": ts_fmt(candles[0]["ts"]), "end": ts_fmt(candles[-1]["ts"]),
        "bars": len(candles),
        "cfg": {k: sym[k] for k in ("er_min", "er_weak_min", "quick_enabled",
                                    "use_dynamic_threshold",
                                    "scoring_full_threshold")},
        "current": cur,
        "best_score": best,
        "best_pnl": best_pnl,
        "top10": ok[:10],
    }
    with open(os.path.join(os.path.dirname(__file__), "_sndk_opt.json"),
              "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print("Wrote _sndk_opt.json")


if __name__ == "__main__":
    main()
