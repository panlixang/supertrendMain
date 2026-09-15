# -*- coding: utf-8 -*-
"""V3 阻断强度敏感性：CHASE_BLOCK 越大 = 越少阻断（999 = 等效关闭阻断）。

目的：判断 V3 表现差是"参数没调对"还是"等回踩这个机制本身不适合该品种"。
若 pnl 随 CHASE_BLOCK 单调上升并在"关闭阻断"时最好，说明阻断/等回踩有害；
若在中等阻断强度出现峰值，说明机制有效只是阈值没标定好。

用法: python _v3_sens.py [BTC|NVDA|MU|SNDK|CL]
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import replace  # noqa: E402

import v3_signal as v3  # noqa: E402
from backtest import run_backtest  # noqa: E402
from _live_cfg_backtest import exit_rules, trade_cfg, _get, ts_fmt  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data")
HOST = {"43": "http://43.108.10.84:5174/api/trade/symbols",
        "47": "http://47.84.106.154:5174/api/trade/symbols"}
CASES = [
    {"host": "43", "sym": "BTC-USDT-SWAP",  "file": "BTC.json"},
    {"host": "43", "sym": "SNDK-USDT-SWAP", "file": "SNDK.json"},
    {"host": "47", "sym": "NVDA-USDT-SWAP", "file": "NVDA.json"},
    {"host": "47", "sym": "MU-USDT-SWAP",   "file": "MU.json"},
    {"host": "47", "sym": "CL-USDT-SWAP",   "file": "cl_half_cache.json"},
]
BLOCKS = [20, 30, 40, 999]
THRS = [25, 30, 35, 45]


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
    p = sym["params"]
    base = trade_cfg(sym)
    cfgV3 = replace(base, score_engine="event_timing_v3", score_v2=False)
    common = dict(init_cash=100.0, fee_rate=0.0005, allow_short=True,
                  exit_rules=exit_rules(sym), sizing="fixed",
                  margin_usdt=sym["margin_usdt"], leverage=sym["leverage"],
                  gate_tf=tf, candles_by_tf=cbtf)
    args = sys.argv[2:]
    tag = ""
    if "--v3v1" in args:            # 底座换成 V1（V1 型品种用）
        cfgV3 = replace(cfgV3, score_engine="event_timing_v3_v1")
        tag += " [V1底座]"
    if "--nohist" in args:          # 分离历史统计的贡献：关掉 HCS，权重还给底座
        v3.W_HIST = 0.0
        v3.W_STV2 = 0.8
        tag = " [关闭历史统计 W_HIST=0]"
    if "--norisk" in args:          # 分离风险扣分的贡献
        v3.risk_penalty = lambda *a, **k: (0.0, {})
        tag += " [关闭风险扣分]"
    for a in args:                  # --thrs=50,55,60 自定义阈值网格
        if a.startswith("--thrs="):
            THRS[:] = [int(x) for x in a.split("=")[1].split(",")]
            tag += f" [thrs={THRS}]"
    print(f"== {name} V3 阻断敏感性{tag} == {ts_fmt(candles[0]['ts'])} ~ "
          f"{ts_fmt(candles[-1]['ts'])}", flush=True)

    rows = []
    for blk in BLOCKS:
        v3.CHASE_BLOCK = blk
        for thr in THRS:
            cfg = replace(cfgV3, scoring_full_threshold=float(thr),
                          scoring_half_threshold=float(thr),
                          scoring_alert_threshold=float(thr))
            r = run_backtest(candles, p, live_gate=cfg, **common)
            if "error" in r:
                continue
            tl = r["trade_list"]
            row = {"block": blk, "thr": thr, "pnl": round(r["final"] - 100, 2),
                   "dd": r["max_dd_pct"], "pf": r["profit_factor"],
                   "wr": r["win_rate"], "n": r["trades"],
                   "avg": round(sum(t["pnl_pct"] for t in tl) / len(tl), 3) if tl else None}
            rows.append(row)
            print(f"  block={blk:<4g} thr={thr:<3g}: pnl={row['pnl']}U "
                  f"dd={row['dd']}% PF={row['pf']} wr={row['wr']}% n={row['n']} "
                  f"avg={row['avg']}%", flush=True)
    ok = [x for x in rows if x["n"] >= 15]
    if ok:
        best = max(ok, key=lambda x: x["pnl"])
        print(f"\n  >> 最优: block={best['block']:g} thr={best['thr']:g} "
              f"pnl={best['pnl']}U n={best['n']} PF={best['pf']}", flush=True)
    with open(os.path.join(DATA, f"_v3_sens_{name.lower()}.json"), "w",
              encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1, default=str)
    print(f"耗时 {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
