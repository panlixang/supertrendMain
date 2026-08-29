"""MU/SPCX/SNDK：线上配置基线 vs 方案A(er_min=0.12 关弱档) 对比，并扫分数线。

- 基线：完全按线上配置（er_min/er_weak_min/quick_enabled/use_dynamic_threshold/
        scoring_full_threshold + 线上 exit_rules + exit_rules_quick）。
- 方案A：er_min=0.12, er_weak_min=0.12, quick_enabled=false，ER<0.12 震荡照下
        （打分制不拦 tradable），全走正常档规则。扫分数找合适分数线。
"""
from __future__ import annotations

import json
import os
import sys
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
TAGS = ("MU", "SPCX", "SNDK")


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
    syms = {s["symbol"].replace("-USDT-SWAP", "").replace("-USDT", ""): s
            for s in live["symbols"]}
    out = {}

    for tag in TAGS:
        sym = syms[tag]
        data = json.load(open(os.path.join(DATA_DIR, f"{tag}.json"), encoding="utf-8"))
        candles = data[GATE_TF]
        cbtf = {tf: data[tf] for tf in ("15m", "1h", "4h", "1d") if data.get(tf)}
        p = sym["params"]
        rules = exit_rules(sym)
        thr_live = sym["scoring_full_threshold"]

        def run(cfg, thr, **kw):
            kwargs = dict(
                init_cash=100.0, fee_rate=0.0005, allow_short=True,
                exit_rules=rules,
                sizing="fixed", margin_usdt=sym["margin_usdt"], leverage=sym["leverage"],
                live_gate=cfg, gate_tf=GATE_TF, candles_by_tf=cbtf,
                score_only_gate=True, min_total_score=thr,
            )
            kwargs.update(kw)
            return run_backtest(candles, p, **kwargs)

        def line(name, r):
            print(f"  {name}: pnl={r['final']-r['init_cash']:.2f}U  trades={r['trades']} "
                  f"(weak={r['quick_trades']})  wr={r['win_rate']}%  "
                  f"PF={r['profit_factor']}  dd={r['max_dd_pct']}%", flush=True)

        print(f"=== {tag} (1h, thr={thr_live}, "
              f"er_min={sym['er_min']}, weak={sym['er_weak_min']}, "
              f"quick={sym['quick_enabled']}, dynamic={sym['use_dynamic_threshold']}) ===",
              flush=True)

        # 1. 线上基线
        cfg0 = score_only_cfg(GATE_TF)
        cfg0.er_min = sym["er_min"]
        cfg0.er_weak_min = sym["er_weak_min"]
        cfg0.quick_enabled = sym["quick_enabled"]
        cfg0.use_dynamic_threshold = sym["use_dynamic_threshold"]
        cfg0.er_hide_below = sym["er_hide_below"]
        r0 = run(cfg0, float(thr_live), er_min=sym["er_min"],
                 er_weak_min=sym["er_weak_min"],
                 exit_rules_quick=_quick_rules(sym))
        print("  [线上基线]")
        line(f"原配置(带弱档规则)", r0)

        # 2. 方案A：er_min=0.12 关弱档，震荡照下，扫分数
        cfg1 = score_only_cfg(GATE_TF)
        cfg1.er_min = 0.12
        cfg1.er_weak_min = 0.12
        cfg1.quick_enabled = False
        cfg1.use_dynamic_threshold = sym["use_dynamic_threshold"]
        cfg1.er_hide_below = sym["er_hide_below"]

        print("  [方案A er_min=0.12 关弱档, 震荡照下]")
        rows = []
        for thr in range(15, 75, 5):
            r = run(cfg1, float(thr), block_untradable=False)
            rows.append((thr, r))
            line(f"score>={thr}", r)
        best = max(rows, key=lambda x: x[1]["final"] - x[1]["init_cash"])
        rA = run(cfg1, float(thr_live), block_untradable=False)
        print(f"  线上分数不变 score>={thr_live}:", flush=True)
        line("", rA)
        print(f"  扫分最佳: score>={best[0]}", flush=True)
        line("", best[1])
        print(flush=True)

        out[tag] = {
            "cfg": {k: sym[k] for k in ("er_min", "er_weak_min", "quick_enabled",
                                        "use_dynamic_threshold",
                                        "scoring_full_threshold")},
            "baseline": {**{k: r0[k] for k in ("trades", "quick_trades",
                                               "win_rate", "profit_factor",
                                               "max_dd_pct")},
                         "pnl_u": round(r0["final"] - r0["init_cash"], 2),
                         "start": ts_fmt(r0["start_ts"]), "end": ts_fmt(r0["end_ts"])},
            "planA_live_thr": {**{k: rA[k] for k in ("trades", "quick_trades",
                                                     "win_rate", "profit_factor",
                                                     "max_dd_pct")},
                               "pnl_u": round(rA["final"] - rA["init_cash"], 2)},
            "planA_scan": [{"score": thr,
                            "pnl_u": round(r["final"] - r["init_cash"], 2),
                            "trades": r["trades"], "quick_trades": r["quick_trades"],
                            "win_rate": r["win_rate"], "pf": r["profit_factor"],
                            "dd": r["max_dd_pct"]} for thr, r in rows],
            "planA_best_score": best[0],
            "planA_best_pnl_u": round(best[1]["final"] - best[1]["init_cash"], 2),
        }

    with open(os.path.join(os.path.dirname(__file__), "_others_quick_check.json"),
              "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("Wrote _others_quick_check.json")


if __name__ == "__main__":
    main()
