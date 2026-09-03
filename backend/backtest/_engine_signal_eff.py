# -*- coding: utf-8 -*-
"""Signal Efficiency 实证：验证 V1/V2 归属判据是否被数据支撑。

用户新定义：
  V1 = 低交易频率 × 高信号质量  → 成交率低 + 盈利信号占比高
  V2 = 高交易频率 × 噪声大      → 信号密 + 成交率高 + 假信号比例高

Signal Efficiency（候选口径）：
  ① 成交率  conv = v1成交数 / ST flip数
  ② 盈利信号率 eff = v1盈利单数 / ST flip数      ← 用户主公式（对 flip 全样本）
  （② = ① × wr，两个正交维度都在表里）

对 7 个已 classify 品种逐一重放 v1（配置=线上），flip 数用各自线上 ST 参数算，
与 _engine_classify.json 里的 v2 Δpnl/verdict 对照，看判据是否自洽。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import replace  # noqa: E402

from backtest import run_backtest  # noqa: E402
from indicators import super_trend  # noqa: E402
from _live_cfg_backtest import exit_rules, trade_cfg, _get, ts_fmt  # noqa: E402
from _engine_classify import CASES, LIVE_URLS, DATA, TF  # noqa: E402


def main():
    old = {}
    jp = os.path.join(DATA, "_engine_classify.json")
    if os.path.exists(jp):
        for r in json.load(open(jp, encoding="utf-8")):
            old[r["name"]] = r
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
        if sym is None or not os.path.exists(path):
            continue
        cached = json.load(open(path, encoding="utf-8"))
        cbtf = {k: v for k, v in cached.items()
                if isinstance(v, list) and v and "ts" in (v[0] or {})}
        candles = cbtf.get(TF) or cbtf.get("1H")
        if not candles or len(candles) < 200:
            continue
        p = sym["params"]
        st = super_trend([c["o"] for c in candles], [c["h"] for c in candles],
                         [c["l"] for c in candles], [c["c"] for c in candles],
                         periods=p.get("periods", 15),
                         multiplier=p.get("multiplier", 9.1),
                         src=p.get("src", "hl2"),
                         change_atr=p.get("change_atr", True))
        start = next((i for i in range(len(st["trend"]))
                      if st["trend"][i] is not None), 0)
        flips = len(st["flips"])
        eff_bars = len(st["trend"]) - start
        cfgA = trade_cfg(sym)
        common = dict(init_cash=100.0, fee_rate=0.0005, allow_short=True,
                      exit_rules=exit_rules(sym), sizing="fixed",
                      margin_usdt=sym["margin_usdt"], leverage=sym["leverage"],
                      gate_tf=TF, candles_by_tf=cbtf)
        r = run_backtest(candles, p, live_gate=cfgA, **common)
        if "error" in r:
            continue
        tr = r["trades"]
        wins = r.get("wins", 0)
        v2 = (old.get(name) or {}).get("v2_best")
        o = old.get(name) or {}
        conv = tr / flips * 100 if flips else 0.0
        eff = wins / flips * 100 if flips else 0.0
        row = dict(name=name, n=len(candles), params=f"p={p.get('periods')}×{p.get('multiplier')}",
                   flips=flips, flip100=round(flips / eff_bars * 100, 2),
                   v1_trades=tr, wins=wins, wr=r["win_rate"],
                   conv_pct=round(conv, 1), eff_pct=round(eff, 1),
                   v1_pnl=round(r["final"] - 100.0, 1),
                   v2_dpnl=round((v2["pnl"] - r["final"] + 100.0), 1) if v2 else None,
                   verdict=(o.get("verdict") or "?"))
        rows.append(row)
        print(f"{name:>6} flips={flips:>3}({row['flip100']:>4}/100) 成交={tr:>3} "
              f"wr={r['win_rate']:>4}% conv={conv:>4.0f}% eff={eff:>4.0f}% "
              f"v1={row['v1_pnl']:>6}U v2Δ={row['v2_dpnl']:>5}U → {row['verdict']}")
    with open(os.path.join(DATA, "_engine_signal_eff.json"), "w",
              encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1, default=str)
    print("\nWrote _engine_signal_eff.json")


if __name__ == "__main__":
    main()
