# -*- coding: utf-8 -*-
"""SKHYNIX 在 47 配置下的回测收益。
数据用本地 SKHYNIX.json 缓存（多周期）。
分别按 47 真实 margin_usdt=50 与修正值 5 跑对照。
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import backtest_mu_home as M
from backtest_engine import run_backtest

LIVE47 = "http://47.84.106.154:5174/api/trade/symbols"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data", "SKHYNIX.json")


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "supertrend-bt/1.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def main():
    sym = next(s for s in _get(LIVE47)["symbols"] if s["symbol"] == "SKHYNIX-USDT-SWAP")
    gate_tf = sym["allow_tfs"][0]
    cbtf = json.load(open(DATA, encoding="utf-8"))
    candles = cbtf[gate_tf]
    t0 = datetime.fromtimestamp(candles[0]["ts"] / 1000, tz=timezone.utc)
    t1 = datetime.fromtimestamp(candles[-1]["ts"] / 1000, tz=timezone.utc)
    print(f"缓存区间: {t0:%Y-%m-%d} ~ {t1:%Y-%m-%d}  gate_tf={gate_tf} "
          f"se={sym.get('score_engine')!r} thr={sym['scoring_full_threshold']}/"
          f"{sym['scoring_half_threshold']}/{sym['scoring_alert_threshold']} "
          f"grades={sym['allow_grades']} min_score={sym['min_score']}", flush=True)

    PREV_LO = int(datetime(2026, 4, 1, tzinfo=timezone.utc).timestamp() * 1000)
    PREV_HI = int(datetime(2026, 6, 30, 23, 59, tzinfo=timezone.utc).timestamp() * 1000)
    REC_LO = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp() * 1000)
    REC_HI = int(candles[-1]["ts"])
    W = [("前3月 04-01~06-30", PREV_LO, PREV_HI),
         ("近3月 07-01~%s" % t1.strftime("%m-%d"), REC_LO, REC_HI),
         ("整体", candles[0]["ts"], REC_HI)]

    ex = M.exit_rules(sym)
    cfg = M.trade_cfg(sym)
    for m_label, m_val in [("47真实 margin=50", None), ("修正 margin=5", 5.0)]:
        print(f"\n##### {m_label} #####", flush=True)
        m_sym = dict(sym)
        if m_val is not None:
            m_sym["margin_usdt"] = m_val
        for label, lo, hi in W:
            seg = [c for c in candles if lo <= c["ts"] <= hi]
            cbtf_seg = {tf: [c for c in arr if lo <= c["ts"] <= hi]
                        for tf, arr in cbtf.items() if arr}
            r = run_backtest(
                seg, m_sym["params"], init_cash=100.0, fee_rate=0.0005, allow_short=True,
                exit_rules=ex, sizing="fixed", margin_usdt=m_sym["margin_usdt"],
                leverage=m_sym["leverage"], live_gate=cfg, gate_tf=gate_tf,
                candles_by_tf=cbtf_seg)
            mm = M.metrics(r)
            print(f"  [{label}] 收益={mm['pnl']}% PF={mm['pf']} 成交={mm['t']} "
                  f"胜率={mm['wr']}% 回撤={mm['dd']}% blocked={mm['blocked']}", flush=True)


if __name__ == "__main__":
    main()
