# -*- coding: utf-8 -*-
"""验证 47 MU（已同步 43 配置后）的回测收益，用与 43 记录一致的 前3月/近3月 窗口。
数据用本地 MU.json 缓存（1h/15m/4h/1d，截止 2026-09-15）。
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import backtest_mu_home as M  # trade_cfg / exit_rules / metrics
from backtest_engine import run_backtest

LIVE47 = "http://47.84.106.154:5174/api/trade/symbols"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data", "MU.json")


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "supertrend-bt/1.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def main():
    sym = next(s for s in _get(LIVE47)["symbols"] if s["symbol"] == "MU-USDT-SWAP")
    gate_tf = sym["allow_tfs"][0]
    cbtf = json.load(open(DATA, encoding="utf-8"))
    candles = cbtf[gate_tf]

    # 与 43 记录一致的窗口（缓存截止 09-15，近3月实际到 09-15）
    PREV_LO = int(datetime(2026, 4, 1, tzinfo=timezone.utc).timestamp() * 1000)
    PREV_HI = int(datetime(2026, 6, 30, 23, 59, tzinfo=timezone.utc).timestamp() * 1000)
    REC_LO = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp() * 1000)
    REC_HI = int(datetime(2026, 9, 15, 23, 59, tzinfo=timezone.utc).timestamp() * 1000)
    W = [("前3月 04-01~06-30", PREV_LO, PREV_HI),
         ("近3月 07-01~09-15", REC_LO, REC_HI),
         ("整体", candles[0]["ts"], REC_HI)]

    ex = M.exit_rules(sym)
    cfg = M.trade_cfg(sym)
    print(f"47 MU (已同步43) gate_tf={gate_tf} se={sym.get('score_engine')!r} "
          f"thr={sym['scoring_full_threshold']}/{sym['scoring_half_threshold']}/"
          f"{sym['scoring_alert_threshold']} max_loss={sym['exit_rules'].get('max_loss_enabled')}", flush=True)
    for label, lo, hi in W:
        seg = [c for c in candles if lo <= c["ts"] <= hi]
        cbtf_seg = {tf: [c for c in arr if lo <= c["ts"] <= hi]
                    for tf, arr in cbtf.items() if arr}
        r = run_backtest(
            seg, sym["params"], init_cash=100.0, fee_rate=0.0005, allow_short=True,
            exit_rules=ex, sizing="fixed", margin_usdt=sym["margin_usdt"],
            leverage=sym["leverage"], live_gate=cfg, gate_tf=gate_tf,
            candles_by_tf=cbtf_seg)
        m = M.metrics(r)
        print(f"  [{label}] 收益={m['pnl']}% PF={m['pf']} 成交={m['t']} "
              f"胜率={m['wr']}% 回撤={m['dd']}% blocked={m['blocked']}", flush=True)
    print("\n对照 43 记录: 前3月 8.45%/PF1.51 | 近3月 16.49%/PF3.02", flush=True)


if __name__ == "__main__":
    main()
