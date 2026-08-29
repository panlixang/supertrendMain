"""ETH：按线上配置跑基线，然后 er_min=0.12 + 关弱档(拦ER<0.12)，扫描分数线。

- 基线：完全按线上 ETH 配置（er_min=0.15 / er_weak_min=0.12 / quick_enabled=true /
        use_dynamic_threshold=true / scoring_full_threshold=35 / 线上 exit_rules+quick）。
- 改后：er_min=0.12（[0.12,0.15) 并入正常档）、关闭弱档下单（ER<0.12 拦掉）、
        全部走正常档规则（三级止盈+价格止损）。扫 min_total_score 找合适分数线。
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
    sym = next(s for s in live["symbols"] if s["symbol"].startswith("ETH"))
    data = json.load(open(os.path.join(DATA_DIR, "ETH.json"), encoding="utf-8"))
    candles = data[GATE_TF]
    cbtf = {tf: data[tf] for tf in ("15m", "1h", "4h", "1d") if data.get(tf)}
    p = sym["params"]
    rules = exit_rules(sym)

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

    def line(name, r, extra=""):
        print(f"  {name}: pnl={r['final']-r['init_cash']:.2f}U  trades={r['trades']} "
              f"(weak={r['quick_trades']})  wr={r['win_rate']}%  "
              f"PF={r['profit_factor']}  dd={r['max_dd_pct']}%{extra}", flush=True)

    # ── 1. 基线：完全按线上 ETH 配置 ──
    cfg0 = score_only_cfg(GATE_TF)
    cfg0.er_min = sym["er_min"]
    cfg0.er_weak_min = sym["er_weak_min"]
    cfg0.quick_enabled = sym["quick_enabled"]
    cfg0.use_dynamic_threshold = sym["use_dynamic_threshold"]
    cfg0.er_hide_below = sym["er_hide_below"]
    thr0 = sym["scoring_full_threshold"]
    r0 = run(cfg0, thr0, er_min=sym["er_min"], er_weak_min=sym["er_weak_min"],
             exit_rules_quick=_quick_rules(sym))
    print("== 1. 基线：线上配置原样 "
          f"(er_min={sym['er_min']}, weak={sym['er_weak_min']}, quick={sym['quick_enabled']}, "
          f"dynamic={sym['use_dynamic_threshold']}, thr={thr0}) ==")
    line("线上原样", r0)
    print()

    # ── 2. 改后：er_min=0.12 + 关弱档，扫分数 ──
    cfg1 = score_only_cfg(GATE_TF)
    cfg1.er_min = 0.12
    cfg1.er_weak_min = 0.12
    cfg1.quick_enabled = False
    cfg1.use_dynamic_threshold = sym["use_dynamic_threshold"]
    cfg1.er_hide_below = sym["er_hide_below"]

    print("== 2a. 改后：er_min=0.12, 只关弱档(震荡ER<0.12仍下), 全走正常档规则 ==")
    rows_a = []
    for thr in range(15, 75, 5):
        r = run(cfg1, float(thr), block_untradable=False)
        rows_a.append((thr, r))
        line(f"score>={thr}", r)
    best_a = max(rows_a, key=lambda x: x[1]["final"] - x[1]["init_cash"])
    print(f"\n  2a 最佳: score>={best_a[0]}  pnl={best_a[1]['final']-best_a[1]['init_cash']:.2f}U", flush=True)
    print()

    print("== 2b. 改后：er_min=0.12, 关弱档且拦震荡(ER<0.12不下), 全走正常档规则 ==")
    rows_b = []
    for thr in range(15, 75, 5):
        r = run(cfg1, float(thr), block_untradable=True)
        rows_b.append((thr, r))
        line(f"score>={thr}", r)
    best_b = max(rows_b, key=lambda x: x[1]["final"] - x[1]["init_cash"])
    print(f"\n  2b 最佳: score>={best_b[0]}  pnl={best_b[1]['final']-best_b[1]['init_cash']:.2f}U", flush=True)
    print()

    # ── 3. 灵敏度：dynamic 开关对改后口径的影响（2b 用最佳分数） ──
    thr_b = best_b[0]
    cfg2 = score_only_cfg(GATE_TF)
    cfg2.er_min = 0.12
    cfg2.er_weak_min = 0.12
    cfg2.quick_enabled = False
    cfg2.use_dynamic_threshold = False  # 关动态
    cfg2.er_hide_below = sym["er_hide_below"]
    print(f"== 3. 灵敏度：dynamic 开关（2b 口径, score>={thr_b}） ==")
    rb = run(cfg1, float(thr_b), block_untradable=True)
    rn = run(cfg2, float(thr_b), block_untradable=True)
    line(f"dynamic=on (线上)", rb)
    line(f"dynamic=off      ", rn)
    print()

    out = {
        "baseline": {
            "cfg": {k: sym[k] for k in
                    ("er_min", "er_weak_min", "quick_enabled", "use_dynamic_threshold",
                     "scoring_full_threshold", "margin_usdt", "leverage")},
            **{k: r0[k] for k in ("trades", "quick_trades", "win_rate",
                                  "profit_factor", "max_dd_pct")},
            "pnl_u": round(r0["final"] - r0["init_cash"], 2),
            "start": ts_fmt(r0["start_ts"]), "end": ts_fmt(r0["end_ts"]),
        },
        "er012_only_no_quick": {
            "scan": [{"score": thr, "pnl_u": round(r["final"] - r["init_cash"], 2),
                      "trades": r["trades"], "quick_trades": r["quick_trades"],
                      "win_rate": r["win_rate"], "pf": r["profit_factor"],
                      "dd": r["max_dd_pct"]} for thr, r in rows_a],
            "best_score": best_a[0],
            "best_pnl_u": round(best_a[1]["final"] - best_a[1]["init_cash"], 2),
        },
        "er012_no_quick_block_range": {
            "scan": [{"score": thr, "pnl_u": round(r["final"] - r["init_cash"], 2),
                      "trades": r["trades"], "quick_trades": r["quick_trades"],
                      "win_rate": r["win_rate"], "pf": r["profit_factor"],
                      "dd": r["max_dd_pct"]} for thr, r in rows_b],
            "best_score": thr_b,
            "best_pnl_u": round(best_b[1]["final"] - best_b[1]["init_cash"], 2),
            "dynamic_on": {"pnl_u": round(rb["final"] - rb["init_cash"], 2),
                           "trades": rb["trades"]},
            "dynamic_off": {"pnl_u": round(rn["final"] - rn["init_cash"], 2),
                            "trades": rn["trades"]},
        },
    }
    with open(os.path.join(os.path.dirname(__file__), "_eth_live_012.json"),
              "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("Wrote _eth_live_012.json")


if __name__ == "__main__":
    main()
