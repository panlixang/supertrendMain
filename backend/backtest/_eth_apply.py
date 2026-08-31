"""固化 ETH 最终档位 0.8/2.0/3.5 → _eth_tp_opt.json recommend。

同时输出推送 /api/trade/exit-rules 所需的 payload（后端启动后执行）。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest
from _live_cfg_backtest import BARS, BIAS_TFS, LIVE_URL, _get, fetch_candles, ts_fmt
from _eth_tp_opt import (
    SYMBOL, GATE_TF, BARS_N, trade_cfg_full, exit_rules_of, quick_rules_of,
    run_one, summarize,
)

FINAL_TP = (0.8, 2.0, 3.5)


def main():
    sym = next(s for s in _get(LIVE_URL)["symbols"] if s["symbol"] == SYMBOL)
    cfg = trade_cfg_full(sym)
    q_rules = quick_rules_of(sym)
    candles = fetch_candles(SYMBOL, GATE_TF, BARS_N)
    cbtf = {GATE_TF: candles}
    for tf in BIAS_TFS:
        if tf == GATE_TF:
            continue
        extra = fetch_candles(SYMBOL, tf, min(len(candles), BARS.get(tf, 4500)))
        if extra:
            cbtf[tf] = extra
    start, end = candles[0]["ts"], candles[-1]["ts"]
    mid = len(candles) // 2

    a, b, c = FINAL_TP
    r = run_one(candles, cbtf, sym, cfg, q_rules, a, b, c)
    s = summarize(sym, r)
    s.update({"tp": [a, b, c], "score": round(s["pnl_u"] / s["max_dd_pct"], 3)})

    # 前后半段
    c1, c2 = candles[:mid], candles[mid:]
    b1 = {GATE_TF: c1}
    b2 = {GATE_TF: c2}
    for tf, extra in cbtf.items():
        if tf == GATE_TF:
            continue
        e1 = [x for x in extra if x["ts"] <= c1[-1]["ts"]]
        e2 = [x for x in extra if x["ts"] > c1[-1]["ts"]]
        if e1:
            b1[tf] = e1
        if e2:
            b2[tf] = e2
    s["half1"] = summarize(sym, run_one(c1, b1, sym, cfg, q_rules, a, b, c))
    s["half2"] = summarize(sym, run_one(c2, b2, sym, cfg, q_rules, a, b, c))
    s["trade_list"] = r["trade_list"]

    path = os.path.join(os.path.dirname(__file__), "_eth_tp_opt.json")
    with open(path, encoding="utf-8") as f:
        out = json.load(f)
    out["decision"] = {
        "tp": [a, b, c],
        "date": "2026-08-31",
        "why": "score 高 + 前后半段均正 + 收益分散（top1 仅占12%，后半段去最大笔仍+3.2U）。"
               "放弃 1.8/2.0：其高收益后半段90%靠一笔9.24U大单，去掉后为负，实盘可复现性差。",
    }
    out["recommend"] = s
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("=== FINAL 0.8/2.0/3.5 ===")
    print(json.dumps(s, ensure_ascii=False, indent=2, default=str))
    print(f"\nWrote {path}")

    print("\n=== 推送指令（后端启动后执行）===")
    payload = {
        "symbol": SYMBOL,
        "profile": "normal",
        "tp1_pct": a, "tp1_ratio": 30.0,
        "tp2_pct": b, "tp2_ratio": 40.0,
        "tp3_pct": c, "tp3_ratio": 100.0,
    }
    print(f"POST /api/trade/exit-rules")
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
