# -*- coding: utf-8 -*-
"""BTC 交叉验证：用当前半年(2026-03~09)寻优出的最优参数，在前半年(2025-09~2026-03)
跑关键配置对比，验证是否过拟合。"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _sweep_btc as S  # noqa: E402  (复用 fetch_window/make_cfg/one/parse_ms/ts_fmt)

W_START, W_END = "2025-09-01", "2026-03-01"
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "_live_data", "btc_prev_half_cache.json")

P = {"periods": 11, "multiplier": 4.0, "src": "hl2", "change_atr": True,
     "fast_len": 20, "slow_len": 50, "ma_type": "EMA"}


def main():
    t0 = time.time()
    s_ms, e_ms = S.parse_ms(W_START), S.parse_ms(W_END)

    cbtf: dict[str, list] = {}
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            cached = json.load(f)
        cbtf = {t: cached[t] for t in S.BIAS_TFS if t in cached}
        print(f"使用缓存 K 线 {CACHE_PATH}", flush=True)
    if not cbtf:
        candles = S.fetch_window(S.SYM, S.TF, s_ms, e_ms, S.BARS)
        if not candles:
            print("抓取 K 线失败", flush=True)
            return
        cbtf = {S.TF: candles}
        for t in S.BIAS_TFS:
            if t == S.TF:
                continue
            extra = S.fetch_window(S.SYM, t, s_ms, e_ms, S.BARS_BY_TF[t])
            if extra:
                cbtf[t] = extra
        try:
            os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(cbtf, f)
        except Exception as ex:
            print(f"缓存写盘失败: {ex}", flush=True)
    candles = cbtf[S.TF]
    print(f"K线就绪: {S.TF}={len(candles)} 根, "
          f"{S.ts_fmt(candles[0]['ts'])} ~ {S.ts_fmt(candles[-1]['ts'])}", flush=True)

    cfgs = {
        "A 线上基线(er .12/.12/.3, score 50/50/55 m1)": dict(),
        "B 保守(er .12/.10/.25, score 50/50/55 m1)": dict(er_weak_min=0.10, er_trend=0.25),
        "C 中间(er .12/.10/.25, score 45/45/45 m0)": dict(
            er_weak_min=0.10, er_trend=0.25, min_score=0,
            scoring_full_threshold=45.0, scoring_half_threshold=45.0,
            scoring_alert_threshold=45.0),
        "D 激进(er .12/.10/.25, score 40/40/40 m0)": dict(
            er_weak_min=0.10, er_trend=0.25, min_score=0,
            scoring_full_threshold=40.0, scoring_half_threshold=40.0,
            scoring_alert_threshold=40.0),
        "E 激进ER(er .12/.08/.25, score 40/40/40 m0)": dict(
            er_weak_min=0.08, er_trend=0.25, min_score=0,
            scoring_full_threshold=40.0, scoring_half_threshold=40.0,
            scoring_alert_threshold=40.0),
        "F 线上基线+13×5": dict(),
    }
    params = {
        "A 线上基线(er .12/.12/.3, score 50/50/55 m1)": P,
        "B 保守(er .12/.10/.25, score 50/50/55 m1)": P,
        "C 中间(er .12/.10/.25, score 45/45/45 m0)": P,
        "D 激进(er .12/.10/.25, score 40/40/40 m0)": P,
        "E 激进ER(er .12/.08/.25, score 40/40/40 m0)": P,
        "F 线上基线+13×5": {"periods": 13, "multiplier": 5.0, "src": "hl2",
                             "change_atr": True, "fast_len": 20, "slow_len": 50,
                             "ma_type": "EMA"},
    }

    print(f"\n{'配置':<44} | {'pnl':>7} | {'笔数':>4} | {'wr':>6} | {'PF':>6} | {'dd':>6} | {'拦截':>4}", flush=True)
    print("-" * 100, flush=True)
    rows = []
    for name, over in cfgs.items():
        r = S.one(params[name], S.make_cfg(**over), candles, cbtf)
        if not r:
            print(f"{name}: 回测失败", flush=True)
            continue
        rows.append((name, r))
        print(f"{name:<44} | {r['pnl']:>7.2f}U | {r['trades']:>4} | {r['wr']:>5.1f}% "
              f"| {r['pf'] if r['pf'] else float('inf'):>6.2f} | {r['dd']:>5.2f}% | {r['blocked']:>4}",
              flush=True)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_crossval_btc.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"window": f"{W_START}~{W_END}",
                   "results": [{"name": n, **r} for n, r in rows]},
                  f, ensure_ascii=False, indent=1)
    print(f"\nWrote {out} | 总耗时 {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
