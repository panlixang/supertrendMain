# -*- coding: utf-8 -*-
"""Step 2: V2 Gate 严格 OOS 验证。

训练 03-06 标定阈值并冻结：MU40 / ETH44 / SPCX44。
测试 07-01 ~ 09-30 完全零调参。

目标不是赚钱最大，而是确认：V2 过滤掉的交易（B类）长期负期望。
判定：
  B类(V2过滤) E < 0  ->  V2 Gate 通过（确实在剔除坏单）。
附带看 A类(V2通过) 在 OOS 是否仍 >= V1（鲁棒性）。
"""
from __future__ import annotations
import json
import os
import sys
import time
from dataclasses import replace  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest  # noqa: E402
from _v2_threshold_scan import (fetch_symbols, slice_cbtf, trade_cfg,
                                exit_rules, metrics, DATA)  # noqa: E402

THR = {"MU": 40.0, "ETH": 44.0, "SPCX": 44.0}
V2_SYMS = ["MU", "ETH", "SPCX"]
OOS_LO = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp() * 1000)
OOS_HI = int(datetime(2026, 9, 30, 23, 59, tzinfo=timezone.utc).timestamp() * 1000)


def run2(s, cbtf, cfg, ex, score_only_gate=False, min_total_score=60.0):
    gate_tf = s["allow_tfs"][0]
    candles = cbtf.get(gate_tf)
    if not candles:
        return {"error": "no candles"}
    common = dict(init_cash=100.0, fee_rate=0.0005, allow_short=True,
                  exit_rules=ex, sizing="fixed",
                  margin_usdt=s["margin_usdt"], leverage=s["leverage"],
                  gate_tf=gate_tf, candles_by_tf=cbtf,
                  score_only_gate=score_only_gate,
                  min_total_score=min_total_score)
    return run_backtest(candles, s["params"], live_gate=cfg, **common)


def v2_gate_cfg(s, thr):
    return replace(trade_cfg(s), score_engine="v2",
                   scoring_full_threshold=float(thr),
                   scoring_half_threshold=float(thr),
                   scoring_alert_threshold=float(thr))


def v1_cfg(s):
    return replace(trade_cfg(s), score_engine="")


def class_metrics(trades):
    if not trades:
        return {"t": 0, "pnl": 0.0, "e": 0.0, "wr": 0.0, "pf": 0.0}
    n = len(trades)
    wins = [t["pnl_pct"] for t in trades if t["pnl_pct"] > 0]
    loss = [t["pnl_pct"] for t in trades if t["pnl_pct"] <= 0]
    wr = len(wins) / n * 100
    aw = sum(wins) / len(wins) if wins else 0.0
    al = sum(loss) / len(loss) if loss else 0.0
    e = wr / 100 * aw - (1 - wr / 100) * abs(al)
    gain = sum(wins)
    loss_abs = abs(sum(loss))
    pf = gain / loss_abs if loss_abs > 0 else (gain if gain else 0.0)
    return {"t": n, "pnl": round(sum(t["pnl_pct"] for t in trades), 2),
            "e": round(e, 3), "wr": round(wr, 1), "pf": round(pf, 2)}


def main():
    t0 = time.time()
    syms = fetch_symbols()
    sym_map = {x["symbol"].replace("-USDT-SWAP", "").replace("-USDT", ""): x
               for x in syms}
    out = {}
    comb = {"A": [], "B": [], "C": [], "V1": []}

    print("=== Step2 V2 Gate OOS (07-01~09-30, 阈值冻结 40/44/44) ===\n")
    for name in V2_SYMS:
        if name not in sym_map:
            print(f"{name}: 不在线上配置，跳过"); continue
        s = sym_map[name]
        ex = exit_rules(s)
        gate_tf = s["allow_tfs"][0]
        cache = os.path.join(DATA, f"{name}.json")
        cbtf_full = {k: v for k, v in json.load(open(cache, encoding="utf-8")).items()
                     if isinstance(v, list) and v}
        cbtf = slice_cbtf(cbtf_full, OOS_LO, OOS_HI)
        if not cbtf.get(gate_tf):
            print(f"{name}: OOS 数据不足"); continue
        thr = THR[name]

        v2r = run2(s, cbtf, v2_gate_cfg(s, thr), ex,
                   score_only_gate=True, min_total_score=thr)
        v1r = run2(s, cbtf, v1_cfg(s), ex, score_only_gate=False)
        a = v2r.get("trades_list") or []
        v1 = v1r.get("trades_list") or []
        a_ts = {t["entry_ts"] for t in a}
        v1_ts = {t["entry_ts"] for t in v1}
        b = [t for t in v1 if t["entry_ts"] not in a_ts]
        c = [t for t in a if t["entry_ts"] not in v1_ts]

        mA, mB, mC, mV1 = class_metrics(a), class_metrics(b), class_metrics(c), metrics(v1r)
        comb["A"] += a; comb["B"] += b; comb["C"] += c; comb["V1"] += v1
        out[name] = {"thr": thr, "A": mA, "B": mB, "C": mC, "V1": mV1}

        rng = f"{cbtf[gate_tf][0]['ts']}~{cbtf[gate_tf][-1]['ts']}"
        print(f"--- {name} OOS [{rng}] 阈值 {thr:g} ---")
        print(f"  {'类别':8s} {'T':>4s} {'收益%':>7s} {'E%':>7s} {'WR%':>6s} {'PF':>6s}")
        print(f"  {'V1全量':8s} {mV1['t']:>4d} {mV1['pnl']:>7.2f} {mV1['e']:>7.2f} "
              f"{mV1['wr']:>6.1f} {mV1['pf']:>6.2f}")
        print(f"  {'V2通过':8s} {mA['t']:>4d} {mA['pnl']:>7.2f} {mA['e']:>7.2f} "
              f"{mA['wr']:>6.1f} {mA['pf']:>6.2f}")
        print(f"  {'V2过滤':8s} {mB['t']:>4d} {mB['pnl']:>7.2f} {mB['e']:>7.2f} "
              f"{mB['wr']:>6.1f} {mB['pf']:>6.2f}")
        print(f"  {'V2新增':8s} {mC['t']:>4d} {mC['pnl']:>7.2f} {mC['e']:>7.2f} "
              f"{mC['wr']:>6.1f} {mC['pf']:>6.2f}")
        chk_b = "OK B<0 => V2过滤有效" if mB["e"] < 0 else "NO B>=0 => V2无效"
        chk_a = "OK A>=V1" if mA["e"] >= mV1["e"] else "WARN A<V1"
        print(f"  判定: {chk_b} (B类E={mB['e']}) | {chk_a} (A类E={mA['e']} vs V1E={mV1['e']})")

    print("\n=== OOS 跨品种汇总（合并逐笔）===")
    print(f"  {'类别':8s} {'T':>4s} {'收益%':>7s} {'E%':>7s} {'WR%':>6s} {'PF':>6s}")
    for k, lab in (("V1", "V1全量"), ("A", "V2通过"), ("B", "V2过滤"), ("C", "V2新增")):
        m = class_metrics(comb[k])
        print(f"  {lab:8s} {m['t']:>4d} {m['pnl']:>7.2f} {m['e']:>7.2f} "
              f"{m['wr']:>6.1f} {m['pf']:>6.2f}")
    cB, cA, cV1 = class_metrics(comb["B"]), class_metrics(comb["A"]), class_metrics(comb["V1"])
    print(f"\n  OOS 总判定: B类E={cB['e']} {'<0 => V2 Gate 通过' if cB['e']<0 else '>=0 => 未通过'}"
          f" | A类E={cA['e']} {'>=' if cA['e']>=cV1['e'] else '<'} V1E={cV1['e']}")

    json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
              "_v2_oos_validation.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"\n耗时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
