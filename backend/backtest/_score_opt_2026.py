"""BTC-USDT-SWAP · 近半年(2026-03-23~) 1h · 仅「综合评分」拦截寻优。

信号 = 形态识别原始 SuperTrend(10×3.0, hl2, changeATR=true)。
出场 = 默认 ExitRules(TP1 1.5%/70% + 保本 + 跟随ST)。
过滤 = 只保留综合评分一个闸门，block_4h / trend_filter 一律不开。
两阶段：score>=score_min 直接放行；<50 且下一根1h K 突破确认才放行（趋势形态识别.md 2026-09）。
扫描 score_min 多档，输出收益/回撤/胜率/笔数，按 名义回报/最大回撤 选最优门槛。

用法：python _score_opt_2026.py
"""
from __future__ import annotations

import bisect
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest
from indicators import super_trend
from pattern_trade import pattern_score
from position import ExitRules
from _live_cfg_backtest import fetch_candles, ts_fmt

SYMBOL = os.environ.get("SYM", "BTC-USDT-SWAP")
ST_PERIODS, ST_MULT = 10, 3.0
INIT_CASH = 1000.0
MARGIN, LEV = 100.0, 10
NOTIONAL = MARGIN * LEV
TP1_PCT, TP1_RATIO, SL_PCT = 1.5, 70.0, 2.0
START = int(datetime(2026, 3, 23, tzinfo=timezone.utc).timestamp() * 1000)
BARS_1H, BARS_4H = 5300, 1500
MIN_TRADES = 10
SCORE_MINS = [0, 20, 30, 40, 50, 60, 70, 80, 90]


def _summarize(r: dict, notional: float, margin: float) -> dict:
    pnl = round(r["final"] - r["init_cash"], 2)
    eq_ret = pnl / INIT_CASH * 100
    return {
        "pnl_u": pnl,
        "equity_ret_pct": round(eq_ret, 2),
        "notional_ret_pct": round(pnl / notional * 100, 2),
        "max_dd_pct": r["max_dd_pct"] or 0.0,
        "trades": r["trades"],
        "win_rate": r["win_rate"] or 0.0,
        "profit_factor": r["profit_factor"] or 0.0,
        "avg_win": r["avg_win"] or 0.0, "avg_loss": r["avg_loss"] or 0.0,
        "tp1": r["tp1_count"], "stops": r["stop_count"], "reverses": r["reverse_count"],
        "hold_pct": r["hold_pct"] or 0.0,
        "alpha_pct": round(eq_ret - (r["hold_pct"] or 0.0), 2),
    }


def _score(s: dict) -> float:
    if s["trades"] < MIN_TRADES or s["max_dd_pct"] <= 0:
        return -1e9
    return s["notional_ret_pct"] / s["max_dd_pct"]


def main():
    p = {"periods": ST_PERIODS, "multiplier": ST_MULT, "src": "hl2", "change_atr": True}
    rules = ExitRules(tp1_pct=TP1_PCT, tp1_ratio=TP1_RATIO, sl_mode="st", sl_pct=SL_PCT,
                      move_sl_to_entry=True, trail_with_st=True)

    t0 = time.time()
    raw1 = fetch_candles(SYMBOL, "1h", BARS_1H)
    c4 = fetch_candles(SYMBOL, "4h", BARS_4H)
    print(f"=== {SYMBOL} · 近半年 1h · 仅综合评分拦截寻优 ===", flush=True)
    print(f"  信号 ST = {ST_PERIODS}×{ST_MULT} (形态识别原始参数)", flush=True)
    print(f"  出场 = TP1 {TP1_PCT}%/{TP1_RATIO}%  SL {SL_PCT}% 保本+跟随ST", flush=True)
    print(f"  名义 = margin {MARGIN}U × {LEV}x = {NOTIONAL}U/笔 (本金 {INIT_CASH}U, 等效 1x)", flush=True)
    print(f"  fetch 1h={len(raw1)} 4h={len(c4)} ({time.time()-t0:.0f}s)", flush=True)

    candles = [c for c in raw1 if c["ts"] >= START]
    if len(candles) < 300:
        print("  2026 上半年 K 线不足", flush=True)
        return
    c4_ts = [c["ts"] for c in c4]

    st1 = super_trend([c["o"] for c in raw1], [c["h"] for c in raw1],
                      [c["l"] for c in raw1], [c["c"] for c in raw1],
                      periods=ST_PERIODS, multiplier=ST_MULT, change_atr=True)
    base = len(raw1) - len(candles)
    flips = [{"i": f["i"] - base, "type": f["type"]}
             for f in (st1.get("flips") or []) if f["i"] >= base]
    print(f"  区间 {ts_fmt(candles[0]['ts'])} ~ {ts_fmt(candles[-1]['ts'])} "
          f"({len(candles)} 根 1h) | ST 翻转 {len(flips)} 个", flush=True)

    # ── 预计算每个 flip 的综合评分 + 两阶段确认（1h 序列截至该根，4h 序列截至该 ts）──
    t0 = time.time()
    scores: dict[int, float] = {}
    confirmed: dict[int, bool] = {}
    for f in flips:
        i = f["i"]
        ts = candles[i]["ts"]
        sd = 1 if f["type"] == "buy" else -1
        cs = candles[:i + 1]
        j = bisect.bisect_right(c4_ts, ts)
        c4s = c4[:j + 1] if j >= 0 else []
        scores[ts] = pattern_score(
            [c["c"] for c in cs], [c["h"] for c in cs], [c["l"] for c in cs],
            [c["o"] for c in cs], [c["vol"] for c in cs],
            [c["c"] for c in c4s], [c["h"] for c in c4s], [c["l"] for c in c4s],
            sd, candles[i]["c"],
        )
        # 两阶段：<50 需下一根1h K 突破确认（多：次根收>信号根高；空：次根收<信号根低）
        confirmed[ts] = (i + 1 < len(candles)) and (
            (sd > 0 and candles[i + 1]["c"] > candles[i]["h"]) or
            (sd < 0 and candles[i + 1]["c"] < candles[i]["l"]))
    print(f"  综合评分预计算 {len(flips)} 个 ({time.time()-t0:.0f}s)", flush=True)
    sc_vals = list(scores.values())
    print(f"  评分分布 min={min(sc_vals):.1f} max={max(sc_vals):.1f} "
          f"mean={sum(sc_vals)/len(sc_vals):.1f}", flush=True)

    rows = []
    for sm in SCORE_MINS:
        n_pass = sum(1 for ts in scores
                     if scores[ts] >= sm or (confirmed[ts] and scores[ts] < 50))
        align = {}
        for f in flips:
            ts = candles[f["i"]]["ts"]
            sd = 1 if f["type"] == "buy" else -1
            ok = scores[ts] >= sm or (confirmed[ts] and scores[ts] < 50)
            align[ts] = sd if ok else -sd
        r = run_backtest(candles, p, init_cash=INIT_CASH, fee_rate=0.0005,
                         allow_short=True, exit_rules=rules, sizing="fixed",
                         margin_usdt=MARGIN, leverage=LEV, trend_align=align)
        if "error" in r:
            print(f"  score_min={sm} error: {r['error']}", flush=True)
            continue
        s = _summarize(r, NOTIONAL, MARGIN)
        s.update({"score_min": sm, "pass": n_pass,
                  "blocked": len(flips) - n_pass, "score": round(_score(s), 3)})
        rows.append(s)
        print(f"  score_min={sm:>2} 放行{n_pass:>3}/共{len(flips)} "
              f"| pnl={s['pnl_u']:>8}U 名义{s['notional_ret_pct']:>6}% "
              f"dd={s['max_dd_pct']:>5}% 笔={s['trades']:>3} "
              f"wr={s['win_rate']:>5}% PF={s['profit_factor']:>4} "
              f"alpha={s['alpha_pct']:>5}%", flush=True)

    rows.sort(key=lambda x: x["score"], reverse=True)
    best = rows[0] if rows else None
    print("\n=== 按 名义回报/最大回撤 排序 top5 ===", flush=True)
    hdr = f"{'sm':>3}{'pnlU':>9}{'名义%':>8}{'dd%':>7}{'笔':>5}{'wr%':>6}{'PF':>6}{'评分':>8}"
    print(hdr); print("-" * len(hdr))
    for t in rows[:5]:
        print(f"{t['score_min']:>3}{t['pnl_u']:>9}{t['notional_ret_pct']:>8}"
              f"{t['max_dd_pct']:>7}{t['trades']:>5}{t['win_rate']:>6}"
              f"{t['profit_factor']:>6}{t['score']:>8}", flush=True)

    out = {
        "symbol": SYMBOL, "tf": "1h",
        "signal": f"SuperTrend {ST_PERIODS}x{ST_MULT} (hl2, changeATR)",
        "exit": {"tp1_pct": TP1_PCT, "tp1_ratio": TP1_RATIO, "sl_pct": SL_PCT,
                 "move_sl_to_entry": True, "trail_with_st": True},
        "filter": "仅综合评分(score>=score_min)，两阶段：<50需下一根K确认；无 block_4h / trend_filter",
        "range": {"start": ts_fmt(candles[0]["ts"]), "end": ts_fmt(candles[-1]["ts"]),
                  "bars": len(candles), "flips": len(flips)},
        "score_dist": {"min": round(min(sc_vals), 1), "max": round(max(sc_vals), 1),
                       "mean": round(sum(sc_vals) / len(sc_vals), 1)},
        "top5": rows[:5],
        "all": rows,
        "recommend": best,
    }
    path = os.path.join(os.path.dirname(__file__), f"_score_opt_2026_{SYMBOL.split('-')[0].lower()}.json")
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=2)
    print(f"\nWrote {path}", flush=True)
    if best:
        print("\n=== RECOMMEND score_min =", best["score_min"], "===")
        print(json.dumps({k: v for k, v in best.items()
                          if k not in ("half1", "half2")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
