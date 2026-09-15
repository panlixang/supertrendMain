# -*- coding: utf-8 -*-
"""BTC v1 vs v2 A/B 验证（43 对照机口径，沿用 _score_v2_multi 框架）。

BTC 在 43：ST 11x4.0、ER 0.1/0.08/0.12/0.3、v1 @ full/half=50 alert=55
（conv≈0.43，engine_advisor 判 V1）。本实验回答：BTC 上 v2 是否真的更差。
  A       = v1 现行（43 线上配置）
  B_same  = v2 同阈值（引擎差异本身）
  grid    = v2 阈值扫描 [35..65]（B* 模式）
  E       = B* vs A 前后半段
写 _score_v2_btc.json。
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

LIVE43 = "http://43.108.10.84:5174/api/trade/symbols"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data")
FILE = "BTC.json"
TF = "1h"
GRID = [35, 40, 45, 50, 55, 60, 65]
MIN_TRADES = 30


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
    s43 = next(s for s in _get(LIVE43)["symbols"] if s["symbol"] == "BTC-USDT-SWAP")
    print("== 43 BTC ==", {k: s43.get(k) for k in (
        "score_engine", "scoring_full_threshold", "scoring_half_threshold",
        "scoring_alert_threshold", "use_dynamic_threshold", "min_score")})

    p = s43["params"]
    cfgA = trade_cfg(s43)                        # v1 现行（43 实盘口径）
    cfgB = replace(cfgA, score_engine="quality_filter_v2", score_v2=False)  # v2 引擎
    ex = exit_rules(s43)

    with open(os.path.join(DATA, FILE), "r", encoding="utf-8") as f:
        cached = json.load(f)
    cbtf = {k: v for k, v in cached.items() if isinstance(v, list) and v}
    candles = cbtf.get(TF)
    if not candles or len(candles) < 300:
        print("数据不足"); return
    print(f"行情 {FILE}: " + ", ".join(f"{k}={len(v)}" for k, v in cbtf.items())
          + f"  [{ts_fmt(candles[0]['ts'])} ~ {ts_fmt(candles[-1]['ts'])}]", flush=True)
    print(f"ST {p.get('periods')}x{p.get('multiplier')}  "
          f"ex.max_loss={ex.max_loss_enabled}  range_filter={s43.get('range_filter_enabled')}", flush=True)

    common = dict(init_cash=100.0, fee_rate=0.0005, allow_short=True,
                  exit_rules=ex, sizing="fixed",
                  margin_usdt=s43["margin_usdt"], leverage=s43["leverage"],
                  gate_tf=TF, candles_by_tf=cbtf)

    def run_key(key, cfg):
        r = run_backtest(candles, p, live_gate=cfg, **common)
        s = summarize(r) if "error" not in r else {"error": r.get("error")}
        if "error" not in s:
            print(f"  {key:10s}: pnl={s['pnl_u']}U dd={s['max_dd_pct']}% "
                  f"PF={s['profit_factor']} wr={s['win_rate']}% trades={s['trades']} "
                  f"avg={s['avg_trade_pct']}% blocked={s['blocked']}", flush=True)
        return s

    out = {
        "name": "BTC",
        "params": p,
        "thr_A": {"full": cfgA.scoring_full_threshold,
                  "half": cfgA.scoring_half_threshold,
                  "alert": cfgA.scoring_alert_threshold},
        "A_v1_now": run_key("A(v1@50)", cfgA),
        "B_v2_same": run_key("B(v2@50)", cfgB),
    }

    grid = []
    for thr in GRID:
        cfg_t = replace(cfgB, scoring_full_threshold=float(thr),
                        scoring_half_threshold=float(thr),
                        scoring_alert_threshold=max(cfgA.scoring_alert_threshold, float(thr)))
        rt = run_backtest(candles, p, live_gate=cfg_t, **common)
        if "error" not in rt:
            s = summarize(rt)
            s["thr"] = thr
            grid.append(s)
            print(f"  v2 thr={thr:g}   : pnl={s['pnl_u']}U dd={s['max_dd_pct']}% "
                  f"PF={s['profit_factor']} wr={s['win_rate']}% trades={s['trades']} "
                  f"avg={s['avg_trade_pct']}% blocked={s['blocked']}", flush=True)
    out["grid"] = grid

    ok = [g for g in grid if g["trades"] >= MIN_TRADES]
    best = max(ok, key=lambda g: g["pnl_u"]) if ok else None
    if best:
        cfgB2 = replace(cfgB, scoring_full_threshold=float(best["thr"]),
                        scoring_half_threshold=float(best["thr"]),
                        scoring_alert_threshold=float(best["thr"]))
        out["best_thr"] = best["thr"]
        out["B_star"] = best
        h = lambda s: (f"pnl={s['pnl_u']}U/{s['trades']}笔/dd{s['max_dd_pct']}%/PF{s['profit_factor']}"
                       if s and "error" not in s else "err")
        out["A_h1"] = run_half(candles, cbtf, cfgA, p, common, True)
        out["A_h2"] = run_half(candles, cbtf, cfgA, p, common, False)
        out["Bstar_h1"] = run_half(candles, cbtf, cfgB2, p, common, True)
        out["Bstar_h2"] = run_half(candles, cbtf, cfgB2, p, common, False)
        print(f"\n  >> B* 最优 thr={best['thr']:g}: pnl={best['pnl_u']}U "
              f"dd={best['max_dd_pct']}% PF={best['profit_factor']} "
              f"wr={best['win_rate']}% trades={best['trades']}", flush=True)
        print(f"  前后半段  A    : {h(out['A_h1'])} | {h(out['A_h2'])}")
        print(f"  前后半段  B*(thr{best['thr']}): {h(out['Bstar_h1'])} | {h(out['Bstar_h2'])}", flush=True)

    with open(os.path.join(DATA, "_score_v2_btc.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    print(f"\nWrote _score_v2_btc.json | 总耗时 {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
