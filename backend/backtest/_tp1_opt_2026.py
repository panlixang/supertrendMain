"""BTC-USDT-SWAP · 近半年(2026-03-23~) 1h · TP1 止盈参数寻优（评分闸门固定最优）。

信号 = 形态识别原始 SuperTrend(10×3.0)。
过滤 = 综合评分 score_min=60（含两阶段：<50 且下一根K确认才放行），block_4h/trend_filter 关闭。
扫描 TP1 第一档：tp1_pct × tp1_ratio，sl_mode='st'、sl_pct=2.0、保本+跟随ST 固定。
按 名义回报/最大回撤 选最优 (tp1_pct, tp1_ratio)。

用法：python _tp1_opt_2026.py
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
SL_PCT = 2.0
SCORE_MIN = float(os.environ.get("SMIN", "60"))
START = int(datetime(2026, 3, 23, tzinfo=timezone.utc).timestamp() * 1000)
BARS_1H, BARS_4H = 5300, 1500
TP1_PCTS = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
TP1_RATIOS = [50.0, 60.0, 70.0, 80.0, 90.0, 100.0]


def _summarize(r: dict) -> dict:
    pnl = round(r["final"] - r["init_cash"], 2)
    return {
        "pnl_u": pnl,
        "equity_ret_pct": round(pnl / INIT_CASH * 100, 2),
        "notional_ret_pct": round(pnl / NOTIONAL * 100, 2),
        "max_dd_pct": r["max_dd_pct"] or 0.0,
        "trades": r["trades"],
        "win_rate": r["win_rate"] or 0.0,
        "profit_factor": r["profit_factor"] or 0.0,
        "avg_win": r["avg_win"] or 0.0, "avg_loss": r["avg_loss"] or 0.0,
        "tp1": r["tp1_count"], "stops": r["stop_count"], "reverses": r["reverse_count"],
        "hold_pct": r["hold_pct"] or 0.0,
        "alpha_pct": round(pnl / INIT_CASH * 100 - (r["hold_pct"] or 0.0), 2),
    }


def _score(s: dict) -> float:
    if s["trades"] < 10 or s["max_dd_pct"] <= 0:
        return -1e9
    return s["notional_ret_pct"] / s["max_dd_pct"]


def main():
    print(f"=== {SYMBOL} · 近半年 1h · TP1 止盈寻优（评分门槛固定 {SCORE_MIN}）===", flush=True)
    print(f"  信号 ST={ST_PERIODS}×{ST_MULT} | 过滤 score_min={SCORE_MIN}(两阶段) | "
          f"SL {SL_PCT}%(st) 保本+跟随ST", flush=True)
    t0 = time.time()
    raw1 = fetch_candles(SYMBOL, "1h", BARS_1H)
    c4 = fetch_candles(SYMBOL, "4h", BARS_4H)
    print(f"  fetch 1h={len(raw1)} 4h={len(c4)} ({time.time()-t0:.0f}s)", flush=True)

    candles = [c for c in raw1 if c["ts"] >= START]
    c4_ts = [c["ts"] for c in c4]
    st1 = super_trend([c["o"] for c in raw1], [c["h"] for c in raw1],
                      [c["l"] for c in raw1], [c["c"] for c in raw1],
                      periods=ST_PERIODS, multiplier=ST_MULT, change_atr=True)
    base = len(raw1) - len(candles)
    flips = [{"i": f["i"] - base, "type": f["type"]}
             for f in (st1.get("flips") or []) if f["i"] >= base]
    print(f"  区间 {ts_fmt(candles[0]['ts'])} ~ {ts_fmt(candles[-1]['ts'])} "
          f"({len(candles)} 根 1h) | ST 翻转 {len(flips)} 个", flush=True)

    # ── 评分 + 两阶段确认（固定闸门，只算一次）──
    scores, confirmed = {}, {}
    for f in flips:
        i = f["i"]; ts = candles[i]["ts"]; sd = 1 if f["type"] == "buy" else -1
        cs = candles[:i + 1]; j = bisect.bisect_right(c4_ts, ts)
        c4s = c4[:j + 1] if j >= 0 else []
        scores[ts] = pattern_score(
            [c["c"] for c in cs], [c["h"] for c in cs], [c["l"] for c in cs],
            [c["o"] for c in cs], [c["vol"] for c in cs],
            [c["c"] for c in c4s], [c["h"] for c in c4s], [c["l"] for c in c4s],
            sd, candles[i]["c"])
        confirmed[ts] = (i + 1 < len(candles)) and (
            (sd > 0 and candles[i + 1]["c"] > candles[i]["h"]) or
            (sd < 0 and candles[i + 1]["c"] < candles[i]["l"]))
    align = {}
    for f in flips:
        ts = candles[f["i"]]["ts"]; sd = 1 if f["type"] == "buy" else -1
        ok = scores[ts] >= SCORE_MIN or (confirmed[ts] and scores[ts] < 50)
        align[ts] = sd if ok else -sd
    n_pass = sum(1 for ts in scores if scores[ts] >= SCORE_MIN or (confirmed[ts] and scores[ts] < 50))
    print(f"  评分闸门放行 {n_pass}/{len(flips)}（score_min={SCORE_MIN}）", flush=True)

    rows = []
    for tp in TP1_PCTS:
        for ratio in TP1_RATIOS:
            rules = ExitRules(tp1_pct=tp, tp1_ratio=ratio, sl_mode="st", sl_pct=SL_PCT,
                              move_sl_to_entry=True, trail_with_st=True)
            r = run_backtest(candles, {"periods": ST_PERIODS, "multiplier": ST_MULT,
                                       "src": "hl2", "change_atr": True},
                             init_cash=INIT_CASH, fee_rate=0.0005, allow_short=True,
                             exit_rules=rules, sizing="fixed", margin_usdt=MARGIN,
                             leverage=LEV, trend_align=align)
            if "error" in r:
                print(f"  tp1={tp} ratio={ratio} error: {r['error']}", flush=True)
                continue
            s = _summarize(r); s.update({"tp1_pct": tp, "tp1_ratio": ratio, "score": round(_score(s), 3)})
            rows.append(s)
            print(f"  tp1={tp:>3}% ratio={ratio:>5.0f}% | pnl={s['pnl_u']:>8}U "
                  f"名义{s['notional_ret_pct']:>6}% dd={s['max_dd_pct']:>5}% "
                  f"笔={s['trades']:>3} wr={s['win_rate']:>5}% PF={s['profit_factor']:>4} "
                  f"评分={s['score']:>5}", flush=True)

    rows.sort(key=lambda x: x["score"], reverse=True)
    best = rows[0] if rows else None
    print("\n=== 按 名义回报/最大回撤 排序 top8 ===", flush=True)
    hdr = f"{'tp1%':>5}{'ratio%':>7}{'pnlU':>9}{'名义%':>8}{'dd%':>7}{'笔':>5}{'wr%':>6}{'PF':>6}{'评分':>7}"
    print(hdr); print("-" * len(hdr))
    for t in rows[:8]:
        print(f"{t['tp1_pct']:>5}{t['tp1_ratio']:>7}{t['pnl_u']:>9}{t['notional_ret_pct']:>8}"
              f"{t['max_dd_pct']:>7}{t['trades']:>5}{t['win_rate']:>6}{t['profit_factor']:>6}"
              f"{t['score']:>7}", flush=True)

    out = {"symbol": SYMBOL, "tf": "1h", "score_min": SCORE_MIN,
           "sl_pct": SL_PCT, "base_rules": "sl_mode=st 保本+跟随ST",
           "range": {"start": ts_fmt(candles[0]["ts"]), "end": ts_fmt(candles[-1]["ts"]),
                     "bars": len(candles), "flips": len(flips), "pass": n_pass},
           "tp1_pcts": TP1_PCTS, "tp1_ratios": TP1_RATIOS,
           "top8": rows[:8], "all": rows, "recommend": best}
    path = os.path.join(os.path.dirname(__file__), f"_tp1_opt_2026_{SYMBOL.split('-')[0].lower()}.json")
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=2)
    print(f"\nWrote {path}", flush=True)
    if best:
        print("\n=== RECOMMEND tp1_pct =", best["tp1_pct"], "tp1_ratio =", best["tp1_ratio"], "===")
        print(json.dumps(best, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
