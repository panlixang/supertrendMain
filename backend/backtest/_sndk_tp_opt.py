"""SNDK-USDT-SWAP 止盈止损档位寻优。

信号侧完全对齐线上：1h 周期、23×2.5、ER 0.12/0.12/0.3、打分制 70/70/55
动态阈值、A/B/C min_score=0、10U×10x。
只扫 exit_rules 的三档止盈幅度 tp1/tp2/tp3，其余止盈止损字段保持线上原样。
"""
from __future__ import annotations

import copy
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest
from regime import TradeConfig
from _live_cfg_backtest import BARS, BIAS_TFS, LIVE_URL, _get, fetch_candles, ts_fmt

SYMBOL = "SNDK-USDT-SWAP"
GATE_TF = "1h"
BARS_N = BARS["1h"]
MIN_TRADES = 5

# 三档止盈幅度网格（按顺序约束筛选）
TP1S = [0.8, 1.0, 1.2, 1.5, 1.8, 2.0]
TP2S = [1.8, 2.0, 2.5, 3.0, 3.5]
TP3S = [3.0, 3.5, 4.0, 4.5, 5.0]


def trade_cfg_full(sym: dict) -> TradeConfig:
    """线上全部闸门字段（含打分制），与 /api/trade/symbols 完全一致。"""
    return TradeConfig(
        enabled=True,
        leverage=sym["leverage"],
        amount_usdt=sym["margin_usdt"],
        er_hide_below=sym["er_hide_below"],
        er_weak_min=sym["er_weak_min"],
        er_min=sym["er_min"],
        er_trend=sym["er_trend"],
        quick_enabled=sym["quick_enabled"],
        allow_grades=list(sym["allow_grades"]),
        min_score=sym["min_score"],
        allow_tfs=list(sym["allow_tfs"]),
        cooldown_sec=sym["cooldown_sec"],
        atr_filter_enabled=sym["atr_filter_enabled"],
        atr_vol_min=sym["atr_vol_min"],
        range_filter_enabled=sym["range_filter_enabled"],
        range_size_max=sym["range_size_max"],
        range_touches_min=sym["range_touches_min"],
        mtf_filter_enabled=sym["mtf_filter_enabled"],
        mtf_consistency_min=sym["mtf_consistency_min"],
        mtf_flip_max=sym["mtf_flip_max"],
        adx_filter_enabled=sym["adx_filter_enabled"],
        adx_min=sym["adx_min"],
        adx_period=sym["adx_period"],
        use_scoring=sym.get("use_scoring", True),
        scoring_full_threshold=sym["scoring_full_threshold"],
        scoring_half_threshold=sym["scoring_half_threshold"],
        scoring_alert_threshold=sym["scoring_alert_threshold"],
        use_dynamic_threshold=sym.get("use_dynamic_threshold", True),
    )


def exit_rules_of(sym: dict, tp1: float, tp2: float, tp3: float):
    """以线上 exit_rules 为基准，覆盖三档止盈幅度。"""
    from position_enhanced import EnhancedExitRules
    r = sym["exit_rules"]
    return EnhancedExitRules(
        enabled=r.get("enabled", True),
        tp1_pct=tp1, tp1_ratio=r["tp1_ratio"],
        tp2_pct=tp2, tp2_ratio=r.get("tp2_ratio", 40.0),
        tp3_pct=tp3, tp3_ratio=r.get("tp3_ratio", 100.0),
        tp3_mode=r.get("tp3_mode", "reverse_signal"),
        move_sl_to_entry=r.get("move_sl_to_entry", True),
        sl_mode=r.get("sl_mode", "st"), sl_pct=r.get("sl_pct", 2.0),
        trail_with_st=r.get("trail_with_st", True),
        sl_buffer_atr=r.get("sl_buffer_atr", 0.5),
        sl_min_pct=r.get("sl_min_pct", 1.2),
        protect_profit_at=r.get("protect_profit_at", 1.5),
        protect_trail_pct=r.get("protect_trail_pct", 0.8),
        max_loss_enabled=r.get("max_loss_enabled", False),
        max_loss_pct=r.get("max_loss_pct", 10.0),
    )


def quick_rules_of(sym: dict):
    """线上 quick 档规则（SNDK 弱档区间为空，实际几乎不走，但保持完全对齐）。"""
    from position_enhanced import EnhancedExitRules
    r = sym.get("exit_rules_quick")
    if not r:
        return None
    return EnhancedExitRules(
        enabled=r.get("enabled", True),
        tp1_pct=r["tp1_pct"], tp1_ratio=r["tp1_ratio"],
        tp2_pct=r.get("tp2_pct", 999), tp2_ratio=r.get("tp2_ratio", 0),
        tp3_pct=r.get("tp3_pct", 999), tp3_ratio=r.get("tp3_ratio", 0),
        tp3_mode=r.get("tp3_mode", "pct"),
        move_sl_to_entry=r.get("move_sl_to_entry", False),
        sl_mode=r.get("sl_mode", "st"), sl_pct=r.get("sl_pct", 1.0),
        trail_with_st=r.get("trail_with_st", False),
        sl_buffer_atr=r.get("sl_buffer_atr", 0.3),
        sl_min_pct=r.get("sl_min_pct", 1.0),
        protect_profit_at=r.get("protect_profit_at", 999),
        protect_trail_pct=r.get("protect_trail_pct", 0),
        max_loss_enabled=r.get("max_loss_enabled", False),
        max_loss_pct=r.get("max_loss_pct", 10.0),
    )


def run_one(candles, cbtf, sym, cfg, q_rules, tp1, tp2, tp3):
    r = run_backtest(
        candles, sym["params"],
        init_cash=100.0, fee_rate=0.0005, allow_short=True,
        exit_rules=exit_rules_of(sym, tp1, tp2, tp3),
        exit_rules_quick=q_rules,
        sizing="fixed", margin_usdt=sym["margin_usdt"],
        leverage=sym["leverage"],
        live_gate=cfg, gate_tf=GATE_TF, candles_by_tf=cbtf,
    )
    return r


def summarize(sym, r) -> dict:
    pnl = round(r["final"] - 100.0, 2)
    return {
        "pnl_u": pnl,
        "roi_pct": round(pnl / sym["margin_usdt"] * 100, 1),
        "max_dd_pct": r["max_dd_pct"],
        "trades": r["trades"],
        "quick_trades": r.get("quick_trades", 0),
        "win_rate": r["win_rate"],
        "profit_factor": r["profit_factor"],
        "avg_win": round(r["avg_win"], 2),
        "avg_loss": round(r["avg_loss"], 2),
        "blocked": r["er_blocked"],
        "tp1": r["tp1_count"], "tp2": r.get("tp2_count", 0), "tp3": r.get("tp3_count", 0),
        "stops": r["stop_count"], "reverses": r["reverse_count"],
    }


def score_row(sum_, min_t=MIN_TRADES) -> float:
    if sum_["trades"] < min_t or sum_["max_dd_pct"] <= 0:
        return -999.0
    # 收益回撤比为主，附加小惩罚项鼓励交易数
    return sum_["pnl_u"] / sum_["max_dd_pct"]


def main():
    sym = next(s for s in _get(LIVE_URL)["symbols"] if s["symbol"] == SYMBOL)
    cfg = trade_cfg_full(sym)
    q_rules = quick_rules_of(sym)

    print(f"=== {SYMBOL} {GATE_TF} fetch ===", flush=True)
    candles = fetch_candles(SYMBOL, GATE_TF, BARS_N)
    cbtf = {GATE_TF: candles}
    for tf in BIAS_TFS:
        if tf == GATE_TF:
            continue
        extra = fetch_candles(SYMBOL, tf, min(len(candles), BARS.get(tf, 4500)))
        if extra:
            cbtf[tf] = extra
    start, end = candles[0]["ts"], candles[-1]["ts"]
    print(f"  bars={len(candles)} {ts_fmt(start)} ~ {ts_fmt(end)}", flush=True)

    # 当前线上配置基线
    cur = sym["exit_rules"]
    r0 = run_one(candles, cbtf, sym, cfg, q_rules, cur["tp1_pct"], cur["tp2_pct"], cur["tp3_pct"])
    s0 = summarize(sym, r0)
    print(f"  [当前线上 {cur['tp1_pct']}/{cur['tp2_pct']}/{cur['tp3_pct']}] "
          f"pnl={s0['pnl_u']}U dd={s0['max_dd_pct']}% trades={s0['trades']} "
          f"wr={s0['win_rate']}% PF={s0['profit_factor']} blocked={s0['blocked']}", flush=True)

    # ── 全段网格 ──
    combos = [(a, b, c) for a in TP1S for b in TP2S for c in TP3S if a < b < c]
    print(f"  grid {len(combos)} combos …", flush=True)
    rows = []
    t0 = time.time()
    for i, (a, b, c) in enumerate(combos, 1):
        r = run_one(candles, cbtf, sym, cfg, q_rules, a, b, c)
        if "error" in r:
            continue
        s = summarize(sym, r)
        s.update({"tp": [a, b, c], "score": round(score_row(s), 3)})
        rows.append(s)
        if i % 30 == 0:
            print(f"  {i}/{len(combos)} …", flush=True)
    rows.sort(key=lambda x: x["score"], reverse=True)
    print(f"  grid done {len(rows)} rows in {time.time()-t0:.0f}s", flush=True)

    # ── top 候选前后半段验证 ──
    qual = [r for r in rows if r["trades"] >= MIN_TRADES]
    top = qual[:8]
    print("\n=== top8 前后半段稳定性验证 ===", flush=True)
    mid = len(candles) // 2
    for t in top:
        c1, c2 = candles[:mid], candles[mid:]
        b1 = {GATE_TF: c1}
        b2 = {GATE_TF: c2}
        for tf, extra in cbtf.items():
            if tf == GATE_TF:
                continue
            e1 = [x for x in extra if x["ts"] <= mid]
            e2 = [x for x in extra if x["ts"] > mid]
            if e1:
                b1[tf] = e1
            if e2:
                b2[tf] = e2
        a, b, c = t["tp"]
        r1 = run_one(c1, b1, sym, cfg, q_rules, a, b, c)
        r2 = run_one(c2, b2, sym, cfg, q_rules, a, b, c)
        t["half1"] = summarize(sym, r1)
        t["half2"] = summarize(sym, r2)
        t["half_score"] = round(
            (t["half1"]["pnl_u"] + t["half2"]["pnl_u"]) / max(1e-9, t["half1"]["max_dd_pct"] + t["half2"]["max_dd_pct"]), 3
        )
        t["stable"] = t["half1"]["pnl_u"] > 0 and t["half2"]["pnl_u"] > 0
        print(f"  {a}/{b}/{c}: full={t['pnl_u']}U dd={t['max_dd_pct']}% trades={t['trades']} "
              f"| H1={t['half1']['pnl_u']}U({t['half1']['trades']}) "
              f"H2={t['half2']['pnl_u']}U({t['half2']['trades']}) "
              f"stable={t['stable']}", flush=True)

    # 推荐：优先前后半段都赚的组合里选 score 最高；都没有则选全段 score 最高
    stable = [t for t in top if t["stable"]]
    rec = (stable or qual)[0]
    rec = rec if stable else (qual[0] if qual else rows[0])

    out = {
        "symbol": SYMBOL, "tf": GATE_TF,
        "start": ts_fmt(start), "end": ts_fmt(end), "bars": len(candles),
        "filters": "线上 SNDK：ER 0.12 + 打分制 70/70/55 动态 + A/B/C min_score=0 + 10U×10x 1h",
        "current": {**s0, "tp": [cur["tp1_pct"], cur["tp2_pct"], cur["tp3_pct"]]},
        "recommend": rec,
        "top8": top,
        "all": rows,
    }
    path = os.path.join(os.path.dirname(__file__), "_sndk_tp_opt.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {path}")
    print("\n=== RECOMMEND ===")
    print(json.dumps(rec, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
