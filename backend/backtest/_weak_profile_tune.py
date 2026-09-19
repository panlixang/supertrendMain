# -*- coding: utf-8 -*-
"""对指定品种做弱档 quick.tp1 寻优。

网格 tp1 ∈ [0.6,0.8,1.0,1.2,1.5,1.8,2.0,2.5,3.0]，每个品种：
  - OFF(弱ER走标准出场) 跑一次
  - ON(quick出场, tp1 取网格值, 关价格止损) 逐点跑
输出每品种全曲线 + 两个推荐值：
  - in-sample 最优 tp1 (整体 pnl 最大)
  - 拐点 tp1 (达到 ≥90% 最大增益的最小 tp1, 抗过拟合的稳健取值)
import 复用 _weak_profile_matrix 的取数/回测逻辑。
"""
from __future__ import annotations
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _live_cfg_backtest import _get, trade_cfg, exit_rules  # noqa: E402
from _weak_profile_matrix import (make_quick, run_sym, summarize,  # noqa: E402
                                  LIVE_URLS, CACHE, DATA)

SYMS = ["BTC", "ETH", "SPCX", "NVDA", "CL"]
TP1_GRID = [0.6, 0.8, 1.0, 1.2, 1.5, 1.8, 2.0, 2.5, 3.0]


def main():
    t0 = time.time()
    sym_map: dict[str, dict] = {}
    for url in LIVE_URLS:
        try:
            d = _get(url)
            for x in d.get("symbols", []):
                sym_map.setdefault(x["symbol"].replace("-USDT-SWAP", "").replace("-USDT", ""), x)
        except Exception:
            continue

    results = {}
    for name in SYMS:
        if name not in sym_map or name not in CACHE:
            print(f"{name}: 缺失, 跳过"); continue
        sym = sym_map[name]
        cbtf = {k: v for k, v in json.load(open(os.path.join(DATA, CACHE[name]), encoding="utf-8")).items()
                if isinstance(v, list) and v and "ts" in (v[0] or {})}
        cfg = trade_cfg(sym)
        normal = exit_rules(sym)
        off = summarize(run_sym(sym, cbtf, cfg, normal, normal))
        if off is None or off["n_quick"] == 0:
            print(f"{name}: 无弱ER成交, 跳过"); continue
        off_pnl = off["overall"]["pnl"]
        curve = []
        for tp1 in TP1_GRID:
            s = summarize(run_sym(sym, cbtf, cfg, normal, make_quick(tp1)))
            if s is None:
                continue
            curve.append({"tp1": tp1, "pnl": s["overall"]["pnl"],
                          "e_quick": s["quick_seg"]["e"], "n_quick": s["quick_seg"]["t"],
                          "gain": s["overall"]["pnl"] - off_pnl})
        if not curve:
            continue
        opt = max(curve, key=lambda c: c["pnl"])
        opt_gain = opt["gain"]
        # 拐点: 达到 ≥90% 最大增益的最小 tp1
        knee = None
        for c in curve:
            if c["gain"] >= 0.9 * opt_gain and c["gain"] > 0:
                knee = c["tp1"]; break
        knee = knee or opt["tp1"]
        results[name] = {"off_pnl": off_pnl, "curve": curve,
                         "opt_tp1": opt["tp1"], "opt_pnl": opt["pnl"],
                         "knee_tp1": knee,
                         "knee_pnl": next(c["pnl"] for c in curve if c["tp1"] == knee),
                         "opt_gain": opt_gain}
        print(f"\n--- {name} (OFF整体pnl%={off_pnl}) ---")
        print(f"  {'tp1':>5s}{'整体pnl%':>10s}{'Δ':>8s}{'弱ER_E':>9s}{'n':>4s}")
        for c in curve:
            mark = " *" if c["tp1"] == opt["tp1"] else (" <" if c["tp1"] == knee else "")
            print(f"  {c['tp1']:>5g}{c['pnl']:>10.2f}{c['gain']:>+8.2f}{c['e_quick']:>9.3f}{c['n_quick']:>4d}{mark}")
        print(f"  => in-sample最优 tp1={opt['tp1']:g}(pnl%={opt['pnl']:.2f}, Δ={opt_gain:+.2f}) | "
              f"稳健拐点 tp1={knee:g}(pnl%={results[name]['knee_pnl']:.2f})")

    print("\n=== 弱档 tp1 寻优汇总 ===")
    print(f"  {'品种':<6s}{'OFF':>8s}{'in-sample最优':>14s}{'拐点(稳健)':>14s}")
    for n, r in results.items():
        print(f"  {n:<6s}{r['off_pnl']:>8.2f}{('tp1='+str(r['opt_tp1']) if isinstance(r['opt_tp1'],(int,float)) else r['opt_tp1']):>14s}{('tp1='+str(r['knee_tp1'])):>14s}")
    print(f"\n耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
