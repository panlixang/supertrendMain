# -*- coding: utf-8 -*-
"""V2 权重消融实验（Step 2：特征排序能力）。

冻结架构，只动各评分因子的权重。做法：
- monkey-patch regime_scoring._parts_v2：把某分项置 0，其余按原容量比例
  重新归一化到 100，使闸门阈值仍选相同头部比例 —— 干净的特征消融。
- 对 3 个 V2 品种（MU/ETH/SPCX），固定候选阈值（40/44/44），跑：
    BASE       全因子（==原版）
    NO_SIGNAL  signal_quality 移除
    NO_ATR     volatility(ATR) 移除
    NO_4H      mtf_alignment 移除
    NO_ER      er_momentum 移除
    NO_BREAK   breakout_boost 移除
- 目的不是看谁赚钱最多，而是看：哪个因子被移除后 OOS 质量掉得最多
  （E/PF 跌幅大 = 该因子提供真实排序能力）。
全历史 03~09 一起跑以保证样本量（权重未在任何数据上拟合，无前视）。
"""
from __future__ import annotations
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import regime_scoring                                  # noqa: E402
from _v2_threshold_scan import (fetch_symbols, slice_cbtf, run, trade_cfg,
                                exit_rules, metrics, SYMS, DATA)  # noqa: E402

# 各分项原始容量（与 _parts_v2 一致）
FACTOR_MAX = {
    "signal_quality": 30.0,
    "er_momentum": 25.0,
    "volatility": 15.0,
    "mtf_alignment": 20.0,
    "breakout_boost": 20.0,
}

_ORIG_PARTS_V2 = regime_scoring._parts_v2
_WEIGHTS = None  # dict: factor->0 表示移除；None 或全 1 = 原版


def _patched_parts_v2(sig, candles, cfg, candles_by_tf=None, p=None):
    bd, reasons, total, detail = _ORIG_PARTS_V2(
        sig, candles, cfg, candles_by_tf, p)
    if _WEIGHTS is None:
        return bd, reasons, total, detail
    active = {f: m for f, m in FACTOR_MAX.items() if _WEIGHTS.get(f, 1) != 0}
    s = sum(active.values())
    new = 0.0
    for f, m in FACTOR_MAX.items():
        if f not in active:
            continue
        new += bd.get(f, 0.0) * (100.0 / s)
    pen = bd.get("penalties", 0.0)
    new_total = max(0.0, min(100.0, new + pen))
    return bd, reasons, round(new_total, 1), detail


regime_scoring._parts_v2 = _patched_parts_v2

CAND = {"MU": 40.0, "ETH": 44.0, "SPCX": 44.0}
V2_SYMS = ["MU", "ETH", "SPCX"]
ABLATIONS = [
    ("BASE",      {}),
    ("NO_SIGNAL", {"signal_quality": 0}),
    ("NO_ATR",    {"volatility": 0}),
    ("NO_4H",     {"mtf_alignment": 0}),
    ("NO_ER",     {"er_momentum": 0}),
    ("NO_BREAK",  {"breakout_boost": 0}),
]


def main():
    t0 = time.time()
    syms = fetch_symbols()
    sym_map = {x["symbol"].replace("-USDT-SWAP", "").replace("-USDT", ""): x
               for x in syms}

    results = {}
    for name in V2_SYMS:
        if name not in sym_map:
            print(f"{name}: 不在线上配置", flush=True)
            continue
        s = sym_map[name]
        ex = exit_rules(s)
        gate_tf = s["allow_tfs"][0]
        cache = os.path.join(DATA, f"{name}.json")
        if not os.path.exists(cache):
            print(f"{name}: 缓存缺失", flush=True)
            continue
        cbtf_full = {k: v for k, v in json.load(open(cache, encoding="utf-8")).items()
                     if isinstance(v, list) and v}
        candles = cbtf_full.get(gate_tf)
        print(f"\n=== {name} ({gate_tf}, {len(candles)} 根) 候选阈值 "
              f"{CAND[name]:.0f} ===", flush=True)
        print(f"{'ABLATION':11s} {'T':>4s} {'E%':>7s} {'PF':>6s} {'WR%':>6s} "
              f"{'DD%':>6s} {'PnL':>8s}", flush=True)

        base = None
        sym_res = {}
        for an, w in ABLATIONS:
            global _WEIGHTS
            _WEIGHTS = w if w else None
            cfg = replace_cfg(s, CAND[name])
            r = run(s, cbtf_full, cfg, ex)
            m = metrics(r)
            _WEIGHTS = None
            sym_res[an] = m
            tag = "  <-- BASE" if an == "BASE" else ""
            print(f"{an:11s} {m['t']:>4d} {m['e']:>7.2f} {m['pf']:>6.2f} "
                  f"{m['wr']:>6.1f} {m['dd']:>6.1f} {m['pnl']:>8.2f}{tag}", flush=True)
        results[name] = sym_res

    # ---- 排序能力汇总：移除某因子后 ΔE / ΔPF（相对 BASE）----
    print("\n=== 排序能力汇总（移除因子相对 BASE 的 ΔE / ΔPF，负号=退化=该因子有用）===")
    print(f"{'FACTOR':11s} " + " ".join(f"{s:>14s}" for s in V2_SYMS)
          + "   | 平均ΔE", flush=True)
    for fac in ["signal_quality", "volatility", "mtf_alignment",
                "er_momentum", "breakout_boost"]:
        an = {"signal_quality": "NO_SIGNAL", "volatility": "NO_ATR",
              "mtf_alignment": "NO_4H", "er_momentum": "NO_ER",
              "breakout_boost": "NO_BREAK"}[fac]
        cells, avg = [], 0.0
        for s in V2_SYMS:
            b = results[s]["BASE"]
            d = results[s][an]
            de = round(d["e"] - b["e"], 3)
            cells.append(f"ΔE={de:+.2f}/ΔPF={d['pf']-b['pf']:+.2f}")
            avg += de
        avg /= len(V2_SYMS)
        print(f"{fac:11s} " + " ".join(f"{c:>14s}" for c in cells)
              + f"   | {avg:+.3f}", flush=True)

    # 排名：平均 ΔE 越小（越负）说明该因子越重要
    rank = []
    for fac in ["signal_quality", "volatility", "mtf_alignment",
                "er_momentum", "breakout_boost"]:
        an = {"signal_quality": "NO_SIGNAL", "volatility": "NO_ATR",
              "mtf_alignment": "NO_4H", "er_momentum": "NO_ER",
              "breakout_boost": "NO_BREAK"}[fac]
        avg = sum(results[s][an]["e"] - results[s]["BASE"]["e"]
                 for s in V2_SYMS) / len(V2_SYMS)
        rank.append((fac, avg))
    rank.sort(key=lambda x: x[1])
    print("\n因子重要性排名（平均ΔE 升序，越负=越提供排序能力）:")
    for i, (fac, avg) in enumerate(rank, 1):
        print(f"  {i}. {fac:14s} 平均ΔE={avg:+.3f}", flush=True)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "_v2_weight_ablation.json")
    json.dump(results, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\nWrote {out} | 耗时 {time.time() - t0:.0f}s", flush=True)


def replace_cfg(s, thr):
    from dataclasses import replace
    return replace(trade_cfg(s), score_engine="v2",
                   scoring_full_threshold=thr,
                   scoring_half_threshold=thr,
                   scoring_alert_threshold=thr)


if __name__ == "__main__":
    main()
