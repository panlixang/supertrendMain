# -*- coding: utf-8 -*-
"""V2 Filter Quality —— OOS 验证（生产闸门模式，score_only_gate=False）。

与 _v2_filter_quality_oos.py 的唯一区别：去掉 score_only_gate=True，
改用生产环境真实闸门：score_engine="v2" + scoring_full=half=alert=thr
（V2 引擎内部以 half_threshold 为入场闸门）。这才是能用于上线的终局 A/B。

依据 backtest.py:340 注释，实盘默认不拦（executor 只看分数），score_only_gate=True
是“假设关弱档”的非生产回测开关；故生产模式 = 阈值闸门（score_only_gate 默认 False）。

窗口 07-01 ~ 12-31（未参与阈值标定）。阈值用已标定 40/44/44。
分类（按 entry_ts 对齐）：
  A类 V2通过 = Pass-1 成交（V2 Gate YES）
  B类 V2过滤 = Pass-2 有但不在 A（V1 想做但被 V2 拦）
  C类 V2新增 = Pass-1 有但不在 V1
理想：A类 E > V1 E；B类 E < 0 => V2 是有效过滤器。
"""
from __future__ import annotations
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _v2_filter_quality import (run2, v2_gate_cfg, v1_cfg,
                                class_metrics, THR, V2_SYMS)  # noqa: E402
from _v2_threshold_scan import (fetch_symbols, slice_cbtf,
                                exit_rules, DATA)  # noqa: E402

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

    print("=== V2 Filter Quality OOS (生产闸门模式, score_only_gate=False) ===\n")
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

        # Pass-1 生产闸门：score_engine="v2" + 阈值闸门（score_only_gate 默认 False）
        v2r = run2(s, cbtf, v2_gate_cfg(s, thr), ex)
        # Pass-2 V1 全量（无 V2 闸门）
        v1r = run2(s, cbtf, v1_cfg(s), ex)
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
        print(f"--- {name} OOS 生产闸门 (阈值 {thr:g}) ---")
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

    print("\n=== 跨品种汇总（OOS 三品种合并逐笔, 生产闸门）===")
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
              open(os.path.join(HERE, "_v2_filter_quality_oos_prod.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"\n耗时 {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
