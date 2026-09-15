# -*- coding: utf-8 -*-
"""MU V2 单独标定实验（补 _score_v2_multi 缺项）。

47 的 MU 已上线 quality_filter_v2 @ full/half=45，但当初切 V2 时没有
像 NVDA 那样做阈值网格标定（_score_v2_multi.json 只测了 NVDA/CL/SKH）。

本实验以 47 的 MU sym 为统一框架（params/ER/quality gate/出场全部取 47，
唯一变量 = 引擎 + 阈值），回答：
  A  47 现行 v2 @45        —— 当前实盘口径
  B  v1 引擎同阈值(45/45)  —— 47 若直接退回 v1 的引擎差异
  C  v1 @43 阈值(full45/alert35) —— 43 对照机口径（仅引擎/阈值差异）
  D  v2 阈值网格 [35..60]   —— 45 是否为 v2 最优档（B* 模式）
  E  B* 与 v1 的前后半段稳健性
写 _score_v2_mu.json。
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
LIVE47 = "http://47.84.106.154:5174/api/trade/symbols"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data")
FILE = "MU.json"
TF = "1h"
GRID = [35, 40, 45, 50, 55, 60]
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
    s43 = next(s for s in _get(LIVE43)["symbols"] if s["symbol"] == "MU-USDT-SWAP")
    s47 = next(s for s in _get(LIVE47)["symbols"] if s["symbol"] == "MU-USDT-SWAP")
    print("== 43 MU ==", {k: s43.get(k) for k in (
        "score_engine", "scoring_full_threshold", "scoring_half_threshold",
        "scoring_alert_threshold", "use_dynamic_threshold")})
    print("== 47 MU ==", {k: s47.get(k) for k in (
        "score_engine", "scoring_full_threshold", "scoring_half_threshold",
        "scoring_alert_threshold", "use_dynamic_threshold")})

    p = s47["params"]
    base = trade_cfg(s47)                    # 47 现行 v2
    cfg_v1_same = replace(base, score_engine="", score_v2=False)  # v1 @47阈值
    cfg_v1_43 = replace(cfg_v1_same,
                        scoring_full_threshold=45.0,
                        scoring_half_threshold=s43.get("scoring_half_threshold", 45.0),
                        scoring_alert_threshold=s43.get("scoring_alert_threshold", 35.0))
    ex = exit_rules(s47)

    with open(os.path.join(DATA, FILE), "r", encoding="utf-8") as f:
        cached = json.load(f)
    cbtf = {k: v for k, v in cached.items() if isinstance(v, list) and v}
    candles = cbtf.get(TF)
    if not candles or len(candles) < 300:
        print("数据不足"); return
    print(f"行情 {FILE}: " + ", ".join(f"{k}={len(v)}" for k, v in cbtf.items())
          + f"  [{ts_fmt(candles[0]['ts'])} ~ {ts_fmt(candles[-1]['ts'])}]", flush=True)
    print(f"ST {p.get('periods')}x{p.get('multiplier')}  "
          f"ex47.max_loss={ex.max_loss_enabled}@{ex.max_loss_pct}", flush=True)

    common = dict(init_cash=100.0, fee_rate=0.0005, allow_short=True,
                  exit_rules=ex, sizing="fixed",
                  margin_usdt=s47["margin_usdt"], leverage=s47["leverage"],
                  gate_tf=TF, candles_by_tf=cbtf)

    runs = {
        "V2_now_45": base,
        "V1_same_thr": cfg_v1_same,
        "V1_43_thr": cfg_v1_43,
    }
    out = {}
    for key, cfg in runs.items():
        r = run_backtest(candles, p, live_gate=cfg, **common)
        out[key] = summarize(r) if "error" not in r else {"error": r.get("error")}
        s = out[key]
        if "error" not in s:
            print(f"  {key:12s}: pnl={s['pnl_u']}U dd={s['max_dd_pct']}% "
                  f"PF={s['profit_factor']} wr={s['win_rate']}% trades={s['trades']} "
                  f"avg={s['avg_trade_pct']}% blocked={s['blocked']}", flush=True)

    grid = []
    for thr in GRID:
        cfg_t = replace(base, scoring_full_threshold=float(thr),
                        scoring_half_threshold=float(thr),
                        scoring_alert_threshold=max(base.scoring_alert_threshold, float(thr)))
        rt = run_backtest(candles, p, live_gate=cfg_t, **common)
        if "error" not in rt:
            s = summarize(rt)
            s["thr"] = thr
            grid.append(s)
            print(f"  v2 thr={thr:g}  : pnl={s['pnl_u']}U dd={s['max_dd_pct']}% "
                  f"PF={s['profit_factor']} wr={s['win_rate']}% trades={s['trades']} "
                  f"avg={s['avg_trade_pct']}% blocked={s['blocked']}", flush=True)

    ok = [g for g in grid if g["trades"] >= MIN_TRADES]
    best = max(ok, key=lambda g: g["pnl_u"]) if ok else None
    out["grid"] = grid
    if best:
        cfgB2 = replace(base, scoring_full_threshold=float(best["thr"]),
                        scoring_half_threshold=float(best["thr"]),
                        scoring_alert_threshold=float(best["thr"]))
        out["best_thr"] = best["thr"]
        out["B_star"] = best
        h = lambda s: (f"pnl={s['pnl_u']}U/{s['trades']}笔/dd{s['max_dd_pct']}%/PF{s['profit_factor']}"
                       if s and "error" not in s else "err")
        out["V2_now_45_h1"] = run_half(candles, cbtf, base, p, common, True)
        out["V2_now_45_h2"] = run_half(candles, cbtf, base, p, common, False)
        out["V1_same_h1"] = run_half(candles, cbtf, cfg_v1_same, p, common, True)
        out["V1_same_h2"] = run_half(candles, cbtf, cfg_v1_same, p, common, False)
        out["Bstar_h1"] = run_half(candles, cbtf, cfgB2, p, common, True)
        out["Bstar_h2"] = run_half(candles, cbtf, cfgB2, p, common, False)
        print(f"\n  >> B* 最优 thr={best['thr']:g}: pnl={best['pnl_u']}U "
              f"dd={best['max_dd_pct']}% PF={best['profit_factor']} "
              f"wr={best['win_rate']}% trades={best['trades']}", flush=True)
        print(f"  前后半段  V2now: {h(out['V2_now_45_h1'])} | {h(out['V2_now_45_h2'])}")
        print(f"  前后半段  V1same: {h(out['V1_same_h1'])} | {h(out['V1_same_h2'])}")
        print(f"  前后半段  B*(thr{best['thr']}): {h(out['Bstar_h1'])} | {h(out['Bstar_h2'])}", flush=True)

    with open(os.path.join(DATA, "_score_v2_mu.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    print(f"\nWrote _score_v2_mu.json | 总耗时 {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
