# -*- coding: utf-8 -*-
"""CL 首页 47 配置回测：复用 47 线上 CL-USDT-SWAP 真实配置。

首页口径 = ST(19,2.5) + V1 阶梯引擎(空串默认回退, thr 50/50/40, 动态) + range_filter
          + 三档 TP(1.2/3.0/3.5) + 保本 + 跟随ST + max_loss 3%
数据用本地 cl_half_cache.json (2026-03-04 ~ 2026-08-31)。
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import backtest_mu_home as M  # 复用 trade_cfg / exit_rules / metrics
from backtest_engine import run_backtest

LIVE47 = "http://47.84.106.154:5174/api/trade/symbols"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data", "cl_half_cache.json")


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "supertrend-bt/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def ts_fmt(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def main():
    t0 = time.time()
    sym = [s for s in _get(LIVE47)["symbols"] if s["symbol"] == "CL-USDT-SWAP"][0]
    gate_tf = sym["allow_tfs"][0]
    cbtf = json.load(open(DATA, encoding="utf-8"))
    candles = cbtf[gate_tf]
    er = sym["exit_rules"]
    print(f"\n##### CL-USDT-SWAP (gate_tf={gate_tf}) "
          f"lev={sym['leverage']} margin={sym['margin_usdt']} #####", flush=True)
    print(f"  params={sym['params']}", flush=True)
    print(f"  score_engine={sym.get('score_engine')!r} "
          f"thr={sym['scoring_full_threshold']}/{sym['scoring_half_threshold']}/"
          f"{sym['scoring_alert_threshold']} dyn={sym['use_dynamic_threshold']}", flush=True)
    print(f"  range_filter={sym['range_filter_enabled']} "
          f"max_loss={er.get('max_loss_enabled')}@{er.get('max_loss_pct')}%", flush=True)
    print(f"  K {gate_tf}: {ts_fmt(candles[0]['ts'])} ~ {ts_fmt(candles[-1]['ts'])} "
          f"n={len(candles)}", flush=True)

    PREV_LO = int(datetime(2026, 3, 4, tzinfo=timezone.utc).timestamp() * 1000)
    PREV_HI = int(datetime(2026, 5, 31, 23, 59, tzinfo=timezone.utc).timestamp() * 1000)
    REC_LO = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)
    REC_HI = int(datetime(2026, 8, 31, 23, 59, tzinfo=timezone.utc).timestamp() * 1000)
    WINDOWS = [("前半 03-04~05-31", PREV_LO, PREV_HI),
               ("后半 06-01~08-31", REC_LO, REC_HI),
               ("整体", PREV_LO, REC_HI)]

    ex = M.exit_rules(sym)

    def run_one(se_force):
        s = dict(sym)
        if se_force is not None:
            s["score_engine"] = se_force
        cfg = M.trade_cfg(s)
        res = {}
        for label, lo, hi in WINDOWS:
            seg = [c for c in candles if lo <= c["ts"] <= hi]
            cbtf_seg = {tf: [c for c in arr if lo <= c["ts"] <= hi]
                        for tf, arr in cbtf.items() if arr}
            if len(seg) < 100:
                print(f"    [{label}] K 不足 ({len(seg)})", flush=True)
                continue
            r = run_backtest(
                seg, s["params"], init_cash=100.0, fee_rate=0.0005, allow_short=True,
                exit_rules=ex, sizing="fixed", margin_usdt=sym["margin_usdt"],
                leverage=sym["leverage"], live_gate=cfg, gate_tf=gate_tf,
                candles_by_tf=cbtf_seg)
            m = M.metrics(r)
            res[label] = m
            if "error" in m:
                print(f"    [{label}] error: {m['error']}", flush=True)
                continue
            print(f"    [{label}] 收益={m['pnl']}% PF={m['pf']} 成交={m['t']} "
                  f"胜率={m['wr']}% 回撤={m['dd']}% blocked={m['blocked']} "
                  f"tp1/2/3={m['tp1']}/{m['tp2']}/{m['tp3']} stops={m['stops']}",
                  flush=True)
        return res

    print("  -- 当前 47 口径 (V1 阶梯引擎, 空串回退) --", flush=True)
    v1 = run_one(None)
    print("  -- V2 对照 (quality_filter_v2 连续软分, 同阈值) --", flush=True)
    v2 = run_one("v2")
    print(f"\n总耗时 {time.time() - t0:.0f}s", flush=True)
    print("v1=", json.dumps(v1, ensure_ascii=False))
    print("v2=", json.dumps(v2, ensure_ascii=False))


if __name__ == "__main__":
    main()
