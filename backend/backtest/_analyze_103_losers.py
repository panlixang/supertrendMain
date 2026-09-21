"""分析 10×3 版回测的亏损单：为什么没被分数拦截挡掉。

对每笔成交复算过闸瞬间的：
  - 综合分数 total_score（score_signal，与实盘同一套）
  - 评级 grade（A/B/C）
  - ER 状态（tradable? er>=er_min）
  - 区间过滤状态（是否处于被拦截的窄幅震荡）
  - MAE/MFE（入场后最大不利/有利偏移）
并对比 亏损单 vs 盈利单 的分数分布，判断拦截失效的性质。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _live_cfg_btc_103 as L
from backtest import run_backtest
from indicators import super_trend, st_signals
from strategy import mtf_bias, grade, evaluate
from regime import efficiency_ratio, classify, range_bound
from regime_scoring import score_signal
from integration import enhanced_signal_handler
from regime import TradeConfig

LIVE_URL = "http://43.108.10.84:5174/api/trade/symbols"


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "bt/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def trunc_cbtf(cbtf, ts):
    out = {}
    for tf, arr in cbtf.items():
        sl = [c for c in arr if c["ts"] <= ts]
        if sl:
            out[tf] = sl
    return out or {next(iter(cbtf)): list(cbtf.values())[0][:1]}


def main():
    live = _get(LIVE_URL)
    sym = [s for s in live["symbols"] if "BTC" in s["symbol"].upper()][0]
    gate_tf = sym["allow_tfs"][0]
    bars = L.BARS.get(gate_tf, 4500)

    candles = L.fetch_candles(sym["symbol"], gate_tf, bars)
    cbtf = {gate_tf: candles}
    for tf in L.BIAS_TFS:
        if tf == gate_tf:
            continue
        ex = L.fetch_candles(sym["symbol"], tf, min(bars, L.BARS.get(tf, 4500)))
        if ex:
            cbtf[tf] = ex

    p = dict(L.OVERRIDE_ST)
    # 先跑一遍拿到全部成交
    res = run_backtest(
        candles, p, init_cash=100.0, fee_rate=0.0005, allow_short=True,
        exit_rules=L.exit_rules(sym), sizing="fixed",
        margin_usdt=sym["margin_usdt"], leverage=sym["leverage"],
        live_gate=L.trade_cfg(sym), gate_tf=gate_tf, candles_by_tf=cbtf,
        full_trades=True,
    )
    if "error" in res:
        print("ERR", res["error"])
        return
    trades = res["trade_list"]

    # 计算超级趋势翻转信号
    st = super_trend([c["o"] for c in candles], [c["h"] for c in candles],
                     [c["l"] for c in candles], [c["c"] for c in candles],
                     periods=p["periods"], multiplier=p["multiplier"],
                     src=p["src"], change_atr=p["change_atr"])
    sigs = st_signals(candles, st, gate_tf)
    verdict_full = mtf_bias(cbtf, p)["verdict"]
    sig_by_ts = {}
    for s in sigs:
        s["grade"] = grade(s, verdict_full)
        sig_by_ts.setdefault(s["ts"], s)

    cfg = L.trade_cfg(sym)
    er_min = cfg.er_min
    range_size_max = cfg.range_size_max * 100

    ts_idx = {c["ts"]: i for i, c in enumerate(candles)}

    rows = []
    for t in trades:
        ets = t["entry_ts"]
        i = ts_idx.get(ets)
        if i is None:
            continue
        side = "buy" if t["side"] == "long" else "sell"
        sig = sig_by_ts.get(ets)
        if not sig or sig["type"] != side:
            continue
        cbtf_t = trunc_cbtf(cbtf, ets)
        full = evaluate(cbtf_t, p, sig)
        gate = enhanced_signal_handler(
            full, candles[: i + 1], cfg, candles_by_tf=cbtf_t, p=p,
            use_momentum=True, use_false_filter=True, use_adaptive=True)
        sc = score_signal(full, candles[: i + 1], cfg, candles_by_tf=cbtf_t, p=p)
        er = efficiency_ratio(candles[: i + 1])
        er_ok = (er is not None and er >= er_min)
        rc = range_bound(candles[: i + 1])
        rng_pct = rc.get("range_size_pct", 999)
        touches = rc.get("touches", 0)
        # 区间过滤本应拦截的条件（与 engine 一致）
        rng_block = (rng_pct < range_size_max) and (touches >= cfg.range_touches_min)

        # MAE / MFE
        ei = ts_idx.get(t["exit_ts"], len(candles) - 1)
        mae = mfe = 0.0
        entry = t["entry"]
        for c in candles[i: ei + 1]:
            if t["side"] == "long":
                mae = max(mae, (entry - c["l"]) / entry * 100)
                mfe = max(mfe, (c["h"] - entry) / entry * 100)
            else:
                mae = max(mae, (c["h"] - entry) / entry * 100)
                mfe = max(mfe, (entry - c["l"]) / entry * 100)

        reasons = [e["reason"] for e in t.get("exits", [])]
        tp_first = any(r.startswith("止盈") for r in reasons)
        stop_last = reasons[-1] in ("止损", "保本止损", "极端止损", "爆仓")

        rows.append({
            "entry": datetime.fromtimestamp(ets / 1000, tz=timezone.utc).strftime("%m-%d %H:%M"),
            "side": side, "pnl_pct": t["pnl_pct"], "score": sc.get("total_score"),
            "grade": sig.get("grade"), "er": round(er, 3) if er is not None else None,
            "er_ok": er_ok, "rng_pct": round(rng_pct, 2), "touches": touches,
            "rng_block": rng_block, "mae": round(mae, 2), "mfe": round(mfe, 2),
            "tp_first": tp_first, "stop_last": stop_last, "reasons": reasons,
            "win": t["pnl_pct"] >= 0,
        })

    losers = [r for r in rows if not r["win"]]
    winners = [r for r in rows if r["win"]]

    def avg(xs, k):
        xs = [r[k] for r in xs if r[k] is not None]
        return sum(xs) / len(xs) if xs else float("nan")

    print(f"\n=== BTC 1h ST(10,3.0) 成交分析  总 {len(rows)} 笔 "
          f"(亏 {len(losers)} / 盈 {len(winners)}) ===\n")
    print("—— 亏损单明细 ——")
    print(f"{'时间':<14}{'方向':<5}{'盈亏%':>7}{'分数':>7}{'级':>3}"
          f"{'ER':>7}{'可交':>4}{'区间%':>7}{'触':>4}{'该拦':>5}"
          f"{'MAE%':>7}{'MFE%':>7}  出场")
    for r in sorted(losers, key=lambda x: x["pnl_pct"]):
        print(f"{r['entry']:<14}{r['side']:<5}{r['pnl_pct']:>7.2f}"
              f"{str(r['score']):>7}{r['grade']:>3}{str(r['er']):>7}{str(r['er_ok']):>4}"
              f"{r['rng_pct']:>7.2f}{r['touches']:>4}{str(r['rng_block']):>5}"
              f"{r['mae']:>7.2f}{r['mfe']:>7.2f}  {'/'.join(r['reasons'])}")

    print("\n—— 分数/ER 对比（判断拦截失效性质）——")
    print(f"  平均分数  亏损 {avg(losers,'score'):.1f}   盈利 {avg(winners,'score'):.1f}   "
          f"(闸门 50/55)")
    print(f"  平均 ER    亏损 {avg(losers,'er'):.3f}   盈利 {avg(winners,'er'):.3f}   "
          f"(可交阈值 {er_min})")
    print(f"  ER 不可交占比  亏损 {sum(1 for r in losers if not r['er_ok'])}/{len(losers)}   "
          f"盈利 {sum(1 for r in winners if not r['er_ok'])}/{len(winners)}")
    print(f"  处于『应被区间过滤』占比  亏损 {sum(1 for r in losers if r['rng_block'])}/{len(losers)}   "
          f"盈利 {sum(1 for r in winners if r['rng_block'])}/{len(winners)}")
    print(f"  先吃 TP 再被止损(洗盘)  亏损 {sum(1 for r in losers if r['tp_first'] and r['stop_last'])}/{len(losers)}")
    print(f"  MAE>3% (触发硬止损后才亏)  亏损 {sum(1 for r in losers if r['mae']>=3.0)}/{len(losers)}")
    print(f"  MFE>0.8% (曾到 TP1 才回吐)  亏损 {sum(1 for r in losers if r['mfe']>=0.8)}/{len(losers)}")

    # 评级分布
    from collections import Counter
    gl = Counter(r["grade"] for r in losers)
    gw = Counter(r["grade"] for r in winners)
    print(f"\n  评级分布  亏损 {dict(gl)}   盈利 {dict(gw)}")


if __name__ == "__main__":
    main()
