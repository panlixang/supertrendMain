# -*- coding: utf-8 -*-
"""受影响品种(MU/ETH/SNDK) max_loss 档位扫描：关闭 vs 3 vs 5 vs 8。"""
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
DATA_DIR = Path(__file__).parent / "_live_data"
WANT = {"MU", "ETH", "SNDK"}


def main():
    req = urllib.request.Request(
        LIVE, headers={"User-Agent": "supertrend-bt/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        syms = json.loads(resp.read())["symbols"]
    for sym in syms:
        name = sym["symbol"].replace("-USDT-SWAP", "").replace("-USDT", "")
        if name not in WANT:
            continue
        er = exit_rules(sym)
        tf = sym["allow_tfs"][0]
        raw = json.loads((DATA_DIR / f"{name}.json").read_text(encoding="utf-8"))
        candles = raw[tf]
        cbtf = {k: v for k, v in raw.items() if isinstance(v, list) and v}
        p = dict(sym["params"])
        print(f"== {name} | 线上 max_loss={er.max_loss_pct}% ==", flush=True)

        def run(ml):
            return run_backtest(
                candles, p, init_cash=100.0, fee_rate=0.0005, allow_short=True,
                exit_rules=er, sizing="fixed",
                margin_usdt=sym["margin_usdt"], leverage=sym["leverage"],
                live_gate=trade_cfg(sym), gate_tf=tf,
                candles_by_tf=cbtf, max_loss_pct=ml, enhanced_stop=True,
            )

        print("  ml档  | trades   wr%    pnlU   PF    dd%  极端止损", flush=True)
        for ml in (None, 3.0, 5.0, 6.0, 8.0):
            r = run(ml)
            label = "关闭" if ml is None else f"{ml:.0f}%"
            print(f"  {label:<5} | {r['trades']:5d} {r['win_rate']:6.1f} "
                  f"{r['final']-100:7.2f} "
                  f"{r['profit_factor'] if r['profit_factor'] else 0:5.2f} "
                  f"{r['max_dd_pct']:6.2f} {r['max_loss_count']:5d}", flush=True)
        print("", flush=True)


if __name__ == "__main__":
    main()
