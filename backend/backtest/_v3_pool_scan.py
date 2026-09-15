# -*- coding: utf-8 -*-
"""Step 3: 扩展品种池扫描（修正版）。

正确方法（同 Filter Quality Test）：每品种跑两遍，按 entry_ts 对齐分类：
  A类 V2通过 = V2-Gate(score_only_gate=True) 成交
  B类 V2过滤 = V1生产成交 且 entry_ts 不在 A类
  C类 V2新增 = V2-Gate 成交 且 entry_ts 不在 V1
再汇总：V1 总笔数 / A类总笔数 / B类总笔数 + B类合并 Expectancy（负期望检验）。

注意：fetch_symbols 只取首服务器，这里取两服务器并集。
阈值：MU40/ETH44/SPCX44 已标定；其余默认 40（标注 未标定，仅看样本量与方向）。
缺失（无配置无数据）：AMD TSLA META AAPL SOL Gold，需另行采集。
"""
from __future__ import annotations
import json
import os
import sys
import time
import urllib.request
from dataclasses import replace  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest  # noqa: E402
from _v2_threshold_scan import trade_cfg, exit_rules, metrics, DATA  # noqa: E402

URLS = ["http://43.108.10.84:5174/api/trade/symbols",
        "http://47.84.106.154:5174/api/trade/symbols"]
POOL = ["BTC", "ETH", "MU", "SNDK", "SPCX", "NVDA", "SKHYNIX", "CL"]
DATA_FILE = {"CL": "cl_half_cache.json"}
THR = {"MU": 40.0, "ETH": 44.0, "SPCX": 44.0}
DEF_THR = 40.0


def fetch_all():
    syms, seen = [], set()
    for u in URLS:
        try:
            d = json.loads(urllib.request.urlopen(
                urllib.request.Request(u, headers={"User-Agent": "x"}), timeout=20).read())
            for s in d.get("symbols", []):
                if s["symbol"] not in seen:
                    seen.add(s["symbol"]); syms.append(s)
        except Exception as e:
            print("fetch err", u, e)
    return syms


def run2(s, cbtf, cfg, ex, score_only_gate=False, min_total_score=60.0):
    gate_tf = s["allow_tfs"][0]
    candles = cbtf.get(gate_tf)
    if not candles:
        return {"error": "no candles"}
    common = dict(init_cash=100.0, fee_rate=0.0005, allow_short=True,
                  exit_rules=ex, sizing="fixed",
                  margin_usdt=s["margin_usdt"], leverage=s["leverage"],
                  gate_tf=gate_tf, candles_by_tf=cbtf,
                  score_only_gate=score_only_gate,
                  min_total_score=min_total_score)
    return run_backtest(candles, s["params"], live_gate=cfg, **common)


def cm(trades):
    if not trades:
        return {"t": 0, "pnl": 0.0, "e": 0.0, "wr": 0.0, "pf": 0.0}
    n = len(trades)
    wins = [t["pnl_pct"] for t in trades if t["pnl_pct"] > 0]
    loss = [t["pnl_pct"] for t in trades if t["pnl_pct"] <= 0]
    wr = len(wins) / n * 100
    aw = sum(wins) / len(wins) if wins else 0.0
    al = sum(loss) / len(loss) if loss else 0.0
    e = wr / 100 * aw - (1 - wr / 100) * abs(al)
    gain = sum(wins); loss_abs = abs(sum(loss))
    pf = gain / loss_abs if loss_abs > 0 else (gain if gain else 0.0)
    return {"t": n, "pnl": round(sum(t["pnl_pct"] for t in trades), 2),
            "e": round(e, 3), "wr": round(wr, 1), "pf": round(pf, 2)}


def main():
    t0 = time.time()
    syms = fetch_all()
    sub_map = {x["symbol"].replace("-USDT-SWAP", "").replace("-USDT", ""): x
               for x in syms}
    out = {}
    agg = {"V1": [], "A": [], "B": [], "C": []}
    print("=== Step3 扩展池（按 entry_ts 对齐分 A/B/C）===\n")
    print(f"{'SYM':8s} {'标*':4s} {'V1_T':>5s} {'A_T':>5s} {'B_T':>5s} {'C_T':>5s} "
          f"{'B_E':>7s} {'B_PF':>6s}")
    for name in POOL:
        s = sub_map.get(name)
        if not s:
            print(f"{name}: 无 live 配置，跳过"); continue
        ex = exit_rules(s)
        cache = os.path.join(DATA, DATA_FILE.get(name, f"{name}.json"))
        if not os.path.exists(cache):
            print(f"{name}: 数据缺失 {cache}"); continue
        cbtf = {k: v for k, v in json.load(open(cache, encoding="utf-8")).items()
                if isinstance(v, list) and v}
        thr = THR.get(name, DEF_THR)
        a = run2(s, cbtf, replace(trade_cfg(s), score_engine="v2",
                  scoring_full_threshold=thr, scoring_half_threshold=thr,
                  scoring_alert_threshold=thr), ex,
                 score_only_gate=True, min_total_score=thr).get("trades_list") or []
        v1 = run2(s, cbtf, replace(trade_cfg(s), score_engine=""), ex).get("trades_list") or []
        a_ts = {t["entry_ts"] for t in a}
        v1_ts = {t["entry_ts"] for t in v1}
        b = [t for t in v1 if t["entry_ts"] not in a_ts]
        c = [t for t in a if t["entry_ts"] not in v1_ts]
        mA, mB, mC, mV1 = cm(a), cm(b), cm(c), metrics({"trades": len(v1)})
        # metrics() needs full result; recompute V1 E via cm on v1 list:
        mV1 = cm(v1)
        agg["V1"] += v1; agg["A"] += a; agg["B"] += b; agg["C"] += c
        out[name] = {"thr": thr, "calibrated": name in THR,
                     "V1": mV1, "A": mA, "B": mB, "C": mC}
        print(f"{name:8s} {'*' if name in THR else ' ':4s} {mV1['t']:>5d} "
              f"{mA['t']:>5d} {mB['t']:>5d} {mC['t']:>5d} "
              f"{mB['e']:>7.2f} {mB['pf']:>6.2f}")

    cv1, cA, cB = cm(agg["V1"]), cm(agg["A"]), cm(agg["B"])
    print(f"\n=== 汇总（全池，全历史 03~09，按 entry_ts 对齐）===")
    print(f"  V1 全量总交易数        : {cv1['t']}")
    print(f"  V2通过(A类)总交易数    : {cA['t']}")
    print(f"  V2过滤(B类)总交易数    : {cB['t']}")
    print(f"  B类合并 E={cB['e']}  PF={cB['pf']}  -> "
          f"{'负期望 OK V2过滤有效' if cB['e'] < 0 else '非负 NO'}")
    print(f"  目标 V1>=1000 笔 : {'达成' if cv1['t'] >= 1000 else '未达成, 缺口 %d 笔' % (1000 - cv1['t'])}")
    print(f"  注: BTC/SNDK/NVDA/SKHYNIX/CL 用默认阈值40(未标定)，仅看样本量与方向")

    json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
              "_v3_pool_scan.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"\n耗时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
