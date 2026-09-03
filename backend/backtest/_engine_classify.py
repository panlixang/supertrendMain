# -*- coding: utf-8 -*-
"""阶段6 品种 × Engine 分类实验。

对 43.108 服务器在跑品种（BTC/ETH/MU/SPCX/SNDK）+ 冻结三品种，用各自线上
v1 配置作基线，再跑 v2 引擎的阈值网格，输出"该品种归 v1 还是 v2"证据表：
  - v2 相对 v1 的 Δpnl / ΔPF / Δ笔数
  - 按样本量给可信度标记（n<30 弱结论）

数据用 _live_data 缓存（同窗口内 v1/v2 对照可比）。窗口打印出来便于评估。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import replace  # noqa: E402

from backtest import run_backtest  # noqa: E402
from _live_cfg_backtest import exit_rules, trade_cfg, _get, ts_fmt  # noqa: E402

# (sym_key, 缓存文件)  —— 43.108 第一批
CASES = [
    ("BTC-USDT-SWAP", "BTC.json"),
    ("ETH-USDT-SWAP", "ETH.json"),
    ("MU-USDT-SWAP", "MU.json"),
    ("SPCX-USDT-SWAP", "SPCX.json"),
    ("SNDK-USDT-SWAP", "SNDK.json"),
    ("NVDA-USDT-SWAP", "NVDA.json"),     # 参照：已知 v2 品种
    ("CL-USDT-SWAP", "cl_half_cache.json"),   # 参照：已知 v1 品种
]
LIVE_URLS = ["http://43.108.10.84:5174/api/trade/symbols",
             "http://47.84.106.154:5174/api/trade/symbols"]
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data")
V2_THRS = [40.0, 45.0, 50.0, 55.0, 60.0]
TF = "1h"


def summarize(r):
    return {"pnl": r["final"] - 100.0, "pf": r["profit_factor"],
            "wr": r["win_rate"], "n": r["trades"],
            "dd": r["max_dd_pct"]}


def main():
    rows = []
    for sym_key, fname in CASES:
        name = sym_key.split("-")[0]
        sym = None
        for url in LIVE_URLS:
            try:
                live = _get(url)
            except Exception:
                continue
            sym = next((s for s in live["symbols"] if s["symbol"] == sym_key), None)
            if sym:
                break
        path = os.path.join(DATA, fname)
        if sym is None:
            print(f"== {name}: 两台服务器均无此品种配置，跳过")
            continue
        if not os.path.exists(path):
            print(f"== {name}: 无缓存数据 {fname}，跳过")
            continue
        cached = json.load(open(path, encoding="utf-8"))
        cbtf = {k: v for k, v in cached.items() if isinstance(v, list) and v and "ts" in (v[0] or {})}
        candles = cbtf.get(TF) or cbtf.get("1H")
        if not candles or len(candles) < 200:
            print(f"== {name}: {fname} 无 {TF} 数据 / 不足")
            continue
        cfgA = trade_cfg(sym)
        ex = exit_rules(sym)
        common = dict(init_cash=100.0, fee_rate=0.0005, allow_short=True,
                      exit_rules=ex, sizing="fixed",
                      margin_usdt=sym["margin_usdt"], leverage=sym["leverage"],
                      gate_tf=TF, candles_by_tf=cbtf)

        def one(cfg):
            r = run_backtest(candles, sym["params"], live_gate=cfg, **common)
            return summarize(r) if "error" not in r else None

        sa = one(cfgA)
        if sa is None:
            continue
        best = None
        for thr in V2_THRS:
            ct = replace(cfgA, score_engine="quality_filter_v2",
                         scoring_full_threshold=thr, scoring_half_threshold=thr,
                         scoring_alert_threshold=max(cfgA.scoring_alert_threshold, thr))
            st = one(ct)
            if st and (best is None or st["pnl"] > best["pnl"]):
                best = {**st, "thr": thr}
        win_v2 = best is not None and best["pnl"] > sa["pnl"] + 0.5
        verdict = "v2" if win_v2 else ("v1" if sa["pnl"] > (best or {"pnl": -9e9})["pnl"] + 0.5 else "≈打平")
        rows.append({
            "name": name, "start": ts_fmt(candles[0]["ts"]), "end": ts_fmt(candles[-1]["ts"]),
            "v1": sa, "v2_best": best, "verdict": verdict,
        })
        print(f"\n== {name} [{ts_fmt(candles[0]['ts'])} ~ {ts_fmt(candles[-1]['ts'])}] "
              f"→ 倾向 {verdict}")
        print(f"   v1 线上: pnl={sa['pnl']:.2f}U PF={sa['pf']} wr={sa['wr']}% n={sa['n']} dd={sa['dd']}%")
        if best:
            print(f"   v2 最优(引擎=quality_filter_v2 @{best['thr']:g}): "
                  f"pnl={best['pnl']:.2f}U PF={best['pf']} wr={best['wr']}% "
                  f"n={best['n']} dd={best['dd']}%  Δpnl={best['pnl']-sa['pnl']:+.2f}U")

    with open(os.path.join(DATA, "_engine_classify.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1, default=str)
    print("\nWrote _engine_classify.json")


if __name__ == "__main__":
    main()
