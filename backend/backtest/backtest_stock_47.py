# -*- coding: utf-8 -*-
"""股票品种 首页 47 配置回测（NVDA / SKHYNIX 等）。

复用 47 线上真实配置：ST + 评分引擎(V1/V2) + range_filter + 三档 TP + max_loss。
数据用本地缓存 {NAME}.json（NAME 大写，如 NVDA.json / SKHYNIX.json）。
窗口按数据实际中点切前后半（整体也给出），不依赖固定日期。
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
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data")


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "supertrend-bt/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def ts_fmt(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def main():
    t0 = time.time()
    names = [a.upper() for a in (sys.argv[1:] or ["NVDA", "SKHYNIX"])]
    d = _get(LIVE47)
    for name in names:
        syms = [x for x in d["symbols"] if x["symbol"].startswith(name)]
        if not syms:
            print(f"!! {name} 不在 47 配置", flush=True)
            continue
        sym = syms[0]
        gate_tf = sym["allow_tfs"][0]
        cache = os.path.join(DATA_DIR, name + ".json")
        if not os.path.exists(cache):
            print(f"!! {cache} 缓存缺失", flush=True)
            continue
        cbtf = json.load(open(cache, encoding="utf-8"))
        candles = cbtf[gate_tf]
        er = sym["exit_rules"]
        cur_se = sym.get("score_engine")
        def _canon(se):
            s = (se or "").lower()
            return "v2" if ("v2" in s or s == "quality_filter_v2") else "v1"
        cur_canon = _canon(cur_se)
        other = "" if cur_canon == "v2" else "v2"
        tag_cur = "V2" if cur_canon == "v2" else "V1"
        tag_other = "V1" if tag_cur == "V2" else "V2"
        print(f"\n##### {sym['symbol']} (gate_tf={gate_tf}) "
              f"lev={sym['leverage']} margin={sym['margin_usdt']} #####", flush=True)
        print(f"  params={sym['params']}", flush=True)
        print(f"  score_engine={cur_se!r} "
              f"thr={sym['scoring_full_threshold']}/{sym['scoring_half_threshold']}/"
              f"{sym['scoring_alert_threshold']} dyn={sym['use_dynamic_threshold']}", flush=True)
        print(f"  range={sym['range_filter_enabled']} "
              f"max_loss={er.get('max_loss_enabled')}@{er.get('max_loss_pct')}%", flush=True)
        print(f"  K {gate_tf}: {ts_fmt(candles[0]['ts'])} ~ {ts_fmt(candles[-1]['ts'])} "
              f"n={len(candles)}", flush=True)

        mid = len(candles) // 2
        lo_all = candles[0]["ts"]
        hi_all = candles[-1]["ts"]
        mid_ts = candles[mid]["ts"]
        WINDOWS = [("前半", lo_all, mid_ts), ("后半", mid_ts, hi_all), ("整体", lo_all, hi_all)]

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

        print(f"  -- 当前 47 口径 ({tag_cur}) --", flush=True)
        cur = run_one(None)
        print(f"  -- {tag_other} 对照 (同阈值) --", flush=True)
        oth = run_one(other)
        print(f"  {tag_cur}={json.dumps(cur, ensure_ascii=False)}")
        print(f"  {tag_other}={json.dumps(oth, ensure_ascii=False)}")
    print(f"\n总耗时 {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
