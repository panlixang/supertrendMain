# -*- coding: utf-8 -*-
"""单一对照实验（方案 A）：扩大测试窗，严格独立 OOS。

训练区 2026-03~06 选出的阈值完全锁定，测试区 07-01~09-30（缓存实际到 09-15）
绝不再调参。只比较 3 个配置：

  ① V1           = 原版 SuperTrend（score_engine="v1"，阶梯式闸门）
  ② V2 Old       = 当前线上配置原样（含线上阈值/引擎，作为“在线 V2”基准）
  ③ V2 Candidate = score_engine="v2" + 上一轮候选阈值
                   MU=40 ETH=44 SPCX=44 SNDK=56 BTC=36（全=半=提醒）

5 个品种全跑，测试窗一致、互不调参。仅做校准对照，不新增策略模块。
"""
from __future__ import annotations
import json
import os
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _v2_threshold_scan import (fetch_symbols, slice_cbtf, metrics, run,
                                trade_cfg, exit_rules, SYMS, DATA)

TS = lambda y, m, d, h=0, mi=0: int(
    datetime(y, m, d, h, mi, tzinfo=timezone.utc).timestamp() * 1000)

TEST_LO = TS(2026, 7, 1)
TEST_HI = TS(2026, 9, 30, 23, 59)  # 数据实际到 09-15，自动截断
CAND = {"MU": 40.0, "ETH": 44.0, "SPCX": 44.0, "SNDK": 56.0, "BTC": 36.0}


def fmt(m):
    return (f"T={m['t']:>3} E={m['e']:>6.2f} PF={m['pf']:>5.2f} "
            f"WR={m['wr']:>5.1f} DD={m['dd']:>5.1f} PnL={m['pnl']:>7.2f}")


def main():
    t0 = time.time()
    syms = fetch_symbols()
    sym_map = {x["symbol"].replace("-USDT-SWAP", "").replace("-USDT", ""): x
               for x in syms}

    print(f"{'SYM':5s} {'CONFIG':12s} {'ENGINE':6s} {'FULL':>4s} "
          f"{'T':>4s} {'E%':>7s} {'PF':>6s} {'WR%':>5s} {'DD%':>5s} {'PnL':>8s}",
          flush=True)
    print("-" * 80, flush=True)

    results = {}
    for name in SYMS:
        if name not in sym_map:
            print(f"{name:5s} 不在线上配置", flush=True)
            continue
        s = sym_map[name]
        ex = exit_rules(s)
        gate_tf = s["allow_tfs"][0]
        cache = os.path.join(DATA, f"{name}.json")
        if not os.path.exists(cache):
            print(f"{name:5s} 缓存缺失", flush=True)
            continue
        cbtf_full = {k: v for k, v in json.load(open(cache, encoding="utf-8")).items()
                     if isinstance(v, list) and v}
        cbtf_test = slice_cbtf(cbtf_full, TEST_LO, TEST_HI)
        tcs = cbtf_test.get(gate_tf)
        if not tcs or len(tcs) < 50:
            print(f"{name:5s} 测试窗数据不足", flush=True)
            continue
        lo = datetime.fromtimestamp(tcs[0]["ts"] / 1000, tz=timezone.utc).strftime("%m-%d")
        hi = datetime.fromtimestamp(tcs[-1]["ts"] / 1000, tz=timezone.utc).strftime("%m-%d")

        base = trade_cfg(s)
        cfg_v1 = replace(base, score_engine="v1")
        cfg_old = base  # 线上原样 = V2 Old
        t = CAND[name]
        cfg_cand = replace(base, score_engine="v2",
                           scoring_full_threshold=t,
                           scoring_half_threshold=t,
                           scoring_alert_threshold=t)

        m_v1 = metrics(run(s, cbtf_test, cfg_v1, ex))
        m_old = metrics(run(s, cbtf_test, cfg_old, ex))
        m_cand = metrics(run(s, cbtf_test, cfg_cand, ex))

        print(f"{name:5s} V1           v1     -    {fmt(m_v1)}", flush=True)
        print(f"{name:5s} V2Old        {base.score_engine or 'v1':6s} "
              f"{base.scoring_full_threshold:>4.0f}  {fmt(m_old)}", flush=True)
        print(f"{name:5s} V2Candidate  v2     {t:>4.0f}  {fmt(m_cand)}", flush=True)
        print(f"      (测试窗 {lo}~{hi})", flush=True)

        results[name] = {
            "test_window": [lo, hi],
            "online_engine": base.score_engine or "v1",
            "online_full_threshold": base.scoring_full_threshold,
            "candidate_threshold": t,
            "V1": m_v1, "V2Old": m_old, "V2Candidate": m_cand,
        }

    # 汇总：每个品种 OOS 最优配置
    print("=" * 80, flush=True)
    print("汇总（测试窗 07-01~09-15，三配置对照）：", flush=True)
    print(f"{'SYM':5s} {'V1_E':>6s} {'V2Old_E':>8s} {'V2Cand_E':>9s} "
          f"{'V2Old_PF':>9s} {'V2Cand_PF':>10s} {'OOS最优':>10s}", flush=True)
    best_counts = {"V1": 0, "V2Old": 0, "V2Candidate": 0}
    for name, d in results.items():
        v1, vo, vc = d["V1"], d["V2Old"], d["V2Candidate"]
        # 仅 PF>1 且 E>0 才算有效；否则取 E 最高
        cands = {"V1": v1, "V2Old": vo, "V2Candidate": vc}
        valid = {k: m for k, m in cands.items() if m["e"] > 0 and m["pf"] > 1}
        best = max(valid, key=lambda k: cands[k]["e"]) if valid else \
            max(cands, key=lambda k: cands[k]["e"])
        best_counts[best] += 1
        print(f"{name:5s} {v1['e']:>6.2f} {vo['e']:>8.2f} {vc['e']:>9.2f} "
              f"{vo['pf']:>9.2f} {vc['pf']:>10.2f} {best:>10s}", flush=True)
    print(f"\nOOS 最优计数: V1={best_counts['V1']} "
          f"V2Old={best_counts['V2Old']} V2Candidate={best_counts['V2Candidate']}",
          flush=True)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "_v2_threeway.json")
    json.dump(results, open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"\nWrote {out} | 耗时 {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
