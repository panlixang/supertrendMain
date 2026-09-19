# -*- coding: utf-8 -*-
"""INTC-USDT-SWAP 全量下单配置联合寻优（坐标下降，分阶段）。

阶段：
  S0 周期(15m/1h) × SuperTrend(periods×multiplier)   —— 指标参数
  S1 ER 四档阈值（er_min × er_trend，hide/weak 派生）  —— 趋势过滤
  S2 信号打分三阈值（full/half/alert）                 —— 仓位闸门
  S3 止盈止损（tp1/tp2/tp3 × sl_pct 地板）             —— 出场
出口的 sl 用 ST 跟随式（sl_mode='st'），sl_pct 为地板。
评分=pnl/最大回撤，要求最小交易笔数 + 前后半段稳定性校验。
注意：这是坐标下降（非全笛卡尔积），近似但可解释；最终配置做稳定性校验。

数据：OKX 实盘 INTC-USDT-SWAP
  1h : 4500 根 (~2026-03-14~09-18, ~6 个月)
  15m: 9000 根 (~2026-06-16~09-18, ~3 个月)
注：15m 窗口比 1h 短，S0 跨周期比较时回测口径按各自窗口；score=pnl/dd 不按时间归一。
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest
from regime import TradeConfig
from position_enhanced import EnhancedExitRules
from _live_cfg_backtest import BARS, fetch_candles, ts_fmt

SYMBOL = "INTC-USDT-SWAP"
MIN_TRADES = 10
LEV = 10
MARGIN = 5.0

# 默认信号模板（S0/S1/S2 里被逐段覆盖；S3 固定信号侧）
DEF_ER = dict(er_hide_below=0.1, er_weak_min=0.08, er_min=0.12, er_trend=0.3)
DEF_SCORE = dict(scoring_full_threshold=50.0, scoring_half_threshold=50.0,
                scoring_alert_threshold=55.0)

# ── 标准档出场模板（S0~S2 用，S3 扫）──
STD_TPL = dict(
    enabled=True, tp1_ratio=30.0, tp2_ratio=40.0, tp3_ratio=100.0,
    tp3_mode="reverse_signal", move_sl_to_entry=True, sl_mode="st", sl_pct=3.0,
    trail_with_st=True, sl_buffer_atr=0.5, sl_min_pct=1.2,
    protect_profit_at=1.5, protect_trail_pct=0.8,
    max_loss_enabled=True, max_loss_pct=3.0,
)
# 弱档出场（profile=quick，score_signal 按 ER 带分类）
QUICK_RULES = dict(
    enabled=True, tp1_pct=1.5, tp1_ratio=100.0, tp2_pct=100.0, tp2_ratio=0.0,
    tp3_pct=100.0, tp3_ratio=0.0, tp3_mode="pct", move_sl_to_entry=True,
    sl_mode="st", sl_pct=1.5, trail_with_st=True, sl_buffer_atr=0.3,
    sl_min_pct=1.5, protect_profit_at=50.0, protect_trail_pct=0.0,
    max_loss_enabled=True, max_loss_pct=1.5,
)


def gate_base(gate_tf, er, score):
    return dict(
        enabled=True, leverage=LEV, amount_usdt=MARGIN,
        er_hide_below=er["er_hide_below"], er_weak_min=er["er_weak_min"],
        er_min=er["er_min"], er_trend=er["er_trend"],
        quick_enabled=False,
        allow_grades=["A", "B", "C"], min_score=1, allow_tfs=[gate_tf],
        cooldown_sec=300,
        atr_filter_enabled=False, atr_vol_min=0.7,
        range_filter_enabled=True, range_size_max=0.15, range_touches_min=2,
        mtf_filter_enabled=False, mtf_consistency_min=0.6, mtf_flip_max=5,
        adx_filter_enabled=False, adx_min=20.0, adx_period=14,
        use_scoring=True,
        scoring_full_threshold=score["scoring_full_threshold"],
        scoring_half_threshold=score["scoring_half_threshold"],
        scoring_alert_threshold=score["scoring_alert_threshold"],
        use_dynamic_threshold=True,
    )


def std_rules(tp1, tp2, tp3, sl_pct=3.0):
    r = dict(STD_TPL)
    r.update(tp1_pct=tp1, tp2_pct=tp2, tp3_pct=tp3, sl_pct=sl_pct,
             sl_min_pct=min(1.2, sl_pct * 0.4))
    return EnhancedExitRules(**r)


def quick_rules():
    return EnhancedExitRules(**QUICK_RULES)


def run_cfg(candles_map, gate_tf, params, er, score, exit_r, q_r):
    gate_c = candles_map[gate_tf]
    cbtf = {gate_tf: gate_c}
    for tf in ("15m", "1h", "4h", "1d"):
        if tf != gate_tf and tf in candles_map:
            cbtf[tf] = candles_map[tf]
    cfg = TradeConfig(**gate_base(gate_tf, er, score))
    r = run_backtest(
        gate_c, params, init_cash=100.0, fee_rate=0.001, allow_short=True,
        exit_rules=exit_r, exit_rules_quick=q_r,
        sizing="fixed", margin_usdt=MARGIN, leverage=LEV,
        live_gate=cfg, gate_tf=gate_tf, candles_by_tf=cbtf,
    )
    return r


def summarize(r):
    if "error" in r:
        return None
    pnl = round(r["final"] - 100.0, 2)
    return dict(
        pnl_u=pnl, roi_pct=round(pnl / MARGIN * 100, 1),
        max_dd_pct=r["max_dd_pct"], trades=r["trades"],
        quick_trades=r.get("quick_trades", 0), win_rate=r["win_rate"],
        profit_factor=r["profit_factor"], avg_win=round(r["avg_win"], 2),
        avg_loss=round(r["avg_loss"], 2), blocked=r["er_blocked"],
        tp1=r["tp1_count"], tp2=r.get("tp2_count", 0), tp3=r.get("tp3_count", 0),
        stops=r["stop_count"], reverses=r["reverse_count"],
    )


def score_row(s):
    if not s or s["trades"] < MIN_TRADES or s["max_dd_pct"] <= 0:
        return -999.0
    return s["pnl_u"] / s["max_dd_pct"]


def st_params(periods, mult):
    return dict(periods=periods, multiplier=mult, src="hl2", change_atr=True,
                fast_len=20, slow_len=50, ma_type="EMA")


def main():
    print(f"=== {SYMBOL} fetch ===", flush=True)
    cm = {}
    cm["15m"] = fetch_candles(SYMBOL, "15m", BARS["15m"])
    cm["1h"] = fetch_candles(SYMBOL, "1h", BARS["1h"])
    cm["4h"] = fetch_candles(SYMBOL, "4h", min(4500, BARS.get("4h", 4500)))
    cm["1d"] = fetch_candles(SYMBOL, "1d", min(4500, BARS.get("1d", 4500)))
    for tf in ("15m", "1h", "4h", "1d"):
        if cm[tf]:
            print(f"  {tf}: {len(cm[tf])} bars {ts_fmt(cm[tf][0]['ts'])}~{ts_fmt(cm[tf][-1]['ts'])}", flush=True)

    # ===== S0: 周期 × ST =====
    print("\n=== S0 周期×ST ===", flush=True)
    q_r = quick_rules()
    s0_rows = []
    for gate_tf in ("1h", "15m"):
        for periods in (7, 10, 11, 12, 14):
            for mult in (2.5, 3.0, 4.0):
                r = run_cfg(cm, gate_tf, st_params(periods, mult), DEF_ER, DEF_SCORE,
                            std_rules(1.0, 1.5, 3.0), q_r)
                s = summarize(r)
                if s:
                    s.update({"gate_tf": gate_tf, "periods": periods, "mult": mult,
                              "score": round(score_row(s), 3)})
                    s0_rows.append(s)
                    print(f"  {gate_tf} {periods}x{mult}: pnl={s['pnl_u']} dd={s['max_dd_pct']}% "
                          f"tr={s['trades']} wr={s['win_rate']}% PF={s['profit_factor']} blk={s['blocked']}", flush=True)
    s0_rows.sort(key=lambda x: x["score"], reverse=True)
    best0 = s0_rows[0]
    GATE_TF = best0["gate_tf"]; PST = best0["periods"]; PMULT = best0["mult"]
    print(f"  >> best S0: {GATE_TF} {PST}x{PMULT} score={best0['score']} pnl={best0['pnl_u']}", flush=True)

    # ===== S1: ER =====
    print(f"\n=== S1 ER (固定 {GATE_TF} {PST}x{PMULT}) ===", flush=True)
    s1_rows = []
    for er_min in (0.10, 0.13, 0.16, 0.20):
        for er_trend in (0.28, 0.34, 0.40):
            er = dict(er_min=er_min, er_trend=er_trend,
                      er_weak_min=max(0.04, er_min - 0.04),
                      er_hide_below=max(0.02, er_min - 0.06))
            r = run_cfg(cm, GATE_TF, st_params(PST, PMULT), er, DEF_SCORE,
                        std_rules(1.0, 1.5, 3.0), q_r)
            s = summarize(r)
            if s:
                s.update({"er": er, "score": round(score_row(s), 3)})
                s1_rows.append(s)
                print(f"  min={er_min} trend={er_trend}: pnl={s['pnl_u']} dd={s['max_dd_pct']}% "
                      f"tr={s['trades']} wr={s['win_rate']}% PF={s['profit_factor']} blk={s['blocked']}", flush=True)
    s1_rows.sort(key=lambda x: x["score"], reverse=True)
    best1 = s1_rows[0]; BEST_ER = best1["er"]
    print(f"  >> best S1: {BEST_ER} score={best1['score']} pnl={best1['pnl_u']}", flush=True)

    # ===== S2: 打分 =====
    print(f"\n=== S2 打分 (固定 {GATE_TF} {PST}x{PMULT} ER={BEST_ER['er_min']}/{BEST_ER['er_trend']}) ===", flush=True)
    s2_rows = []
    for sf in (50.0, 60.0, 70.0):
        for sh in (40.0, 50.0, 60.0):
            for sa in (30.0, 45.0, 55.0):
                sc = dict(scoring_full_threshold=sf, scoring_half_threshold=sh, scoring_alert_threshold=sa)
                r = run_cfg(cm, GATE_TF, st_params(PST, PMULT), BEST_ER, sc,
                            std_rules(1.0, 1.5, 3.0), q_r)
                s = summarize(r)
                if s:
                    s.update({"score_th": sc, "score": round(score_row(s), 3)})
                    s2_rows.append(s)
                    print(f"  f={sf} h={sh} a={sa}: pnl={s['pnl_u']} dd={s['max_dd_pct']}% "
                          f"tr={s['trades']} wr={s['win_rate']}% PF={s['profit_factor']}", flush=True)
    s2_rows.sort(key=lambda x: x["score"], reverse=True)
    best2 = s2_rows[0]; BEST_SCORE = best2["score_th"]
    print(f"  >> best S2: {BEST_SCORE} score={best2['score']} pnl={best2['pnl_u']}", flush=True)

    # ===== S3: 止盈止损 =====
    print(f"\n=== S3 止盈止损 (固定 {GATE_TF} {PST}x{PMULT} ER={BEST_ER['er_min']}/{BEST_ER['er_trend']} score={BEST_SCORE}) ===", flush=True)
    TP1S = [1.0, 1.2, 1.5, 1.8, 2.0, 2.5]
    TP2S = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
    TP3S = [3.0, 3.5, 4.0, 4.5, 5.0, 6.0]
    s3_rows = []
    t0 = time.time()
    combos = [(a, b, c) for a in TP1S for b in TP2S for c in TP3S if a < b < c]
    for i, (a, b, c) in enumerate(combos, 1):
        r = run_cfg(cm, GATE_TF, st_params(PST, PMULT), BEST_ER, BEST_SCORE,
                    std_rules(a, b, c, sl_pct=3.0), q_r)
        s = summarize(r)
        if s:
            s.update({"tp": [a, b, c], "sl_pct": 3.0, "score": round(score_row(s), 3)})
            s3_rows.append(s)
        if i % 40 == 0:
            print(f"  {i}/{len(combos)} …", flush=True)
    s3_rows.sort(key=lambda x: x["score"], reverse=True)
    print(f"  tp grid done {len(s3_rows)} in {time.time()-t0:.0f}s", flush=True)
    # sl 地板微调：取 tp 前 6 扫 sl_pct
    top_tp = [r for r in s3_rows if r["trades"] >= MIN_TRADES][:6]
    print("  sl 微调（top6 tp × sl_pct∈{1.5,2.0,3.0}）:", flush=True)
    for base in top_tp:
        a, b, c = base["tp"]
        for sl in (1.5, 2.0, 3.0):
            r = run_cfg(cm, GATE_TF, st_params(PST, PMULT), BEST_ER, BEST_SCORE,
                        std_rules(a, b, c, sl_pct=sl), q_r)
            s = summarize(r)
            if s:
                s.update({"tp": [a, b, c], "sl_pct": sl, "score": round(score_row(s), 3)})
                s3_rows.append(s)
                print(f"    {a}/{b}/{c} sl={sl}: pnl={s['pnl_u']} dd={s['max_dd_pct']}% tr={s['trades']} PF={s['profit_factor']}", flush=True)
    s3_rows.sort(key=lambda x: x["score"], reverse=True)
    best3 = s3_rows[0]
    print(f"  >> best S3: tp={best3['tp']} sl={best3['sl_pct']} score={best3['score']} pnl={best3['pnl_u']}", flush=True)

    # ===== 最终稳定性校验（前后半段）=====
    print("\n=== 最终配置 前后半段稳定性 ===", flush=True)
    gate_c = cm[GATE_TF]
    mid = len(gate_c) // 2
    c1 = gate_c[:mid]; c2 = gate_c[mid:]
    cbtf1 = {GATE_TF: c1}; cbtf2 = {GATE_TF: c2}
    for tf in ("15m", "1h", "4h", "1d"):
        if tf != GATE_TF and tf in cm:
            e1 = [x for x in cm[tf] if x["ts"] <= c1[-1]["ts"]]
            e2 = [x for x in cm[tf] if x["ts"] > c1[-1]["ts"]]
            if e1: cbtf1[tf] = e1
            if e2: cbtf2[tf] = e2
    a, b, c = best3["tp"]
    cfg_final = TradeConfig(**gate_base(GATE_TF, BEST_ER, BEST_SCORE))
    def _run_on(cbtf):
        gc = cbtf[GATE_TF]
        return run_backtest(gc, st_params(PST, PMULT), init_cash=100.0, fee_rate=0.001,
                            allow_short=True, exit_rules=std_rules(a, b, c, best3["sl_pct"]),
                            exit_rules_quick=q_r, sizing="fixed", margin_usdt=MARGIN, leverage=LEV,
                            live_gate=cfg_final, gate_tf=GATE_TF, candles_by_tf=cbtf)
    rH1 = summarize(_run_on(cbtf1)); rH2 = summarize(_run_on(cbtf2))
    stable = (rH1 and rH1["pnl_u"] > 0) and (rH2 and rH2["pnl_u"] > 0)
    print(f"  H1: pnl={rH1['pnl_u']}U tr={rH1['trades']} PF={rH1['profit_factor']}", flush=True)
    print(f"  H2: pnl={rH2['pnl_u']}U tr={rH2['trades']} PF={rH2['profit_factor']}", flush=True)
    print(f"  stable={stable}", flush=True)

    rec = {
        "symbol": SYMBOL, "gate_tf": GATE_TF,
        "params": st_params(PST, PMULT),
        "er": BEST_ER, "scoring": BEST_SCORE,
        "exit_rules": dict(STD_TPL, tp1_pct=a, tp2_pct=b, tp3_pct=c, sl_pct=best3["sl_pct"],
                           sl_min_pct=min(1.2, best3["sl_pct"] * 0.4)),
        "full_summary": best3, "half1": rH1, "half2": rH2, "stable": stable,
        "s0_best": best0, "s1_best": best1, "s2_best": best2,
    }
    path = os.path.join(os.path.dirname(__file__), "_intc_full_opt.json")
    json.dump(rec, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=str)
    print(f"\nWrote {path}")
    print("\n=== 推荐完整配置 ===")
    print(json.dumps(rec, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
