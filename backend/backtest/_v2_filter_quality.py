# -*- coding: utf-8 -*-
"""V2 Filter Quality Test —— V2 作为 V1 信号过滤器的质量验证。

方法（无前视，两遍回测按 entry_ts 对齐）：
  Pass-1  V2过滤跑：score_engine="v2" + score_only_gate=True + min_total_score=T
          —— 对【同一批 V1 候选信号】用 V2 打分，>=T 才开仓 = A类（V2通过）
  Pass-2  V1生产跑：score_engine=""（V1 原路径，无 V2 闸门）= 全部 V1 交易

分类（按 entry_ts 对齐）：
  A类 V2通过   = Pass-1 成交（V1信号 + V2 Gate YES）
  B类 V2过滤   = Pass-2 成交但 entry_ts 不在 A类 = （V1信号 + V2 Gate NO，且 V1 本想做）
  C类 V2新增   = Pass-1 成交但 entry_ts 不在 V1   = （V2 多做的，通常因 V1 当时被占用）

理想结果：A类 E > 原V1 E；B类 E < 0  →  V2 是有效过滤器。

阈值用已标定：MU40 / ETH44 / SPCX44（仅跑 V2 适用三品种）。
"""
from __future__ import annotations
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import replace  # noqa: E402
from backtest import run_backtest  # noqa: E402
from _v2_threshold_scan import (fetch_symbols, slice_cbtf, trade_cfg,
                                exit_rules, metrics, SYMS, DATA)  # noqa: E402

THR = {"MU": 40.0, "ETH": 44.0, "SPCX": 44.0}
V2_SYMS = ["MU", "ETH", "SPCX"]


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
    return replace(trade_cfg(s), score_engine="")   # 生产 V1 路径


def class_metrics(trades):
    """从逐笔明细算 交易数/收益(累计pnl%)/Expectancy/胜率/PF。"""
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

    print("=== V2 Filter Quality Test（V2 作为 V1 过滤器）===\n")
    comb = {"A": [], "B": [], "C": [], "V1": []}
    for name in V2_SYMS:
        if name not in sym_map:
            continue
        s = sym_map[name]
        ex = exit_rules(s)
        gate_tf = s["allow_tfs"][0]
        cache = os.path.join(DATA, f"{name}.json")
        cbtf_full = {k: v for k, v in json.load(open(cache, encoding="utf-8")).items()
                     if isinstance(v, list) and v}
        thr = THR[name]

        v2r = run2(s, cbtf_full, v2_gate_cfg(s, thr), ex,
                   score_only_gate=True, min_total_score=thr)   # A类（V2通过）
        v1r = run2(s, cbtf_full, v1_cfg(s), ex,
                   score_only_gate=False)                  # 全量 V1
        a_trades = v2r.get("trades_list") or []
        v1_trades = v1r.get("trades_list") or []
        a_ts = {t["entry_ts"] for t in a_trades}
        v1_ts = {t["entry_ts"] for t in v1_trades}

        b_trades = [t for t in v1_trades if t["entry_ts"] not in a_ts]
        c_trades = [t for t in a_trades if t["entry_ts"] not in v1_ts]

        mA = class_metrics(a_trades)
        mB = class_metrics(b_trades)
        mC = class_metrics(c_trades)
        mV1 = metrics(v1r)

        comb["A"] += a_trades
        comb["B"] += b_trades
        comb["C"] += c_trades
        comb["V1"] += v1_trades
        out[name] = {"thr": thr, "A": mA, "B": mB, "C": mC, "V1": mV1,
                     "n_a": len(a_trades), "n_b": len(b_trades),
                     "n_c": len(c_trades), "n_v1": len(v1_trades)}

        print(f"--- {name} (阈值 {thr:g}, gate_tf {gate_tf}) ---")
        print(f"  {'类别':8s} {'T':>4s} {'收益%':>7s} {'E%':>7s} {'WR%':>6s} {'PF':>6s}")
        print(f"  {'V1全量':8s} {mV1['t']:>4d} {mV1['pnl']:>7.2f} {mV1['e']:>7.2f} "
              f"{mV1['wr']:>6.1f} {mV1['pf']:>6.2f}")
        print(f"  {'V2通过':8s} {mA['t']:>4d} {mA['pnl']:>7.2f} {mA['e']:>7.2f} "
              f"{mA['wr']:>6.1f} {mA['pf']:>6.2f}")
        print(f"  {'V2过滤':8s} {mB['t']:>4d} {mB['pnl']:>7.2f} {mB['e']:>7.2f} "
              f"{mB['wr']:>6.1f} {mB['pf']:>6.2f}")
        print(f"  {'V2新增':8s} {mC['t']:>4d} {mC['pnl']:>7.2f} {mC['e']:>7.2f} "
              f"{mC['wr']:>6.1f} {mC['pf']:>6.2f}")
        # 理想结果判定
        chk_a = "OK A>E" if mA["e"] > mV1["e"] else "NO A<=E"
        chk_b = "OK B<0" if mB["e"] < 0 else "NO B>=0"
        print(f"  判定: {chk_a} ({mA['e']} vs {mV1['e']}) | "
              f"{chk_b} ({mB['e']})")

    # 跨品种汇总
    print("\n=== 跨品种汇总（三品种合并逐笔）===")
    print(f"  {'类别':8s} {'T':>4s} {'收益%':>7s} {'E%':>7s} {'WR%':>6s} {'PF':>6s}")
    for k, lab in (("V1", "V1全量"), ("A", "V2通过"), ("B", "V2过滤"), ("C", "V2新增")):
        m = class_metrics(comb[k])
        print(f"  {lab:8s} {m['t']:>4d} {m['pnl']:>7.2f} {m['e']:>7.2f} "
              f"{m['wr']:>6.1f} {m['pf']:>6.2f}")
    cA, cV1, cB = class_metrics(comb["A"]), class_metrics(comb["V1"]), class_metrics(comb["B"])
    print(f"\n  总判定: A类E={cA['e']} {'>' if cA['e']>cV1['e'] else '<='} "
          f"V1E={cV1['e']} | B类E={cB['e']} {'<0 => V2有效' if cB['e']<0 else '>=0 => V2无效'}")

    json.dump(out,
              open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "_v2_filter_quality.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"\n耗时 {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
