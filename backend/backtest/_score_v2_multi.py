# -*- coding: utf-8 -*-
"""Score V2 A/B 多品种验证。

对 47.84 服务器上的每个品种：
  A  = 线上现行打分制 v1（score_v2=False, 用线上阈值）
  B  = v2 同阈值直接比
  B* = v2 阈值重标定后的最优档（笔数下限约束）→ 与 A 对等比较 + 前后半段
写 _score_v2_multi.json 汇总。
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import replace  # noqa: E402

from backtest import run_backtest  # noqa: E402
from _live_cfg_backtest import exit_rules, trade_cfg, _get, ts_fmt  # noqa: E402

LIVE_URL = "http://47.84.106.154:5174/api/trade/symbols"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data")
# symbol → (数据文件, 1h/15m 等主 tf, 阈值扫描网格, 最少交易数)
CASES = [
    {"sym": "NVDA-USDT-SWAP", "file": "NVDA.json", "thr": [25, 30, 35, 40, 45, 50, 55],
     "min_trades": 40},
    {"sym": "CL-USDT-SWAP", "file": "cl_half_cache.json", "thr": [30, 35, 40, 45, 50, 55],
     "min_trades": 40},
    {"sym": "SKHYNIX-USDT-SWAP", "file": "SKHYNIX.json", "thr": [40, 45, 50, 55, 60, 65, 70],
     "min_trades": 20},
]
TF = "1h"


def summarize(r: dict) -> dict:
    tl = r.get("trade_list", [])
    return {
        "pnl_u": round(r["final"] - 100.0, 2),
        "max_dd_pct": r["max_dd_pct"],
        "profit_factor": r["profit_factor"],
        "win_rate": r["win_rate"],
        "trades": r["trades"],
        "avg_trade_pct": round(sum(t["pnl_pct"] for t in tl) / len(tl), 3) if tl else None,
        "avg_bars": r["avg_bars"],
        "blocked": r["er_blocked"],
    }


def run_half(candles, cbtf, cfg, p, common, first_half: bool):
    mid_ts = candles[len(candles) // 2]["ts"]
    sel = ([c for c in candles if c["ts"] <= mid_ts] if first_half
           else [c for c in candles if c["ts"] > mid_ts])
    cbtf_sel = {k: [c for c in v if c["ts"] <= mid_ts] if first_half
                else [c for c in v if c["ts"] > mid_ts]
                for k, v in cbtf.items()}
    if not sel:
        return None
    r = run_backtest(sel, p, live_gate=cfg,
                     **{**common, "candles_by_tf": cbtf_sel})
    return summarize(r) if "error" not in r else None


def main():
    t0 = time.time()
    out = {}
    live = _get(LIVE_URL)
    for case in CASES:
        sym_key = case["sym"]
        name = sym_key.split("-")[0]
        print(f"\n{'=' * 62}\n== {name} ({sym_key}) ==\n{'=' * 62}", flush=True)
        try:
            sym = next(s for s in live["symbols"] if s["symbol"] == sym_key)
        except StopIteration:
            print(f"  47.84 无 {sym_key} 配置，跳过"); continue
        p = sym["params"]
        cfgA = trade_cfg(sym)
        cfgB = replace(cfgA, score_v2=True)
        ex = exit_rules(sym)
        with open(os.path.join(DATA, case["file"]), "r", encoding="utf-8") as f:
            cached = json.load(f)
        cbtf = {k: v for k, v in cached.items() if isinstance(v, list) and v}
        candles = cbtf.get(TF)
        if not candles or len(candles) < 300:
            print(f"  数据不足"); continue
        print(f"  行情 {case['file']}: " + ", ".join(
            f"{k}={len(v)}" for k, v in cbtf.items())
            + f"  [{ts_fmt(candles[0]['ts'])} ~ {ts_fmt(candles[-1]['ts'])}]", flush=True)
        print(f"  线上: ST {p.get('periods')}x{p.get('multiplier')} "
              f"er={cfgA.er_hide_below}/{cfgA.er_weak_min}/{cfgA.er_min}/{cfgA.er_trend} "
              f"score={cfgA.scoring_full_threshold:g}/{cfgA.scoring_half_threshold:g}/"
              f"{cfgA.scoring_alert_threshold:g}", flush=True)

        common = dict(init_cash=100.0, fee_rate=0.0005, allow_short=True,
                      exit_rules=ex, sizing="fixed",
                      margin_usdt=sym["margin_usdt"], leverage=sym["leverage"],
                      gate_tf=TF, candles_by_tf=cbtf)

        ra = run_backtest(candles, p, live_gate=cfgA, **common)
        sa = summarize(ra) if "error" not in ra else None
        if sa is None:
            print(f"  A 回测失败"); continue
        print(f"  A(v1 现行): pnl={sa['pnl_u']}U dd={sa['max_dd_pct']}% "
              f"PF={sa['profit_factor']} wr={sa['win_rate']}% trades={sa['trades']} "
              f"avg={sa['avg_trade_pct']}%", flush=True)

        # B 同阈值 + 网格
        rt0 = run_backtest(candles, p,
                           live_gate=replace(cfgB, scoring_full_threshold=cfgA.scoring_full_threshold,
                                             scoring_half_threshold=cfgA.scoring_half_threshold,
                                             scoring_alert_threshold=cfgA.scoring_alert_threshold),
                           **common)
        sb0 = summarize(rt0) if "error" not in rt0 else None
        grid = []
        for thr in case["thr"]:
            cfg_t = replace(cfgB, scoring_full_threshold=thr,
                            scoring_half_threshold=thr,
                            scoring_alert_threshold=max(cfgA.scoring_alert_threshold, thr))
            rt = run_backtest(candles, p, live_gate=cfg_t, **common)
            if "error" not in rt:
                s = summarize(rt)
                s["thr"] = thr
                grid.append(s)
                print(f"  v2 thr={thr:g}: pnl={s['pnl_u']}U dd={s['max_dd_pct']}% "
                      f"PF={s['profit_factor']} wr={s['win_rate']}% "
                      f"trades={s['trades']} avg={s['avg_trade_pct']}%", flush=True)

        ok = [g for g in grid if g["trades"] >= case["min_trades"]]
        best = max(ok, key=lambda g: g["pnl_u"]) if ok else None
        case_out = {"name": name, "sa": sa, "sb_same_thr": sb0, "grid": grid,
                    "params": p,
                    "thr_A": {"full": cfgA.scoring_full_threshold,
                              "half": cfgA.scoring_half_threshold,
                              "alert": cfgA.scoring_alert_threshold}}
        if best:
            cfgB2 = replace(cfgB, scoring_full_threshold=best["thr"],
                            scoring_half_threshold=best["thr"],
                            scoring_alert_threshold=best["thr"])
            s1 = run_half(candles, cbtf, cfgB2, p, common, True)
            s2 = run_half(candles, cbtf, cfgB2, p, common, False)
            h1a = run_half(candles, cbtf, cfgA, p, common, True)
            h2a = run_half(candles, cbtf, cfgA, p, common, False)
            case_out["best_thr"] = best["thr"]
            case_out["sb_best"] = best
            case_out["A_h1"], case_out["A_h2"] = h1a, h2a
            case_out["B_h1"], case_out["B_h2"] = s1, s2
            print(f"\n  >> B* 最优 thr={best['thr']:g}: pnl={best['pnl_u']}U "
                  f"dd={best['max_dd_pct']}% PF={best['profit_factor']} "
                  f"wr={best['win_rate']}% trades={best['trades']} "
                  f"avg={best['avg_trade_pct']}%", flush=True)
            f = lambda s: (f"pnl={s['pnl_u']}U/{s['trades']}笔/dd{s['max_dd_pct']}%" if s else "err")
            print(f"  前后半段  A: {f(h1a)} | {f(h2a)}")
            print(f"  前后半段 B*: {f(s1)} | {f(s2)}", flush=True)
        out[sym_key] = case_out

    with open(os.path.join(DATA, "_score_v2_multi.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    print(f"\nWrote _score_v2_multi.json | 总耗时 {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
