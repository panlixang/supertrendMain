# -*- coding: utf-8 -*-
"""Volatility Shock 探针：验证「大K进场 = 差单」假设（Entry Timing 前测）。

对每个品种按线上现行引擎跑完整账户回测，把每笔成交按其进场 K 的
  - body_atr  = |close-open| / ATR        （触发 K 的实体爆发度）
  - range_atr = (high-low) / ATR          （触发 K 的全幅爆发度）
  - pre_atr_ratio = ATR/前50均值ATR        （进场前的压缩/扩张状态，at i-1 时点）
分桶，统计各桶 pnl 表现。若 high-body 桶系统性差 → Shock Filter 值得做；
若压缩后大K桶(compressed breakout)最差 → 方案 1/4(Volatility Shock + Compression)
的组合得到数据支撑。

用法: python _shock_probe.py [sym关键词, 可空=全部]
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest  # noqa: E402
from _live_cfg_backtest import trade_cfg, exit_rules, _get  # noqa: E402
from indicators import super_trend  # noqa: E402
from regime import atr_volatility  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data")
CASES = [
    {"host": "43", "sym": "BTC-USDT-SWAP",   "file": "BTC.json"},
    {"host": "47", "sym": "CL-USDT-SWAP",    "file": "cl_half_cache.json"},
    {"host": "43", "sym": "SNDK-USDT-SWAP",  "file": "SNDK.json"},
    {"host": "47", "sym": "MU-USDT-SWAP",    "file": "MU.json"},
    {"host": "47", "sym": "NVDA-USDT-SWAP",  "file": "NVDA.json"},
    {"host": "47", "sym": "SKHYNIX-USDT-SWAP", "file": "SKHYNIX.json"},
    {"host": "43", "sym": "ETH-USDT-SWAP",   "file": "ETH.json"},
    {"host": "43", "sym": "SPCX-USDT-SWAP",  "file": "SPCX.json"},
]
HOST = {"43": "http://43.108.10.84:5174/api/trade/symbols",
        "47": "http://47.84.106.154:5174/api/trade/symbols"}
BODY_EDGES = [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 99.0]


def bucket(x: float, edges: list[float]) -> str:
    for lo, hi in zip(edges, edges[1:]):
        if lo <= x < hi:
            return f"{lo:g}~{hi:g}" if hi < 99 else f">={lo:g}"
    return "?"


def fmt_label(tag: str, v: float) -> str:
    if v is None:
        return f"{tag}:n/a"
    return f"{tag}:{v:.2f}"


def main():
    t0 = time.time()
    kw = sys.argv[1].upper() if len(sys.argv) > 1 else ""
    cases = [c for c in CASES if not kw or kw in c["sym"].upper()]
    live = {h: _get(url) for h, url in HOST.items()}

    for case in cases:
        sym_key = case["sym"]
        name = sym_key.split("-")[0]
        try:
            sym = next(s for s in live[case["host"]]["symbols"]
                       if s["symbol"] == sym_key)
        except StopIteration:
            print(f"\n!! {case['host']} 无 {sym_key}，跳过", flush=True)
            continue
        with open(os.path.join(DATA, case["file"]), "r", encoding="utf-8") as f:
            cached = json.load(f)
        cbtf = {k: v for k, v in cached.items() if isinstance(v, list) and v}
        candles = cbtf.get(sym["allow_tfs"][0])
        if not candles or len(candles) < 300:
            print(f"\n!! {name} 数据不足", flush=True)
            continue

        p = sym["params"]
        cfg = trade_cfg(sym)
        ex = exit_rules(sym)
        common = dict(init_cash=100.0, fee_rate=0.0005, allow_short=True,
                      exit_rules=ex, sizing="fixed",
                      margin_usdt=sym["margin_usdt"], leverage=sym["leverage"],
                      gate_tf=sym["allow_tfs"][0], candles_by_tf=cbtf)
        r = run_backtest(candles, p, live_gate=cfg, **common)
        if "error" in r:
            print(f"\n!! {name} 回测失败: {r['error']}", flush=True)
            continue
        trades = r["trade_list"]
        if not trades:
            print(f"\n{name}: 无成交", flush=True)
            continue
        st = super_trend([c["o"] for c in candles], [c["h"] for c in candles],
                         [c["l"] for c in candles], [c["c"] for c in candles],
                         periods=p.get("periods", 15),
                         multiplier=p.get("multiplier", 9.1),
                         src=p.get("src", "hl2"),
                         change_atr=p.get("change_atr", True))
        atr = st.get("atr") or []
        idx = {c["ts"]: i for i, c in enumerate(candles)}

        rows = []
        for t in trades:
            i = idx.get(t["entry_ts"])
            if i is None:
                continue
            c = candles[i]
            a = atr[i] if i < len(atr) else None
            body = abs(c["c"] - c["o"]) if a else None
            body_atr = body / a if (a and a > 0) else None
            range_atr = (c["h"] - c["l"]) / a if (a and a > 0) else None
            pre = None
            if i >= 80:
                try:
                    pre = atr_volatility(candles[:i], atr_window=14, lookback=50)
                except Exception:
                    pre = None
            rows.append({"side": t["side"], "pnl_pct": t["pnl_pct"],
                         "body_atr": body_atr, "range_atr": range_atr,
                         "pre": pre})
        if not rows:
            print(f"\n{name}: 无定位成交", flush=True)
            continue

        def stat(rs: list[dict]) -> str:
            if not rs:
                return "  0笔"
            n = len(rs)
            wins = sum(1 for x in rs if x["pnl_pct"] > 0)
            s = sum(x["pnl_pct"] for x in rs)
            return (f"  {n:2d}笔 win={wins/n*100:4.0f}% "
                    f"avg={s/n:+7.2f}% sum={s:+8.1f}")

        print(f"\n{'='*66}\n{name} [{cfg.score_engine or 'v1'}]  "
              f"ST {p['periods']}x{p['multiplier']}  {r['trades']}笔 "
              f"pnl={r['final']-100:.1f}U\n{'='*66}")
        print("进场K实体 body_atr:")
        for lo, hi in zip(BODY_EDGES, BODY_EDGES[1:]):
            rs = [x for x in rows if x["body_atr"] is not None
                  and lo <= x["body_atr"] < hi]
            if rs:
                print(f"  {fmt_label('body', lo)}~{fmt_label('', hi if hi < 99 else lo)}"
                      .replace("~n/a", "~") + stat(rs))
        print("进场K振幅 range_atr:")
        for lo, hi in zip([0.0, 1.0, 1.5, 2.0, 2.5, 3.0, 99.0],
                          [1.0, 1.5, 2.0, 2.5, 3.0, 99.0, 99.0]):
            rs = [x for x in rows if x["range_atr"] is not None
                  and lo <= x["range_atr"] < hi]
            if rs:
                tag = f"{lo:g}" if lo > 0 else "<1"
                print(f"  range>={tag}~{hi:g}" if hi < 99 else f"  range>={lo:g}",
                      end="")
                print(stat(rs))
        # 压缩×爆发 交叉
        comp = [x for x in rows if x["pre"] is not None and x["pre"] < 0.7]
        big = [x for x in rows if x["body_atr"] is not None and x["body_atr"] >= 1.5]
        cb = [x for x in rows if x["pre"] is not None and x["pre"] < 0.7
              and x["body_atr"] is not None and x["body_atr"] >= 1.5]
        print("交叉:")
        print("  压缩(pre<0.7):" + stat(comp))
        print("  大实体(body>=1.5):" + stat(big))
        print("  压缩+大实体(Shock):" + stat(cb))
        print("  其余:" + stat([x for x in rows if x not in cb]))
    print(f"\n总耗时 {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
