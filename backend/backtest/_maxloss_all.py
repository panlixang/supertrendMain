# -*- coding: utf-8 -*-
"""所有线上品种极端止损(max_loss)误损诊断（与 SNDK 同法）。

实盘口径 enhanced_stop=True（初损=max(sl_pct, ST线∓0.5ATR) 的 3% 保底）下，
对比 不开极端止损 vs 线上 max_loss 值 的表现：被砍笔数、被砍单本来盈亏。
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
DATA_DIR = Path(__file__).parent / "_live_data"


def main():
    req = urllib.request.Request(
        LIVE, headers={"User-Agent": "supertrend-bt/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        live = json.loads(resp.read())
    syms = live["symbols"]

    print(f"线上共 {len(syms)} 个品种\n", flush=True)
    for sym in syms:
        name = sym["symbol"].replace("-USDT-SWAP", "").replace("-USDT", "")
        er = exit_rules(sym)
        tf = sym["allow_tfs"][0]
        print(f"== {name} | tf={tf} | sl_mode={er.sl_mode} sl_pct={er.sl_pct}% "
              f"min={er.sl_min_pct}% buf={er.sl_buffer_atr}ATR | "
              f"max_loss={'ON' if er.max_loss_enabled else 'OFF'} "
              f"{er.max_loss_pct}%", flush=True)

        data_path = DATA_DIR / f"{name}.json"
        if not data_path.exists():
            print("   (无本地数据，跳过)\n", flush=True)
            continue
        raw = json.loads(data_path.read_text(encoding="utf-8"))
        if tf not in raw or not raw[tf]:
            print(f"   (json 无 {tf} 数据，跳过)\n", flush=True)
            continue
        candles = raw[tf]
        if len(candles) < 400:
            print(f"   (数据不足 {len(candles)} 根，跳过)\n", flush=True)
            continue
        cbtf = {k: v for k, v in raw.items() if isinstance(v, list) and v}
        p = dict(sym["params"])

        def run(ml):
            return run_backtest(
                candles, p, init_cash=100.0, fee_rate=0.0005, allow_short=True,
                exit_rules=er, sizing="fixed",
                margin_usdt=sym["margin_usdt"], leverage=sym["leverage"],
                live_gate=trade_cfg(sym), gate_tf=tf,
                candles_by_tf=cbtf, max_loss_pct=ml, enhanced_stop=True,
            )

        r_none = run(None)
        if not er.max_loss_enabled:
            print(f"   pnl={r_none['final']-100:.2f}U trades={r_none['trades']} "
                  f"wr={r_none['win_rate']:.1f}% (极端止损未启用，无对比)\n", flush=True)
            continue
        r_ml = run(er.max_loss_pct)
        cut = [t for t in r_ml["trade_list"]
               if any(e["reason"] == "极端止损" for e in t.get("exits", []))]
        by_ts = {t["entry_ts"]: t for t in r_none["trade_list"]}
        # 被砍单"如果没有极端止损"本来结果
        if cut:
            w = [by_ts[t["entry_ts"]] for t in cut
                 if by_ts.get(t["entry_ts"]) and by_ts[t["entry_ts"]]["pnl"] > 0]
            ls = [by_ts[t["entry_ts"]] for t in cut
                  if by_ts.get(t["entry_ts"]) and by_ts[t["entry_ts"]]["pnl"] <= 0]
            wsum = sum(x["pnl"] for x in w)
            lsum = sum(x["pnl"] for x in ls)
            wdesc = f"其中{len(w)}笔本应盈利(+{wsum:.2f}U), {len(ls)}笔本会亏损({lsum:.2f}U)"
        else:
            wdesc = "无极端止损触发"
        print(f"   ml关闭: pnl={r_none['final']-100:7.2f}U wr={r_none['win_rate']:5.1f}% "
              f"dd={r_none['max_dd_pct']:5.2f}% stops={r_none['stop_count']}", flush=True)
        print(f"   ml={er.max_loss_pct:.0f}% : pnl={r_ml['final']-100:7.2f}U "
              f"wr={r_ml['win_rate']:5.1f}% dd={r_ml['max_dd_pct']:5.2f}% "
              f"stops={r_ml['stop_count']} 极端止损x{len(cut)} | {wdesc}", flush=True)
        print(f"   损失 = {r_ml['final']-r_none['final']:+.2f}U\n", flush=True)


if __name__ == "__main__":
    main()
