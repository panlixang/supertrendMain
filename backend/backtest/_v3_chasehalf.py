# -*- coding: utf-8 -*-
"""Chase 降半仓 A/B：V3 追高时"禁止入场等回踩"(block) vs "允许进但半仓"(half)。

CHASE_MODE = "block" → 追高信号登记候选，等回踩 Relaunch 才判单（v3.md 原设计）
CHASE_MODE = "half"  → 追高信号照常判单，但降级半仓（对齐实盘 executor 保证金减半）

对照组（同一品种、同一参数与出场、阈值各自扫网格取最优）：
  A  v1/v2 现行基线
  B  V3v1（V1 底座）block
  C  V3v1（V1 底座）half
  D  V3  （V2 底座）block
  E  V3  （V2 底座）half

用法: python _v3_chasehalf.py [BTC|NVDA|MU|SNDK|CL]  默认 BTC
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
import v3_signal as v3  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data")
HOST = {"43": "http://43.108.10.84:5174/api/trade/symbols",
        "47": "http://47.84.106.154:5174/api/trade/symbols"}
CASES = [
    {"host": "43", "sym": "BTC-USDT-SWAP",      "file": "BTC.json"},
    {"host": "43", "sym": "SNDK-USDT-SWAP",     "file": "SNDK.json"},
    {"host": "47", "sym": "NVDA-USDT-SWAP",     "file": "NVDA.json"},
    {"host": "47", "sym": "MU-USDT-SWAP",       "file": "MU.json"},
    {"host": "47", "sym": "CL-USDT-SWAP",       "file": "cl_half_cache.json"},
]
GRID = [15, 20, 25, 30, 35, 40, 45, 50]
MIN_TRADES = 20


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
    }


def run_half(candles, cbtf, cfg, p, common, first_half: bool):
    mid_ts = candles[len(candles) // 2]["ts"]
    sel = ([c for c in candles if c["ts"] <= mid_ts] if first_half
           else [c for c in candles if c["ts"] > mid_ts])
    cbtf_sel = {k: ([c for c in v if c["ts"] <= mid_ts] if first_half
                    else [c for c in v if c["ts"] > mid_ts])
                for k, v in cbtf.items()}
    if not sel:
        return None
    r = run_backtest(sel, p, live_gate=cfg, **{**common, "candles_by_tf": cbtf_sel})
    return summarize(r) if "error" not in r else None


def count_chase_hit(candles, cbtf, p, cfg, tf: str) -> dict:
    """half 模式下有多少翻转信号被判定为追高（会被降半仓的那一批）。"""
    import strategy  # noqa: E402
    from indicators import st_signals, super_trend  # noqa: E402
    from regime_scoring import score_signal  # noqa: E402

    v3.LIFECYCLE.reset()
    st = super_trend([c["o"] for c in candles], [c["h"] for c in candles],
                     [c["l"] for c in candles], [c["c"] for c in candles],
                     periods=p.get("periods", 15), multiplier=p.get("multiplier", 9.1),
                     src=p.get("src", "hl2"), change_atr=p.get("change_atr", True))
    try:
        verdict = strategy.mtf_bias(cbtf or {tf: candles}, p)["verdict"]
    except Exception:
        verdict = "mixed"
    idx = {c["ts"]: i for i, c in enumerate(candles)}
    n = m = 0
    for s in st_signals(candles, st, tf):
        s["grade"] = strategy.grade(s, verdict)
        i = idx.get(s["ts"])
        if i is None:
            continue
        try:
            r = score_signal(s, candles[: i + 1], cfg, cbtf, p)
        except Exception:
            continue
        d = r.get("detail") or {}
        if d.get("chase_half"):
            n += 1
            if r.get("action") == "trade_half":
                m += 1
    v3.LIFECYCLE.reset()
    return {"追高被降半仓的信号数": n, "其中实际按半仓入场": m}


def main():
    t0 = time.time()
    kw = (sys.argv[1].upper() if len(sys.argv) > 1 else "BTC")
    case = next((c for c in CASES if kw in c["sym"]), CASES[0])
    sym_key, tf = case["sym"], "1h"
    name = sym_key.split("-")[0]

    sym = next(s for s in _get(HOST[case["host"]])["symbols"] if s["symbol"] == sym_key)
    with open(os.path.join(DATA, case["file"]), "r", encoding="utf-8") as f:
        cached = json.load(f)
    cbtf = {k: v for k, v in cached.items() if isinstance(v, list) and v}
    candles = cbtf.get(tf)
    if not candles or len(candles) < 300:
        print("数据不足"); return
    p = sym["params"]
    ex = exit_rules(sym)
    base = trade_cfg(sym)
    cfgNow = replace(base, score_engine=sym.get("score_engine") or "trend_follow_v1",
                     score_v2=False)
    cfgV3 = replace(base, score_engine="event_timing_v3", score_v2=False)
    cfgV3v1 = replace(base, score_engine="event_timing_v3_v1", score_v2=False)

    print(f"== {name} Chase 降半仓 A/B == ST {p.get('periods')}x{p.get('multiplier')}  "
          f"{ts_fmt(candles[0]['ts'])} ~ {ts_fmt(candles[-1]['ts'])}  "
          f"线上阈值 full={base.scoring_full_threshold} half={base.scoring_half_threshold} "
          f"alert={base.scoring_alert_threshold}", flush=True)

    common = dict(init_cash=100.0, fee_rate=0.0005, allow_short=True,
                  exit_rules=ex, sizing="fixed", margin_usdt=sym["margin_usdt"],
                  leverage=sym["leverage"], gate_tf=tf, candles_by_tf=cbtf)

    out = {"name": name, "params": p}
    h = lambda s: (f"pnl={s['pnl_u']}U/{s['trades']}笔/dd{s['max_dd_pct']}%/"
                   f"PF{s['profit_factor']}" if s and "error" not in s else "err")

    def line(tag, s):
        print(f"  {tag:<16s}: pnl={s['pnl_u']}U dd={s['max_dd_pct']}% "
              f"PF={s['profit_factor']} wr={s['win_rate']}% n={s['trades']} "
              f"avg={s['avg_trade_pct']}%", flush=True)

    # ── 基线：线上现行配置 ──
    v3.CHASE_MODE = "block"
    r_now = run_backtest(candles, p, live_gate=cfgNow, **common)
    out["A_now"] = summarize(r_now) if "error" not in r_now else {"error": r_now}
    line(f"A 现行({sym.get('score_engine') or 'v1'})", out["A_now"])
    out["A_h1"] = run_half(candles, cbtf, cfgNow, p, common, True)
    out["A_h2"] = run_half(candles, cbtf, cfgNow, p, common, False)
    print(f"     前后半段 : {h(out['A_h1'])} | {h(out['A_h2'])}", flush=True)

    combos = [("B V3v1·block", cfgV3v1, "block"), ("C V3v1·half", cfgV3v1, "half"),
              ("D V3·block", cfgV3, "block"), ("E V3·half", cfgV3, "half")]
    best_cfg = {}
    for tag, cfg_e, mode in combos:
        v3.CHASE_MODE = mode
        acc = []
        for thr in GRID:
            cfg_t = replace(cfg_e, scoring_full_threshold=float(thr),
                            scoring_half_threshold=float(thr),
                            scoring_alert_threshold=float(thr))
            r = run_backtest(candles, p, live_gate=cfg_t, **common)
            if "error" in r:
                continue
            s = summarize(r); s["thr"] = thr
            acc.append(s)
        ok = [g for g in acc if g["trades"] >= MIN_TRADES]
        best = max(ok, key=lambda g: g["pnl_u"]) if ok else None
        out[tag] = {"grid": acc, "best": best}
        if not best:
            print(f"  {tag:<16s}: 无有效档位", flush=True)
            continue
        line(f"{tag} thr{best['thr']:g}", best)
        cfgB = replace(cfg_e, scoring_full_threshold=float(best["thr"]),
                       scoring_half_threshold=float(best["thr"]),
                       scoring_alert_threshold=float(best["thr"]))
        best_cfg[tag] = cfgB
        out[tag + "_h1"] = run_half(candles, cbtf, cfgB, p, common, True)
        out[tag + "_h2"] = run_half(candles, cbtf, cfgB, p, common, False)
        print(f"     前后半段 : {h(out[tag + '_h1'])} | {h(out[tag + '_h2'])}",
              flush=True)

    # half 模式机制自检：到底有多少信号真的走了降半仓
    v3.CHASE_MODE = "half"
    out["diag"] = count_chase_hit(candles, cbtf, p,
                                  best_cfg.get("C V3v1·half") or cfgV3v1, tf)
    v3.CHASE_MODE = "block"
    print(f"\n  V3v1 half 模式自检: {out['diag']}", flush=True)

    fp = os.path.join(DATA, f"_v3_chasehalf_{name.lower()}.json")
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"Wrote {os.path.basename(fp)} | 耗时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
