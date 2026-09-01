# -*- coding: utf-8 -*-
"""BTC ER 细网格双窗口对比：当前半年(2026-03~09) vs 前半年(2025-09~2026-03)。
在线上 params(11×4) 基础上扫 er_weak × er_trend × 两种分数档，找两段都稳健的配置。"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _sweep_btc as S  # noqa: E402

P = {"periods": 11, "multiplier": 4.0, "src": "hl2", "change_atr": True,
     "fast_len": 20, "slow_len": 50, "ma_type": "EMA"}

WINDOWS = {
    "后半(26/3~9)": ("2026-03-01", "2026-09-01",
                     os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "_live_data", "btc_half_cache.json")),
    "前半(25/9~26/3)": ("2025-09-01", "2026-03-01",
                        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "_live_data", "btc_prev_half_cache.json")),
}


def load_win(name, start, end, cache_path):
    s_ms, e_ms = S.parse_ms(start), S.parse_ms(end)
    cbtf = {}
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            cached = json.load(f)
        cbtf = {t: cached[t] for t in S.BIAS_TFS if t in cached}
    if not cbtf:
        candles = S.fetch_window(S.SYM, S.TF, s_ms, e_ms, S.BARS)
        if not candles:
            return None
        cbtf = {S.TF: candles}
        for t in S.BIAS_TFS:
            if t == S.TF:
                continue
            extra = S.fetch_window(S.SYM, t, s_ms, e_ms, S.BARS_BY_TF[t])
            if extra:
                cbtf[t] = extra
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cbtf, f)
        except Exception as ex:
            print(f"缓存写盘失败: {ex}", flush=True)
    return cbtf[S.TF], cbtf


def main():
    t0 = time.time()
    data = {}
    for name, (ws, we, cp) in WINDOWS.items():
        r = load_win(name, ws, we, cp)
        if r:
            data[name] = r
            print(f"{name}: 1h={len(r[0])} 根, "
                  f"{S.ts_fmt(r[0][0]['ts'])} ~ {S.ts_fmt(r[0][-1]['ts'])}", flush=True)

    er_weaks = [0.06, 0.08, 0.10, 0.12]
    er_trends = [0.20, 0.25, 0.30]
    score_opts = {
        "线上分数50/50/55 m1": dict(min_score=1,
                                    scoring_full_threshold=50.0,
                                    scoring_half_threshold=50.0,
                                    scoring_alert_threshold=55.0),
        "放开分数40/40/40 m0": dict(min_score=0,
                                    scoring_full_threshold=40.0,
                                    scoring_half_threshold=40.0,
                                    scoring_alert_threshold=40.0),
    }

    # 基线（各自窗口的线上配置）
    print(f"\n{'ER配置':<26} | {'分数':<18} | {'后半 pnl/笔':>16} | {'前半 pnl/笔':>16}", flush=True)
    print("-" * 100, flush=True)
    rows = []
    for ew in er_weaks:
        for et in er_trends:
            for sname, sopt in score_opts.items():
                over = dict(er_weak_min=ew, er_trend=et, **sopt)
                line = {"ew": ew, "et": et, "score": sname}
                for wname, (candles, cbtf) in data.items():
                    r = S.one(P, S.make_cfg(**over), candles, cbtf)
                    line[wname] = r
                rows.append(line)

    for r in sorted(rows, key=lambda x: -(x["后半(26/3~9)"]["pnl"])):
        h = r["后半(26/3~9)"]
        p = r["前半(25/9~26/3)"]
        print(f"er={r['ew']:.2f}/{r['et']:.2f} | {r['score']:<18} | "
              f"{h['pnl']:>7.2f}U/{h['trades']:>2}笔 | "
              f"{p['pnl']:>7.2f}U/{p['trades']:>2}笔", flush=True)

    # 汇总：两段都正且稳健的配置
    print("\n=== 两段都盈利的配置 ===", flush=True)
    ok = [r for r in rows if r["后半(26/3~9)"]["pnl"] > 0 and r["前半(25/9~26/3)"]["pnl"] > 0]
    ok.sort(key=lambda x: (x["后半(26/3~9)"]["pnl"] + x["前半(25/9~26/3)"]["pnl"]),
            reverse=True)
    for r in ok[:15]:
        h, p = r["后半(26/3~9)"], r["前半(25/9~26/3)"]
        print(f"er={r['ew']:.2f}/{r['et']:.2f} | {r['score']:<18} | "
              f"后半 {h['pnl']:>7.2f}U/{h['trades']:>2}笔 PF{h['pf']} dd{h['dd']:.1f}% | "
              f"前半 {p['pnl']:>7.2f}U/{p['trades']:>2}笔 PF{p['pf']} dd{p['dd']:.1f}%",
              flush=True)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_crossval_btc_er.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1, default=str)
    print(f"\nWrote {out} | 总耗时 {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
