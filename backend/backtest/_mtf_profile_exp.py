# -*- coding: utf-8 -*-
"""MTF 档位变体实验（Profile 化验证）。

假设：CL 的过度过滤主要由 mtf 稳定反向档=4 造成；把反向档抬高到中性区
（commodity profile），CL 应拿回被误杀的赢家；而 NVDA 保持强反重罚最优
（noise profile）。这证明"同一 feature 不同市场 regime 权重不同"。

变体（mtf 四档 align/neutral/recent_reverse/strong_reverse）：
  v2_base    20/12/8/4   —— v2 现行
  v2_soft    20/12/10/8  —— 反向半罚（commodity 候选）
  v2_neutral 20/12/12/12 —— 反向不罚（极端对照组）
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import replace  # noqa: E402

from backtest import run_backtest  # noqa: E402
from _live_cfg_backtest import exit_rules, trade_cfg, _get, ts_fmt  # noqa: E402

LIVE_URL = "http://47.84.106.154:5174/api/trade/symbols"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data")
CASES = [
    ("NVDA-USDT-SWAP", "NVDA.json", [40.0, 45.0, 50.0]),
    ("CL-USDT-SWAP", "cl_half_cache.json", [45.0, 50.0, 55.0, 60.0]),
    ("SKHYNIX-USDT-SWAP", "SKHYNIX.json", [50.0, 55.0, 60.0]),
]
TF = "1h"
VARIANTS = {
    "v2_base":    dict(mtf_recent_reverse_score=8.0,  mtf_strong_reverse_score=4.0),
    "v2_soft":    dict(mtf_recent_reverse_score=10.0, mtf_strong_reverse_score=8.0),
    "v2_neutral": dict(mtf_recent_reverse_score=12.0, mtf_strong_reverse_score=12.0),
}


def summarize(r):
    return {"pnl": round(r["final"] - 100.0, 2), "dd": r["max_dd_pct"],
            "pf": r["profit_factor"], "wr": r["win_rate"], "n": r["trades"]}


def main():
    live = _get(LIVE_URL)
    out = {}
    for sym_key, fname, thrs in CASES:
        name = sym_key.split("-")[0]
        sym = next(s for s in live["symbols"] if s["symbol"] == sym_key)
        p = sym["params"]
        cfgA = trade_cfg(sym)
        ex = exit_rules(sym)
        cached = json.load(open(os.path.join(DATA, fname), encoding="utf-8"))
        cbtf = {k: v for k, v in cached.items() if isinstance(v, list) and v}
        candles = cbtf[TF]
        common = dict(init_cash=100.0, fee_rate=0.0005, allow_short=True,
                      exit_rules=ex, sizing="fixed",
                      margin_usdt=sym["margin_usdt"], leverage=sym["leverage"],
                      gate_tf=TF, candles_by_tf=cbtf)
        print(f"\n{'=' * 64}\n== {name} [{ts_fmt(candles[0]['ts'])} ~ "
              f"{ts_fmt(candles[-1]['ts'])}]\n{'=' * 64}")
        ra = run_backtest(candles, p, live_gate=cfgA, **common)
        sa = summarize(ra) if "error" not in ra else None
        print(f"  v1 现行(A): pnl={sa['pnl']}U dd={sa['dd']}% PF={sa['pf']} "
              f"wr={sa['wr']}% n={sa['n']}")
        res = {}
        for vname, vpar in VARIANTS.items():
            for thr in thrs:
                cfg_t = replace(cfgA, score_v2=True, **vpar,
                                scoring_full_threshold=thr,
                                scoring_half_threshold=thr,
                                scoring_alert_threshold=max(cfgA.scoring_alert_threshold, thr))
                rt = run_backtest(candles, p, live_gate=cfg_t, **common)
                if "error" not in rt:
                    s = summarize(rt)
                    print(f"  {vname} thr={thr:g}: pnl={s['pnl']}U dd={s['dd']}% "
                          f"PF={s['pf']} wr={s['wr']}% n={s['n']}", flush=True)
                    res[f"{vname}@{thr:g}"] = s
        out[sym_key] = res
    with open(os.path.join(DATA, "_mtf_profile_exp.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    print("\nWrote _mtf_profile_exp.json")


if __name__ == "__main__":
    main()
