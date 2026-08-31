"""固化 BTC 最终档位 0.5/1.5/3.0 → _btc_tp_opt.json recommend + decision。"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _live_cfg_backtest import BARS, BIAS_TFS, LIVE_URL, _get, fetch_candles, ts_fmt
from _btc_tp_opt import (
    SYMBOL, GATE_TF, BARS_N, trade_cfg_full, exit_rules_of, quick_rules_of, run_one, summarize,
)

FINAL_TP = (0.5, 1.5, 3.0)


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
    s["stable"] = s["half1"]["pnl_u"] > 0 and s["half2"]["pnl_u"] > 0
    s["trade_list"] = r["trade_list"]

    path = os.path.join(os.path.dirname(__file__), "_btc_tp_opt.json")
    with open(path, encoding="utf-8") as f:
        out = json.load(f)
    out["decision"] = {
        "tp": [a, b, c],
        "date": "2026-08-31",
        "why": "score 最高且非边界假象（tp1 外推 0.3/0.4/0.5/0.6/0.7 = 3.0/7.4/9.0/8.3/8.7U，0.5 为真实峰值）。"
               "收益完全分散：全段 top1 仅 12%，去最大笔仍 +8.9U；后半段 wr 84.6% 去 top1 仍 +3.7U。"
               "线上 1.0/2.0/3.5 后半段 -4.18U（wr 53.8%）已失效。tp1=0.5% 为浅止盈高胜率风格，"
               "覆盖手续费 0.1% 后每笔净利仍有 0.4%+。",
    }
    out["recommend"] = s
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("=== FINAL 0.5/1.5/3.0 ===")
    print(json.dumps(s, ensure_ascii=False, indent=2, default=str))
    print(f"\nWrote {path}")

    print("\n=== 推送指令（生产后端执行）===")
    payload = {
        "symbol": SYMBOL,
        "profile": "normal",
        "tp1_pct": a, "tp1_ratio": 30.0,
        "tp2_pct": b, "tp2_ratio": 40.0,
        "tp3_pct": c, "tp3_ratio": 100.0,
    }
    print(f"POST http://43.108.10.84:5174/api/trade/exit-rules")
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
