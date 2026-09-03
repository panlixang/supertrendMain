# -*- coding: utf-8 -*-
"""MU A/B: TP1=1.5 vs TP1=1.0 —— 同行情同闸门,只动 tp1_pct。
注:43/47 线上 MU 的 tp1_pct 目前都是 1.0,这里 A 模拟"仍为 1.5"的假设版做对比。
行情 = OKX MU-USDT-SWAP 1h 近 ~120 天。
黑洞诊断:曾浮盈>=10%ROE(触 TP1 线)却整笔亏损的单,看其止损根是否
与触线根同一根(=bar 内先止损后止盈的保守假设吞掉了 TP1)。用完即删。"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from dataclasses import replace
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest
from _live_cfg_backtest import fetch_candles, trade_cfg, exit_rules, BIAS_TFS, BARS

LIVE_URL = "http://43.108.10.84:5174/api/trade/symbols"


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "supertrend-bt/1.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read())


def ts_fmt(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%m-%d")


def main():
    live = _get(LIVE_URL)
    sym = next(s for s in live["symbols"] if s["symbol"] == "MU-USDT-SWAP")
    gate_tf = sym["allow_tfs"][0]
    LIMIT = 2900  # 1h ≈ 120 天
    candles = fetch_candles(sym["symbol"], gate_tf, LIMIT)
    cbtf = {gate_tf: candles}
    for tf in BIAS_TFS:
        if tf == gate_tf:
            continue
        extra = fetch_candles(sym["symbol"], tf, min(LIMIT, BARS.get(tf, 4500)))
        if extra:
            cbtf[tf] = extra

    p = sym["params"]
    lev = sym["leverage"]
    m = sym["margin_usdt"]
    cur_rules = exit_rules(sym)                  # 线上现状 = TP1 1.0
    b_rules = replace(cur_rules, tp1_pct=1.0)    # B 版 = 线上
    a_rules = replace(cur_rules, tp1_pct=1.5)    # A 版 = 假设仍是 1.5

    def run(rules, full=False):
        return run_backtest(
            candles, p,
            init_cash=100.0, fee_rate=0.0005, allow_short=True,
            exit_rules=rules, sizing="fixed", margin_usdt=m, leverage=lev,
            live_gate=trade_cfg(sym), gate_tf=gate_tf,
            candles_by_tf=cbtf, full_trades=full,
        )

    rA = run(a_rules)
    rB = run(b_rules)
    if "error" in rA or "error" in rB:
        print("ERROR:", rA.get("error") or rB.get("error"))
        sys.exit(1)

    t0, t1 = candles[0]["ts"], candles[-1]["ts"]
    ml = cur_rules.max_loss_enabled and f" max_loss={cur_rules.max_loss_pct}%px"
    print(f"MU  {gate_tf}  bars={len(candles)}  {ts_fmt(t0)}~{ts_fmt(t1)}  lev={lev}x  "
          f"margin={m}U  TP1线上现状=1.0{ml}")

    def summ(r):
        return {
            "pnl_u": round(r["final"] - r["init_cash"], 2),
            "roi_pct": round((r["final"] - r["init_cash"]) / m * 100, 1),
            "trades": r["trades"],
            "win_rate": r["win_rate"],
            "avg_win_roe": round(r["avg_win"] * lev, 2),
            "avg_loss_roe": round(r["avg_loss"] * lev, 2),
            "profit_factor": r["profit_factor"],
            "max_dd_pct": r["max_dd_pct"],
            "tp1": r["tp1_count"], "tp2": r["tp2_count"], "tp3": r["tp3_count"],
            "stops": r["stop_count"], "reverses": r["reverse_count"],
        }

    A, B = summ(rA), summ(rB)
    print("-" * 56)
    print(f"{'指标':<12}{'A TP1=1.5(假设)':>18}{'B TP1=1.0(线上)':>18}")
    rows = [
        ("净盈亏U", A["pnl_u"], B["pnl_u"]),
        ("ROE按margin%", A["roi_pct"], B["roi_pct"]),
        ("回合数", A["trades"], B["trades"]),
        ("胜率%", A["win_rate"], B["win_rate"]),
        ("avg_win %ROE", A["avg_win_roe"], B["avg_win_roe"]),
        ("avg_loss %ROE", A["avg_loss_roe"], B["avg_loss_roe"]),
        ("盈亏因子PF", A["profit_factor"], B["profit_factor"]),
        ("最大回撤%", A["max_dd_pct"], B["max_dd_pct"]),
        ("TP1/TP2/TP3", f"{A['tp1']}/{A['tp2']}/{A['tp3']}",
         f"{B['tp1']}/{B['tp2']}/{B['tp3']}"),
        ("止损数", A["stops"], B["stops"]),
        ("反向离场", A["reverses"], B["reverses"]),
    ]
    for k, va, vb in rows:
        print(f"{k:<12}{str(va):>18}{str(vb):>18}")
    print("-" * 56)

    # ── 黑洞诊断:基于 B(线上 1.0) 的 trade_list ──
    fullA = run(a_rules, full=True)
    fullB = run(b_rules, full=True)
    idx = {c["ts"]: i for i, c in enumerate(candles)}

    def enrich(r):
        out = []
        for t in r["trade_list"]:
            i0, i1 = idx.get(t.get("entry_ts")), idx.get(t.get("exit_ts"))
            if i0 is None or i1 is None or i1 < i0:
                continue
            # 开仓在信号根收盘确认,该根盘中高点/低点对持仓无意义——
            # fav 只统计开仓之后真正拿在手里的 bar
            seg = candles[i0 + 1:i1 + 1]
            if not seg:
                seg = candles[i0:i1 + 1]
            fav = (max(c["h"] for c in seg) / t["entry"] - 1) * 100 \
                if t["side"] == "long" \
                else (1 - min(c["l"] for c in seg) / t["entry"]) * 100
            out.append({**t, "fav_roe": fav * lev, "seg": seg})
        return out

    tb = enrich(fullB)
    ta = enrich(fullA)
    by_ts_a = {t["entry_ts"]: t for t in ta}
    hole = [t for t in tb if t["pnl_pct"] * lev <= 0 and t["fav_roe"] >= 10]
    hole_a = [t for t in ta if t["pnl_pct"] * lev <= 0 and t["fav_roe"] >= 10]
    print(f"\n黑洞单统计(整笔亏损 & 开仓后曾浮盈>=10%ROE): "
          f"A(TP1=1.5)={len(hole_a)} 笔 / B(TP1=1.0)={len(hole)} 笔")
    if hole_a:
        sa = sum(t["pnl_pct"] * lev for t in hole_a)
        print(f"  A 版黑洞合计 {sa:+.1f}%ROE | 明细: "
              + ", ".join(f"{ts_fmt(t['entry_ts'])}({t['pnl_pct']*lev:+.0f})" for t in hole_a))
    print(f"\n黑洞单(B=线上TP1 1.0 下): {len(hole)} 笔")
    print(f"{'开仓':<8}{'方向':<6}{'B离场方式':<12}{'最高浮盈ROE':>10}{'B最终ROE':>9}"
          f"{'A(1.5)ROE':>11}{'TP1救幅':>9}")
    for t in hole:
        a_t = by_ts_a.get(t["entry_ts"])
        a_roe = a_t["pnl_pct"] * lev if a_t else None
        a_s = f"{a_roe:+.1f}" if a_roe is not None else "无配对"
        saved = (a_roe - t["pnl_pct"] * lev) if a_roe is not None else 0.0
        print(f"{ts_fmt(t['entry_ts']):<8}{t['side']:<6}{t['reason']:<12}"
              f"{t['fav_roe']:>10.1f}{t['pnl_pct']*lev:>+9.1f}{a_s:>11}{saved:>+9.1f}")
    tot_b = sum(t["pnl_pct"] * lev for t in hole)
    tot_a = sum((by_ts_a[t["entry_ts"]]["pnl_pct"] * lev if t["entry_ts"] in by_ts_a else t["pnl_pct"] * lev)
                for t in hole)
    print(f"=> 这 {len(hole)} 笔: B(TP1=1.0) 合计 {tot_b:+.1f}%ROE | "
          f"A(TP1=1.5) 合计 {tot_a:+.1f}%ROE | 差值(1.0 相对少亏) {tot_a - tot_b:+.1f}%ROE")

    out = {
        "window": [ts_fmt(t0), ts_fmt(t1)], "bars": len(candles), "lev": lev,
        "note": "43/47 线上 tp1_pct 现状=1.0; A=假设 1.5",
        "A": A, "B": B,
        "holeB": [{
            "entry": ts_fmt(t["entry_ts"]), "side": t["side"], "reason": t["reason"],
            "fav_roe": round(t["fav_roe"], 1),
            "final_roe": round(t["pnl_pct"] * lev, 1),
        } for t in hole],
    }
    pth = os.path.join(os.path.dirname(__file__), "_mu_tp1_ab.json")
    with open(pth, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\nWrote {pth}")


if __name__ == "__main__":
    main()
