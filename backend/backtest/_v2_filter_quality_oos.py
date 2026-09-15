# -*- coding: utf-8 -*-
"""V2 Filter Quality —— OOS 版本（07-01 ~ 12-31，未参与阈值标定）。

在 OOS 窗口上重做 V2 Filter Quality Test：用 03-06 标定出的阈值(40/44/44)，
检验 V2 闸门在"未见过的数据"上是否真的把坏交易过滤掉了。

方法（与上位脚本一致，仅数据切到 OOS）：
  Pass-1 V2过滤跑：score_engine="v2" + score_only_gate=True + min_total_score=T
  Pass-2 V1生产跑：score_engine=""（V1 原路径，无 V2 闸门）= 全部 V1 交易
  A类 V2通过 = Pass-1 成交；B类 V2过滤 = Pass-2 有但不在 A；C类 V2新增 = A有但不在 V1

理想结果：A类 E > V1 E；B类 E < 0  =>  V2 是有效 OOS 过滤器。
末尾并排对比已有的全量(样本内)结果，看是否过拟合。
"""
from __future__ import annotations
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import replace  # noqa: E402
from _v2_filter_quality import (run2, v2_gate_cfg, v1_cfg,
                                class_metrics, THR, V2_SYMS)  # noqa: E402
from _v2_threshold_scan import (fetch_symbols, slice_cbtf, exit_rules,
                                trade_cfg, DATA)  # noqa: E402

OOS_LO = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp() * 1000)
OOS_HI = int(datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc).timestamp() * 1000)
HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    t0 = time.time()
    syms = fetch_symbols()
    sym_map = {x["symbol"].replace("-USDT-SWAP", "").replace("-USDT", ""): x
               for x in syms}
    out = {}
    comb = {"A": [], "B": [], "C": [], "V1": []}
    full = json.load(open(os.path.join(HERE, "_v2_filter_quality.json"),
                          encoding="utf-8")) if os.path.exists(
        os.path.join(HERE, "_v2_filter_quality.json")) else {}

    print(f"=== V2 Filter Quality OOS Test (07-01 ~ 12-31) ===\n")
    for name in V2_SYMS:
        if name not in sym_map:
            continue
        s = sym_map[name]
        ex = exit_rules(s)
        cache = os.path.join(DATA, f"{name}.json")
        cbtf_full = {k: v for k, v in json.load(open(cache, encoding="utf-8")).items()
                     if isinstance(v, list) and v}
        cbtf = slice_cbtf(cbtf_full, OOS_LO, OOS_HI)
        thr = THR[name]

        v2r = run2(s, cbtf, v2_gate_cfg(s, thr), ex,
                   score_only_gate=True, min_total_score=thr)
        v1r = run2(s, cbtf, v1_cfg(s), ex, score_only_gate=False)
        a_trades = v2r.get("trades_list") or []
        v1_trades = v1r.get("trades_list") or []
        a_ts = {t["entry_ts"] for t in a_trades}
        v1_ts = {t["entry_ts"] for t in v1_trades}
        b_trades = [t for t in v1_trades if t["entry_ts"] not in a_ts]
        c_trades = [t for t in a_trades if t["entry_ts"] not in v1_ts]

        mA = class_metrics(a_trades)
        mB = class_metrics(b_trades)
        mC = class_metrics(c_trades)
        mV1 = class_metrics(v1_trades)
        comb["A"] += a_trades
        comb["B"] += b_trades
        comb["C"] += c_trades
        comb["V1"] += v1_trades
        out[name] = {"thr": thr, "A": mA, "B": mB, "C": mC, "V1": mV1,
                     "n_a": len(a_trades), "n_b": len(b_trades),
                     "n_c": len(c_trades), "n_v1": len(v1_trades)}

        fr = full.get(name, {})
        fA, fB, fV1 = fr.get("A", {}), fr.get("B", {}), fr.get("V1", {})
        print(f"--- {name} OOS (阈值 {thr:g}) ---")
        print(f"  {'类别':8s} {'T':>4s} {'收益%':>7s} {'E%':>7s} {'WR%':>6s} {'PF':>6s}   "
              f"{'全量E%':>7s}")
        print(f"  {'V1全量':8s} {mV1['t']:>4d} {mV1['pnl']:>7.2f} {mV1['e']:>7.2f} "
              f"{mV1['wr']:>6.1f} {mV1['pf']:>6.2f}   {fV1.get('e','-'):>7}")
        print(f"  {'V2通过':8s} {mA['t']:>4d} {mA['pnl']:>7.2f} {mA['e']:>7.2f} "
              f"{mA['wr']:>6.1f} {mA['pf']:>6.2f}   {fA.get('e','-'):>7}")
        print(f"  {'V2过滤':8s} {mB['t']:>4d} {mB['pnl']:>7.2f} {mB['e']:>7.2f} "
              f"{mB['wr']:>6.1f} {mB['pf']:>6.2f}   {fB.get('e','-'):>7}")
        print(f"  {'V2新增':8s} {mC['t']:>4d} {mC['pnl']:>7.2f} {mC['e']:>7.2f} "
              f"{mC['wr']:>6.1f} {mC['pf']:>6.2f}")
        chk_a = "OK A>E" if mA["e"] > mV1["e"] else "NO A<=E"
        chk_b = "OK B<0" if mB["e"] < 0 else "NO B>=0"
        print(f"  判定: {chk_a} ({mA['e']} vs {mV1['e']}) | {chk_b} ({mB['e']})")

    print("\n=== 跨品种汇总（OOS 三品种合并逐笔）===")
    print(f"  {'类别':8s} {'T':>4s} {'收益%':>7s} {'E%':>7s} {'WR%':>6s} {'PF':>6s}")
    for k, lab in (("V1", "V1全量"), ("A", "V2通过"), ("B", "V2过滤"), ("C", "V2新增")):
        m = class_metrics(comb[k])
        print(f"  {lab:8s} {m['t']:>4d} {m['pnl']:>7.2f} {m['e']:>7.2f} "
              f"{m['wr']:>6.1f} {m['pf']:>6.2f}")
    cA, cV1, cB = class_metrics(comb["A"]), class_metrics(comb["V1"]), class_metrics(comb["B"])
    print(f"\n  终局判定: A类E={cA['e']} {'>' if cA['e']>cV1['e'] else '<='} "
          f"V1E={cV1['e']} | B类E={cB['e']} "
          f"{'<0 => V2 OOS 有效' if cB['e']<0 else '>=0 => V2 OOS 无效'}")

    json.dump(out,
              open(os.path.join(HERE, "_v2_filter_quality_oos.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"\n耗时 {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
