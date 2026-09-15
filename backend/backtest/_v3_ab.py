# -*- coding: utf-8 -*-
"""V3（Event/Timing）A/B 验证：v1 vs v2 vs v3（同一品种、同一参数与出场）。

V3 = V2 的 Alpha 底座（0.65 权重）+ Timing（突破/回踩/再启动）+ 历史统计 − 风险扣分，
并带"追高阻断 → 等回踩 → Relaunch 才入场"的生命周期。因为分数量纲与 V2 不同
（0.65 缩放 + 独立扣分），必须重新扫阈值网格才能公平比较。

同时输出 V3 机制诊断：Chase 阻断了多少、回踩后 Relaunch 触发了多少、
多少候选超时/破位失效 —— 用来确认"等回踩再进"这条链路真的在跑，而不是被拦光。

用法: python _v3_ab.py [BTC|NVDA|MU|CL|SNDK|...]  默认 BTC
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
        "blocked": r["er_blocked"],
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


def diag_v3(candles, p, cfg, cbtf, tf: str) -> dict:
    """V3 机制诊断：每个翻转信号的阶段分布 + Relaunch 触发次数。"""
    import strategy  # noqa: E402
    import v3_signal as v3  # noqa: E402
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

    stages: dict[str, int] = {}
    for s in st_signals(candles, st, tf):
        s["grade"] = strategy.grade(s, verdict)
        i = idx.get(s["ts"])
        if i is None:
            continue
        try:
            r = score_signal(s, candles[: i + 1], cfg, cbtf, p)
        except Exception:
            continue
        key = r.get("v3_stage") or "PASS(未阻断)"
        stages[key] = stages.get(key, 0) + 1

    # 模拟每根K推进（对齐 feed/backtest 的补单钩子）
    relaunch = 0
    for i in range(len(candles)):
        for cand in v3.LIFECYCLE.pending(tf=tf):
            try:
                if v3.LIFECYCLE.advance(cand["key"], candles[: i + 1]).get("trigger"):
                    relaunch += 1
            except Exception:
                pass
    return {"stages": stages, "relaunch_triggered": relaunch}


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
    cfgV1 = replace(base, score_engine="trend_follow_v1", score_v2=False)
    cfgV2 = replace(base, score_engine="quality_filter_v2", score_v2=False)
    cfgV3 = replace(base, score_engine="event_timing_v3", score_v2=False)
    cfgV3v1 = replace(base, score_engine="event_timing_v3_v1", score_v2=False)

    print(f"== {name} V3 A/B == ST {p.get('periods')}x{p.get('multiplier')}  "
          f"{ts_fmt(candles[0]['ts'])} ~ {ts_fmt(candles[-1]['ts'])}  "
          f"线上阈值 full={base.scoring_full_threshold} half={base.scoring_half_threshold} "
          f"alert={base.scoring_alert_threshold} engine={sym.get('score_engine') or 'v1'}",
          flush=True)

    common = dict(init_cash=100.0, fee_rate=0.0005, allow_short=True,
                  exit_rules=ex, sizing="fixed", margin_usdt=sym["margin_usdt"],
                  leverage=sym["leverage"], gate_tf=tf, candles_by_tf=cbtf)

    def run_key(key, cfg):
        r = run_backtest(candles, p, live_gate=cfg, **common)
        s = summarize(r) if "error" not in r else {"error": r.get("error")}
        if "error" in s:
            print(f"  {key:14s}: ERROR {s['error']}", flush=True)
        else:
            print(f"  {key:14s}: pnl={s['pnl_u']}U dd={s['max_dd_pct']}% "
                  f"PF={s['profit_factor']} wr={s['win_rate']}% n={s['trades']} "
                  f"avg={s['avg_trade_pct']}% blocked={s['blocked']}", flush=True)
        return s

    out = {"name": name, "params": p, "thr_now": {
        "full": base.scoring_full_threshold, "half": base.scoring_half_threshold,
        "alert": base.scoring_alert_threshold}}
    out["A_v1"] = run_key("A v1(同阈值)", cfgV1)
    out["B_v2"] = run_key("B v2(同阈值)", cfgV2)
    out["C_v3_raw"] = run_key("C v3(同阈值)", cfgV3)
    out["C2_v3v1_raw"] = run_key("C2 v3v1(同阈值)", cfgV3v1)

    print("  -- v3 阈值网格（V2 底座 vs V1 底座）--", flush=True)
    grids = {}
    for tag, cfg_e in (("v3", cfgV3), ("v3v1", cfgV3v1)):
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
            print(f"  {tag:<5s} thr={thr:<3g}: pnl={s['pnl_u']}U dd={s['max_dd_pct']}% "
                  f"PF={s['profit_factor']} wr={s['win_rate']}% n={s['trades']} "
                  f"avg={s['avg_trade_pct']}% blocked={s['blocked']}", flush=True)
        grids[tag] = acc
    out["grid"] = grids["v3"]
    out["grid_v1base"] = grids["v3v1"]

    def pick(acc):
        ok = [g for g in acc if g["trades"] >= MIN_TRADES]
        return max(ok, key=lambda g: g["pnl_u"]) if ok else None

    h = lambda s: (f"pnl={s['pnl_u']}U/{s['trades']}笔/dd{s['max_dd_pct']}%/"
                   f"PF{s['profit_factor']}" if s and "error" not in s else "err")
    out["A_h1"] = run_half(candles, cbtf, cfgV1, p, common, True)
    out["A_h2"] = run_half(candles, cbtf, cfgV1, p, common, False)
    print(f"  前后半段 v1 基线: {h(out['A_h1'])} | {h(out['A_h2'])}", flush=True)

    for tag, acc, cfg_e, key in (("V3(V2底座)", grids["v3"], cfgV3, "D"),
                                 ("V3(V1底座)", grids["v3v1"], cfgV3v1, "E")):
        b = pick(acc)
        if not b:
            continue
        out[f"{key}_best"] = b
        cfgB = replace(cfg_e, scoring_full_threshold=float(b["thr"]),
                       scoring_half_threshold=float(b["thr"]),
                       scoring_alert_threshold=float(b["thr"]))
        out[f"{key}_h1"] = run_half(candles, cbtf, cfgB, p, common, True)
        out[f"{key}_h2"] = run_half(candles, cbtf, cfgB, p, common, False)
        print(f"\n  >> {tag} 最优 thr={b['thr']:g}: pnl={b['pnl_u']}U "
              f"dd={b['max_dd_pct']}% PF={b['profit_factor']} n={b['trades']}")
        print(f"     前后半段: {h(out[f'{key}_h1'])} | {h(out[f'{key}_h2'])}",
              flush=True)

    try:
        d = diag_v3(candles, p, cfgV3, cbtf, tf)
        out["diag"] = d
        print(f"\n  V3 机制诊断: {d['stages']}  Relaunch触发={d['relaunch_triggered']}",
              flush=True)
    except Exception as e:
        print(f"\n  V3 诊断失败: {e}", flush=True)

    with open(os.path.join(DATA, f"_v3_ab_{name.lower()}.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    print(f"Wrote _v3_ab_{name.lower()}.json | 耗时 {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
