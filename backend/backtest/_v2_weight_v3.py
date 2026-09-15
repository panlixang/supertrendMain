# -*- coding: utf-8 -*-
"""V2.1 权重重构 + Score 分桶排序验证。

冻结架构，只动各评分因子的权重（显式权重，合计=100）：
  V2-A 当前        Signal30 ATR15 4H20 ER25 Breakout10
  V2-B 趋势增强    Signal10 ATR15 4H25 ER40 Breakout10
  V2-C 极简Alpha   Signal0  ATR20 4H30 ER50 Breakout0

对 MU/ETH/SPCX 固定候选阈值(40/44/44)跑，比较 E/PF/DD/T；
同时做 Score Bucket Test：把成交按 score 分桶，看"分数越高→未来收益越高"
是否成立（Alpha Ranker），还是只是阈值游戏。

无前视：score 只在信号产生那根 candles[:i+1] 上算，按 entry_ts 反查。
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

FACTOR_CAP = {
    "signal_quality": 30.0,
    "er_momentum": 25.0,
    "volatility": 15.0,
    "mtf_alignment": 20.0,
    "breakout_boost": 20.0,
}
_ORIG = regime_scoring._parts_v2
_WEIGHTS = None          # dict factor->weight(可为0); None=原版
_SCORE_BY_TS = {}        # 信号根 ts -> total_score（供分桶反查）


def _patched(sig, candles, cfg, candles_by_tf=None, p=None):
    bd, reasons, total, detail = _ORIG(sig, candles, cfg, candles_by_tf, p)
    ts = candles[-1]["ts"] if candles else None
    if _WEIGHTS is None:
        if ts is not None:
            _SCORE_BY_TS[ts] = round(total, 1)
        return bd, reasons, total, detail
    new = 0.0
    for f, cap in FACTOR_CAP.items():
        w = _WEIGHTS.get(f, cap)
        new += bd.get(f, 0.0) * (w / cap)
    pen = bd.get("penalties", 0.0)
    new_total = max(0.0, min(100.0, new + pen))
    new_total = round(new_total, 1)
    if ts is not None:
        _SCORE_BY_TS[ts] = new_total
    return bd, reasons, new_total, detail


regime_scoring._parts_v2 = _patched

CONFIGS = {
    "V2-A": {"signal_quality": 30.0, "volatility": 15.0, "mtf_alignment": 20.0,
             "er_momentum": 25.0, "breakout_boost": 10.0},
    "V2-B": {"signal_quality": 10.0, "volatility": 15.0, "mtf_alignment": 25.0,
             "er_momentum": 40.0, "breakout_boost": 10.0},
    "V2-C": {"signal_quality": 0.0,  "volatility": 20.0, "mtf_alignment": 30.0,
             "er_momentum": 50.0, "breakout_boost": 0.0},
}
THR = {"MU": 40.0, "ETH": 44.0, "SPCX": 44.0}
V2_SYMS = ["MU", "ETH", "SPCX"]


def rankdata(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs, ys):
    n = len(xs)
    if n < 4:
        return None
    rx, ry = rankdata(xs), rankdata(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = (sum((a - mx) ** 2 for a in rx)) ** 0.5
    dy = (sum((b - my) ** 2 for b in ry)) ** 0.5
    return round(num / (dx * dy), 3) if dx and dy else None


def bucket_stats(pairs, thr):
    """pairs: list of (score, pnl). 按 [thr, thr+5) ... 分桶统计。"""
    bins = [thr + 5 * k for k in range(5)] + [100.0]   # 5 段: thr~thr+5 ... 60+
    res = []
    for b in range(len(bins) - 1):
        lo, hi = bins[b], bins[b + 1]
        grp = [p for s, p in pairs if lo <= s < hi]
        if not grp:
            res.append((lo, hi, 0, None, None, None))
            continue
        wins = [x for x in grp if x > 0]
        losses = [x for x in grp if x <= 0]
        aw = sum(wins) / len(wins) if wins else 0.0
        al = sum(losses) / len(losses) if losses else 0.0
        e = (len(wins) / len(grp)) * aw - (1 - len(wins) / len(grp)) * abs(al)
        pf = sum(wins) / abs(sum(losses)) if losses else (sum(wins) if wins else 0.0)
        res.append((lo, hi, len(grp), round(len(wins) / len(grp) * 100, 1),
                    round(e, 3), round(pf, 2)))
    return res


def main():
    t0 = time.time()
    syms = fetch_symbols()
    sym_map = {x["symbol"].replace("-USDT-SWAP", "").replace("-USDT", ""): x
               for x in syms}
    results = {}

    # ① 三配置 × 三品种：E/PF/DD/T
    print("=== 权重重构对比（固定候选阈值，全历史 03~09）===\n")
    for name in V2_SYMS:
        s = sym_map[name]
        ex = exit_rules(s)
        gate_tf = s["allow_tfs"][0]
        cache = os.path.join(DATA, f"{name}.json")
        cbtf_full = {k: v for k, v in json.load(open(cache, encoding="utf-8")).items()
                     if isinstance(v, list) and v}
        thr = THR[name]
        sym_res = {}
        print(f"--- {name} (阈值 {thr:g}) ---")
        print(f"{'CFG':6s} {'T':>4s} {'E%':>7s} {'PF':>6s} {'WR%':>6s} {'DD%':>6s}")
        for cn, w in CONFIGS.items():
            global _WEIGHTS, _SCORE_BY_TS
            _WEIGHTS = w
            _SCORE_BY_TS = {}
            cfg = replace_cfg(s, thr)
            r = run(s, cbtf_full, cfg, ex)
            m = metrics(r)
            # 反查每笔 score
            tl = r.get("trades_list") or []
            pairs = []
            miss = 0
            for tr in tl:
                sc = _SCORE_BY_TS.get(tr.get("entry_ts"))
                if sc is None:
                    miss += 1
                    continue
                pairs.append((sc, tr.get("pnl_pct", 0.0)))
            _WEIGHTS = None
            sym_res[cn] = {"m": m, "pairs": pairs, "miss": miss}
            print(f"{cn:6s} {m['t']:>4d} {m['e']:>7.2f} {m['pf']:>6.2f} "
                  f"{m['wr']:>6.1f} {m['dd']:>6.1f}  (分桶样本 {len(pairs)}, 缺失 {miss})")
        results[name] = sym_res

    # ② Score Bucket Test（排序能力验证）
    print("\n=== Score Bucket Test（分数越高→收益越高?）===\n")
    rank_summary = {}
    for name in V2_SYMS:
        rank_summary[name] = {}
        for cn in CONFIGS:
            res = results[name][cn]
            pairs = res["pairs"]
            if not pairs:
                continue
            thr = THR[name]
            bs = bucket_stats(pairs, thr)
            corr = spearman([s for s, _ in pairs], [p for _, p in pairs])
            rank_summary[name][cn] = corr
            print(f"{name} / {cn}  Spearman(score,pnl)={corr}")
            print(f"  {'桶区间':>10s} {'T':>4s} {'WR%':>6s} {'E%':>7s} {'PF':>6s}")
            for lo, hi, t, wr, e, pf in bs:
                tag = f"[{lo:.0f},{hi if hi<100 else 99:.0f}]"
                if t == 0:
                    print(f"  {tag:>10s} {t:>4d}   -     -     -")
                else:
                    print(f"  {tag:>10s} {t:>4d} {wr:>6.1f} {e:>7.2f} {pf:>6.2f}")
            # 单调性：E 是否随桶升序上升
            es = [e for _, _, t, _, e, _ in bs if t > 0]
            mono = sum(1 for a, b in zip(es, es[1:]) if b >= a)
            print(f"  -> E 单调上升段: {mono}/{len(es)-1 if len(es)>1 else 0}"
                  f"  (1=完全单调, 越高越像 Alpha Ranker)\n")

    # ③ 排序能力汇总：跨品种平均 |Spearman|
    print("=== 排序能力汇总（跨品种平均 |Spearman(score,pnl)|，越高=越像 Alpha Ranker）===")
    for cn in CONFIGS:
        vals = [abs(rank_summary[s][cn]) for s in V2_SYMS if rank_summary[s].get(cn) is not None]
        avg = sum(vals) / len(vals) if vals else 0.0
        print(f"  {cn}: 平均|rho|={avg:.3f}  (各品种: "
              + ", ".join(f"{s}={rank_summary[s].get(cn)}" for s in V2_SYMS) + ")")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "_v2_weight_v3.json")
    json.dump({n: {c: {"m": results[n][c]["m"], "miss": results[n][c]["miss"],
                      "spearman": rank_summary[n][c]}
                   for c in CONFIGS} for n in V2_SYMS},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\nWrote {out} | 耗时 {time.time() - t0:.0f}s", flush=True)


def replace_cfg(s, thr):
    from dataclasses import replace
    return replace(trade_cfg(s), score_engine="v2",
                   scoring_full_threshold=thr,
                   scoring_half_threshold=thr,
                   scoring_alert_threshold=thr)


if __name__ == "__main__":
    main()
