# -*- coding: utf-8 -*-
"""Silent Alpha 诊断。

问题：被 v2 过滤掉的信号，如果执行了，是赚还是亏？

方法（避免路径效应的两次回测对齐法）：
  1) 跑 v1(现行) 全量回测 → A 账本（每笔 = 一个 v1 开仓信号）。
  2) 跑 v2(重标定阈值) 回测 → 得到 B 实际开仓的 entry_ts 集合。
  3) 按 entry_ts 对齐，把 A 账本每笔分桶：
       retained = B 也开     → v2 保留的信号
       filtered = B 拒       → v2 过滤掉的信号
  4) filtered 桶净收益若为负 → 过滤正确；若显著为正 → 过度过滤（Silent Alpha 泄漏）。

阈值：取前一轮网格各自最优档 NVDA@45 / CL@55 / SKHYNIX@55。
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

LIVE_URL = "http://47.84.106.154:5174/api/trade/symbols"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data")
# (sym_key, 数据文件, v2 阈值档)
CASES = [
    ("NVDA-USDT-SWAP", "NVDA.json", 45.0),
    ("CL-USDT-SWAP", "cl_half_cache.json", 55.0),
    ("SKHYNIX-USDT-SWAP", "SKHYNIX.json", 55.0),
]
TF = "1h"


def bucket_stats(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0, "pnl_u": 0.0, "avg_pct": None, "wr": None, "pf": None,
                "best": None, "worst": None}
    ps = [t["pnl_pct"] for t in trades]
    gp = sum(x for x in ps if x > 0)
    gl = abs(sum(x for x in ps if x < 0))
    best = max(trades, key=lambda t: t["pnl_pct"])
    worst = min(trades, key=lambda t: t["pnl_pct"])
    return {
        "n": len(trades),
        "pnl_u": round(sum(t["pnl"] for t in trades), 2),
        "avg_pct": round(sum(ps) / len(ps), 3),
        "wr": round(100 * sum(1 for x in ps if x > 0) / len(ps), 1),
        "pf": round(gp / gl, 2) if gl > 0 else None,
        "best": {k: best.get(k) for k in ("entry_ts", "side", "pnl_pct")},
        "worst": {k: worst.get(k) for k in ("entry_ts", "side", "pnl_pct")},
    }


def fmt_ts(ts):
    return ts_fmt(ts) if ts else "-"


def main():
    live = _get(LIVE_URL)
    for sym_key, fname, thr in CASES:
        name = sym_key.split("-")[0]
        sym = next(s for s in live["symbols"] if s["symbol"] == sym_key)
        p = sym["params"]
        cfgA = trade_cfg(sym)
        cfgB = replace(trade_cfg(sym), score_v2=True,
                       scoring_full_threshold=thr, scoring_half_threshold=thr,
                       scoring_alert_threshold=max(cfgA.scoring_alert_threshold, thr))
        ex = exit_rules(sym)
        cached = json.load(open(os.path.join(DATA, fname), encoding="utf-8"))
        cbtf = {k: v for k, v in cached.items() if isinstance(v, list) and v}
        candles = cbtf[TF]
        common = dict(init_cash=100.0, fee_rate=0.0005, allow_short=True,
                      exit_rules=ex, sizing="fixed",
                      margin_usdt=sym["margin_usdt"], leverage=sym["leverage"],
                      gate_tf=TF, candles_by_tf=cbtf, full_trades=True)

        ra = run_backtest(candles, p, live_gate=cfgA, **common)
        rb = run_backtest(candles, p, live_gate=cfgB, **common)
        if "error" in ra or "error" in rb:
            print(f"\n== {name}: 回测失败 {ra.get('error') or rb.get('error')}")
            continue
        ta = ra["trade_list"]
        open_b = {t["entry_ts"] for t in rb["trade_list"]}
        open_a = {t["entry_ts"] for t in ta}

        # 按 entry_ts 对齐：A 账本中 B 也开的 = retained，B 拒 = filtered
        ret = [t for t in ta if t["entry_ts"] in open_b]
        fil = [t for t in ta if t["entry_ts"] not in open_b]
        only_b = [t for t in rb["trade_list"] if t["entry_ts"] not in open_a]

        sr, sf = bucket_stats(ret), bucket_stats(fil)
        sob = bucket_stats(only_b)
        # 账本核对：retained+filtered pnl 应 ≈ A 总 pnl
        s = lambda d: (f"{d['pnl_u']}U | {d['n']}笔 | avg{d['avg_pct']}% | "
                       f"wr{d['wr']}% | PF{d['pf']}" if d["n"] else "0U | 0笔")
        print(f"\n{'=' * 66}\n== {name} | v2 判档 thr={thr:g} | "
              f"[{fmt_ts(candles[0]['ts'])} ~ {fmt_ts(candles[-1]['ts'])}]\n{'=' * 66}")
        print(f"  v1 全量(A):   {s(bucket_stats(ta))}  (A 总 pnl {ra['final']-100:.2f}U)")
        print(f"  v2 全量(B):   {s(bucket_stats(rb['trade_list']))}  "
              f"(B 总 pnl {rb['final']-100:.2f}U)")
        print(f"\n  ── Silent Alpha 分桶 ──")
        print(f"  保留 retained : {s(sr)}")
        print(f"  过滤 filtered : {s(sf)}   <-- 若净亏→过滤有效; 若净赚→过度过滤")
        if only_b:
            print(f"  (注: v2 新纳入但 v1 拒的 {len(only_b)} 笔: {s(sob)})")

        verdict = ("过滤有效（去掉的是亏损贡献）" if sf["pnl_u"] < 0
                   else f"⚠ 过度过滤（filtered 净赚 {sf['pnl_u']}U）")
        print(f"\n  >> {name} 诊断: {verdict}")
        if sf["n"]:
            best = (f"{fmt_ts(sf['best']['entry_ts'])} {sf['best']['side']} "
                    f"{sf['best']['pnl_pct']}%" if sf["best"] else "-")
            worst = (f"{fmt_ts(sf['worst']['entry_ts'])} {sf['worst']['side']} "
                     f"{sf['worst']['pnl_pct']}%" if sf["worst"] else "-")
            print(f"     filtered 最好: {best}   最差: {worst}")
            if sf["pnl_u"] > 0:
                for t in sorted(fil, key=lambda x: x["pnl_pct"], reverse=True)[:5]:
                    print(f"     [漏掉的赢家] {fmt_ts(t['entry_ts'])} {t['side']} "
                          f"pnl={t['pnl_pct']}%")


if __name__ == "__main__":
    main()
