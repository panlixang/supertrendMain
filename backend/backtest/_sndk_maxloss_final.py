# -*- coding: utf-8 -*-
"""SNDK 极端止损最终对比：对齐实盘初始止损口径(enhanced_stop=True, 3%保底+0.5ATR)
下，max_loss 3% / 5% / 8% / 关闭 的表现差异（近半年 1h 本地数据）。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest  # noqa: E402
from _live_cfg_backtest import trade_cfg, exit_rules  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LIVE = "http://43.108.10.84:5174/api/trade/symbols"
DATA = Path(__file__).parent / "_live_data" / "SNDK.json"


def main():
    with urllib.request.urlopen(urllib.request.Request(
            LIVE, headers={"User-Agent": "supertrend-bt/1.0"}), timeout=30) as resp:
        syms = json.loads(resp.read())["symbols"]
    sym = next(x for x in syms if x["symbol"] == "SNDK-USDT-SWAP")
    cfg = trade_cfg(sym)
    rules = exit_rules(sym)
    raw = json.loads(DATA.read_text(encoding="utf-8"))
    candles = raw["1h"]
    cbtf = {k: v for k, v in raw.items() if v}
    p = dict(sym["params"])

    print("exit_rules:", json.dumps({k: getattr(rules, k) for k in (
        "sl_mode", "sl_pct", "sl_buffer_atr", "sl_min_pct")}), flush=True)

    print("\n# 口径                   | trades   wr%    pnlU   PF    dd%  止损 极端止损", flush=True)
    combos = [
        ("旧口径 ST线/ml无   ", False, None),
        ("实盘口径 初损/ml无 ", True, None),
        ("实盘口径 ml=3(现) ", True, 3.0),
        ("实盘口径 ml=5      ", True, 5.0),
        ("实盘口径 ml=8      ", True, 8.0),
    ]
    for name, es, ml in combos:
        r = run_backtest(
            candles, p, init_cash=100.0, fee_rate=0.0005, allow_short=True,
            exit_rules=rules, sizing="fixed", margin_usdt=sym["margin_usdt"],
            leverage=sym["leverage"], live_gate=cfg, gate_tf="1h",
            candles_by_tf=cbtf, max_loss_pct=ml, enhanced_stop=es,
        )
        print(f"# {name:<16} | {r['trades']:5d} {r['win_rate']:6.1f} "
              f"{r['final']-100:7.2f} "
              f"{r['profit_factor'] if r['profit_factor'] else 0:5.2f} "
              f"{r['max_dd_pct']:6.2f} {r['stop_count']:4d} {r['max_loss_count']:5d}",
              flush=True)

    # 实盘口径下 ml=3 被砍单明细 vs 无 ml 时同批单的结果
    r_n = run_backtest(candles, p, init_cash=100.0, fee_rate=0.0005,
                       allow_short=True, exit_rules=rules, sizing="fixed",
                       margin_usdt=sym["margin_usdt"], leverage=sym["leverage"],
                       live_gate=cfg, gate_tf="1h", candles_by_tf=cbtf,
                       max_loss_pct=None, enhanced_stop=True)
    r_3 = run_backtest(candles, p, init_cash=100.0, fee_rate=0.0005,
                       allow_short=True, exit_rules=rules, sizing="fixed",
                       margin_usdt=sym["margin_usdt"], leverage=sym["leverage"],
                       live_gate=cfg, gate_tf="1h", candles_by_tf=cbtf,
                       max_loss_pct=3.0, enhanced_stop=True)
    cut_ts = [t["entry_ts"] for t in r_3["trade_list"]
              if any(e["reason"] == "极端止损" for e in t.get("exits", []))]
    by_ts = {t["entry_ts"]: t for t in r_n["trade_list"]}
    print(f"\n# 实盘口径 ml=3 被极端止损砍掉的 {len(cut_ts)} 笔 —— 若不开极端止损它们本来的结果:", flush=True)
    for ts in cut_ts:
        t = by_ts.get(ts)
        if t:
            print(f"  {ts} {t['side']:4s} pnl={t['pnl']:+.2f}U  bars={t['bars']}", flush=True)


if __name__ == "__main__":
    main()
