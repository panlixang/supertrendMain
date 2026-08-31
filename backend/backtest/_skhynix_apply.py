"""固化 SKHYNIX 最终档位 1.2/1.5/3.5 → _skhynix_tp_opt.json recommend + decision。"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _live_cfg_backtest import BARS, BIAS_TFS, _get, fetch_candles, ts_fmt
from _skhynix_tp_opt import (
    SYMBOL, LIVE2, GATE_TF, BARS_N, trade_cfg_full, exit_rules_of, quick_rules_of,
    run_one, summarize,
)

FINAL_TP = (1.2, 1.5, 3.5)


def main():
    sym = next(s for s in _get(LIVE2)["symbols"] if s["symbol"] == SYMBOL)
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

    path = os.path.join(os.path.dirname(__file__), "_skhynix_tp_opt.json")
    with open(path, encoding="utf-8") as f:
        out = json.load(f)
    out["decision"] = {
        "tp": [a, b, c],
        "date": "2026-08-31",
        "why": "tp2 2.0→1.5 在所有 tp1 下均提升（当前 7.57U → 1.2/1.5/3.5 为 9.42U，dd 1.39%，wr 90.9%，"
               "PF 25.3），是唯一一致方向；tp1 1.0→1.2 小增益；tp3 仅 1 笔触发，保持线上 3.5 不动。"
               "注意：该品种 2026-06-10 上线仅 2.8 个月，全段 11 笔（H2 4 笔），样本少，"
               "本档位改善幅度约 +25%，置信度低于其他品种。",
        "risk": "样本仅 11 笔，2.8 个月；H2 仅 4 笔，前后半段验证参考性弱；建议观察 1-2 个月再复核。",
    }
    out["recommend"] = s
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("=== FINAL 1.2/1.5/3.5 ===")
    print(json.dumps(s, ensure_ascii=False, indent=2, default=str))
    print(f"\nWrote {path}")

    print("\n=== 推送指令（47.84.106.154 后端执行）===")
    payload = {
        "symbol": SYMBOL,
        "profile": "normal",
        "tp1_pct": a, "tp1_ratio": 30.0,
        "tp2_pct": b, "tp2_ratio": 40.0,
        "tp3_pct": c, "tp3_ratio": 100.0,
    }
    print(f"POST http://47.84.106.154:5174/api/trade/exit-rules")
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
