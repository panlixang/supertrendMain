# -*- coding: utf-8 -*-
"""SNDK 候选配置双窗口验证：全窗口 / 前半 / 后半，挑前后半段都稳定的。"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _sweep_sndk import one, make_cfg, SYM, TF, BARS, BIAS_TFS, BARS_BY_TF  # noqa: E402
from _live_cfg_backtest import fetch_candles, ts_fmt  # noqa: E402

# (label, periods, multiplier, er_over)
CANDIDATES = [
    ("当前 23×2.5", 23, 2.5, {}),
    ("13×3.0", 13, 3.0, {}),
    ("15×3.0", 15, 3.0, {}),
    ("15×2.5", 15, 2.5, {}),
    ("11×3.0", 11, 3.0, {}),
    ("17×3.5", 17, 3.5, {}),
]


def run_on(candles, cbtf, per, mul):
    p = {"periods": per, "multiplier": mul, "src": "hl2", "change_atr": True,
         "fast_len": 20, "slow_len": 50, "ma_type": "EMA"}
    return one(p, make_cfg(), candles, cbtf)


def main():
    raw = fetch_candles(SYM, TF, BARS)
    mid = len(raw) // 2
    w1, w2 = raw[:mid], raw[mid:]
    full_cbtf = {TF: raw}
    cbtf1: dict[str, list] = {TF: w1}
    cbtf2: dict[str, list] = {TF: w2}
    for t in BIAS_TFS:
        if t == TF:
            continue
        extra = fetch_candles(SYM, t, BARS_BY_TF[t])
        if extra:
            full_cbtf[t] = extra
            cbtf1[t] = [c for c in extra if c["ts"] <= w1[-1]["ts"]]
            cbtf2[t] = [c for c in extra if c["ts"] > w1[-1]["ts"]]
    print(f"全窗口 {ts_fmt(raw[0]['ts'])} ~ {ts_fmt(raw[-1]['ts'])} ({len(raw)}根)", flush=True)
    print(f"前半 {ts_fmt(w1[0]['ts'])} ~ {ts_fmt(w1[-1]['ts'])} ({len(w1)}根)", flush=True)
    print(f"后半 {ts_fmt(w2[0]['ts'])} ~ {ts_fmt(w2[-1]['ts'])} ({len(w2)}根)", flush=True)

    rows = []
    for label, per, mul, over in CANDIDATES:
        cfg = make_cfg(**over)
        r_all = one({"periods": per, "multiplier": mul, "src": "hl2",
                     "change_atr": True, "fast_len": 20, "slow_len": 50,
                     "ma_type": "EMA"}, cfg, raw, full_cbtf)
        r1 = one({"periods": per, "multiplier": mul, "src": "hl2",
                  "change_atr": True, "fast_len": 20, "slow_len": 50,
                  "ma_type": "EMA"}, cfg, w1, cbtf1)
        r2 = one({"periods": per, "multiplier": mul, "src": "hl2",
                  "change_atr": True, "fast_len": 20, "slow_len": 50,
                  "ma_type": "EMA"}, cfg, w2, cbtf2)
        if not (r_all and r1 and r2):
            print(f"  {label}: 无结果", flush=True)
            continue
        rows.append({"label": label, "all": r_all, "h1": r1, "h2": r2})
        print(
            f"\n{label}:\n"
            f"  全窗口  pnl={r_all['pnl']}U trades={r_all['trades']} "
            f"wr={r_all['wr']}% PF={r_all['pf']} dd={r_all['dd']}%\n"
            f"  前半    pnl={r1['pnl']}U trades={r1['trades']} "
            f"wr={r1['wr']}% PF={r1['pf']} dd={r1['dd']}%\n"
            f"  后半    pnl={r2['pnl']}U trades={r2['trades']} "
            f"wr={r2['wr']}% PF={r2['pf']} dd={r2['dd']}%",
            flush=True,
        )

    out = os.path.join(os.path.dirname(__file__), "_sndk_window.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {out}", flush=True)


if __name__ == "__main__":
    main()
