# -*- coding: utf-8 -*-
"""V2 Gate 泛化验证（Engine Assignment Matrix）。

目的：回答“V2 是不是只在 MU/ETH/SPCX 上有效，还是具备普适过滤能力？”

方法（与已验证的 _v2_oos_validation.py 同口径，但扩展到全部可用品种，且用
冻结阈值而非逐品种最优，避免 in-sample 偏置）：
  - 对每个品种，跑 V1(engine="") 与 V2(engine="v2", 冻结阈值)，
    用 score_only_gate 让闸门只由评分决定，孤立出“评分引擎/阈值”这一变量。
  - 把 V1 全量成交按 entry_ts 分成：
      A 类 = V2 通过（两引擎都接）
      B 类 = V2 过滤（V1 接、V2 不接）  ← 关键：若 B 类 E<0，说明 V2 确实在剔除坏单
      C 类 = V2 新增（V2 接、V1 不接）
  - 逐笔期许值 E = winrate*avg_win - lossrate*|avg_loss|（与 OOS 验证同口径，跨品种可比）。

阈值策略（泛化而非过拟合）：
  MU = 40（其线上冻结值）；其余品种统一 = 44（生产默认闸门）。
  即：直接把生产同款 V2 闸门套到每个品种，不针对任何品种调参。

适用率定义：
  V2 适用率 = (A_E >= V1_E 且 B_E < 0 的品种数) / 有效品种数
  另报“Gate 过滤有效率” = (B_E < 0 的品种数) / 有效品种数（只看过滤质量）。
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

from backtest import run_backtest  # noqa: E402
from _v2_threshold_scan import (fetch_symbols, trade_cfg, exit_rules, DATA,
                                  _get_json, LIVE)  # noqa: E402

# 可用缓存品种 -> 缓存文件名（排除下划线开头的产物文件）
FILES = {
    "BTC": "BTC.json",
    "ETH": "ETH.json",
    "MU": "MU.json",
    "SPCX": "SPCX.json",
    "SNDK": "SNDK.json",
    "NVDA": "NVDA.json",
    "QQQ": "QQQ.json",
    "SKHYNIX": "SKHYNIX.json",
    "CL": "cl_half_cache.json",
}
# 冻结阈值：MU 用其线上值 40，其余统一 44（生产默认闸门）
FROZEN_THR = {name: (40.0 if name == "MU" else 44.0) for name in FILES}


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
    # 合并两个生产节点的品种配置（不同节点交易不同品种）
    sym_map: dict[str, dict] = {}
    for url in LIVE:
        try:
            d = _get_json(url)
            if d and d.get("symbols"):
                for x in d["symbols"]:
                    k = x["symbol"].replace("-USDT-SWAP", "").replace("-USDT", "")
                    sym_map.setdefault(k, x)
        except Exception:
            continue
    if not sym_map:
        raise RuntimeError("无法从任一生产节点获取线上配置")

    print("=== V2 Gate 泛化验证 (冻结阈值 40/44, score_only_gate, A/B/C 分类) ===\n")
    rows = []
    for name, fname in FILES.items():
        if name not in sym_map:
            print(f"{name}: 线上无此品种配置，跳过"); continue
        s = sym_map[name]
        cache = os.path.join(DATA, fname)
        if not os.path.exists(cache):
            print(f"{name}: 无缓存 {fname}，跳过"); continue
        cbtf = {k: v for k, v in json.load(open(cache, encoding="utf-8")).items()
                if isinstance(v, list) and v and "ts" in (v[0] or {})}
        gate_tf = s["allow_tfs"][0]
        candles = cbtf.get(gate_tf)
        if not candles or len(candles) < 200:
            print(f"{name}: {fname} {gate_tf} 数据不足，跳过"); continue
        ex = exit_rules(s)
        thr = FROZEN_THR[name]

        v2r = run2(s, cbtf, v2_gate_cfg(s, thr), ex,
                   score_only_gate=True, min_total_score=thr)
        v1r = run2(s, cbtf, v1_cfg(s), ex, score_only_gate=False)
        if "error" in v2r or "error" in v1r:
            print(f"{name}: 回测错误 {v2r.get('error') or v1r.get('error')}"); continue
        a = v2r.get("trades_list") or []
        v1 = v1r.get("trades_list") or []
        a_ts = {t["entry_ts"] for t in a}
        v1_ts = {t["entry_ts"] for t in v1}
        b = [t for t in v1 if t["entry_ts"] not in a_ts]
        c = [t for t in a if t["entry_ts"] not in v1_ts]

        mA, mB, mC, mV1 = (class_metrics(a), class_metrics(b),
                           class_metrics(c), class_metrics(v1))
        v2_win = (mA["e"] >= mV1["e"]) and (mB["e"] < 0)
        verdict = "V2" if v2_win else "V1"
        rng = f"{candles[0]['ts']}~{candles[-1]['ts']}"
        rows.append({"name": name, "thr": thr, "start": candles[0]["ts"],
                     "end": candles[-1]["ts"], "v1": mV1, "A": mA, "B": mB,
                     "C": mC, "verdict": verdict})
        print(f"--- {name} [{rng}] 冻结阈值 {thr:g} ---")
        print(f"  {'V1全量':8s} T={mV1['t']:>4d} E%={mV1['e']:>6.2f} "
              f"WR%={mV1['wr']:>5.1f} PF={mV1['pf']:>5.2f} pnl%={mV1['pnl']:>7.2f}")
        print(f"  {'V2通过':8s} T={mA['t']:>4d} E%={mA['e']:>6.2f} "
              f"WR%={mA['wr']:>5.1f} PF={mA['pf']:>5.2f} pnl%={mA['pnl']:>7.2f}")
        print(f"  {'V2过滤':8s} T={mB['t']:>4d} E%={mB['e']:>6.2f} "
              f"WR%={mB['wr']:>5.1f} PF={mB['pf']:>5.2f} pnl%={mB['pnl']:>7.2f}  "
              f"{'B<0 OK' if mB['e']<0 else 'B>=0 无效'}")
        print(f"  {'V2新增':8s} T={mC['t']:>4d} E%={mC['e']:>6.2f} "
              f"WR%={mC['wr']:>5.1f} PF={mC['pf']:>5.2f} pnl%={mC['pnl']:>7.2f}")
        print(f"  -> 判定 {verdict}  (A_E={mA['e']} vs V1_E={mV1['e']}, "
              f"B_E={mB['e']})")

    n = len(rows)
    v2 = sum(1 for r in rows if r["verdict"] == "V2")
    gate_ok = sum(1 for r in rows if r["B"]["e"] < 0)
    a_win = sum(1 for r in rows if r["A"]["e"] >= r["v1"]["e"])
    print("\n=== Engine Assignment Matrix ===")
    print(f"  {'品种':<9s}{'thr':>5s}{'V1_E%':>8s}{'V2_E%':>8s}"
          f"{'B_E%':>8s}{'ΔE':>7s}{'选择':>5s}")
    for r in rows:
        de = r["A"]["e"] - r["v1"]["e"]
        print(f"  {r['name']:<9s}{r['thr']:>5.0f}{r['v1']['e']:>8.2f}"
              f"{r['A']['e']:>8.2f}{r['B']['e']:>8.2f}{de:>+7.2f}"
              f"{r['verdict']:>5s}")
    print(f"\n有效品种数 = {n}")
    print(f"V2 适用率 (A_E>=V1_E 且 B_E<0) = {v2}/{n} = {v2/n*100:.0f}%")
    print(f"Gate 过滤有效率 (B_E<0)        = {gate_ok}/{n} = {gate_ok/n*100:.0f}%")
    print(f"A类 E 不低于 V1 的品种数       = {a_win}/{n}")

    out = {"generated": datetime.now(timezone.utc).isoformat(),
           "method": "冻结阈值40/44 + score_only_gate + A/B/C 分类 + 逐笔E",
           "rows": rows,
           "summary": {"n": n, "v2": v2, "gate_ok": gate_ok, "a_win": a_win,
                       "v2_rate": round(v2 / n, 3) if n else 0,
                       "gate_rate": round(gate_ok / n, 3) if n else 0}}
    json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
              "_v2_generalization.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2, default=str)
    print(f"\n耗时 {time.time()-t0:.0f}s | Wrote _v2_generalization.json")


if __name__ == "__main__":
    main()
