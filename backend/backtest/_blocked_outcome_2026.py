"""BTC-USDT-SWAP · 近半年 1h · 被拦截信号的「若未拦截」理论归宿。

对每一个被综合评分拦截（score_min=60，两阶段）的信号，查全量回测里的理论成交，
判定：触发 TP1（止盈）/ 触发止损（含保本止损）/ 持有到区间结束。
用于回答「拦截到底拦对了吗」。

用法：python _blocked_outcome_2026.py
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

SYMBOL = "BTC-USDT-SWAP"
ST_PERIODS, ST_MULT = 10, 3.0
INIT_CASH = 1000.0
MARGIN, LEV = 100.0, 10
TP1_PCT, TP1_RATIO, SL_PCT = 1.5, 60.0, 2.0
SCORE_MIN = 60.0
START = int(datetime(2026, 3, 23, tzinfo=timezone.utc).timestamp() * 1000)
BARS_1H, BARS_4H = 5300, 1500


def main():
    print(f"=== {SYMBOL} · 被拦截信号的「若未拦截」理论归宿（score_min={SCORE_MIN}）===", flush=True)
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

    scores, confirmed = {}, {}
    for f in flips:
        i = f["i"]; ts = candles[i]["ts"]; sd = 1 if f["type"] == "buy" else -1
        cs = candles[:i + 1]; j = bisect.bisect_right(c4_ts, ts)
        c4s = c4[:j + 1] if j >= 0 else []
        scores[ts] = pattern_score([c["c"] for c in cs], [c["h"] for c in cs],
            [c["l"] for c in cs], [c["o"] for c in cs], [c["vol"] for c in cs],
            [c["c"] for c in c4s], [c["h"] for c in c4s], [c["l"] for c in c4s], sd, candles[i]["c"])
        confirmed[ts] = (i + 1 < len(candles)) and (
            (sd > 0 and candles[i + 1]["c"] > candles[i]["h"]) or
            (sd < 0 and candles[i + 1]["c"] < candles[i]["l"]))

    r = run_backtest(candles, {"periods": ST_PERIODS, "multiplier": ST_MULT, "src": "hl2", "change_atr": True},
                     init_cash=INIT_CASH, fee_rate=0.0005, allow_short=True,
                     exit_rules=ExitRules(tp1_pct=TP1_PCT, tp1_ratio=TP1_RATIO, sl_mode="st",
                                          sl_pct=SL_PCT, move_sl_to_entry=True, trail_with_st=True),
                     sizing="fixed", margin_usdt=MARGIN, leverage=LEV, full_trades=True)
    trade_by_ts = {t["entry_ts"]: t for t in r.get("trade_list") or []}

    rows = []
    for k, f in enumerate(flips, 1):
        ts = candles[f["i"]]["ts"]; typ = f["type"]; sd = 1 if typ == "buy" else -1
        sc = scores[ts]
        immediate = sc >= SCORE_MIN
        blocked = not (immediate or (sc < 50 and confirmed[ts]))
        if not blocked:
            continue
        tr = trade_by_ts.get(ts)
        tp1_hit = bool(tr and any(str(e.get("reason", "")).startswith("止盈") for e in tr.get("exits", [])))
        if not tr:
            outcome = "无记录"
        elif tp1_hit:
            outcome = "TP1触发"
        elif tr["reason"] == "区间结束":
            outcome = "持有到结束"
        else:
            outcome = "止损"
        rows.append({
            "idx": k, "date": ts_fmt(ts), "type": typ, "score": round(sc, 1),
            "entry": round(candles[f["i"]]["c"], 1),
            "tp1": round(candles[f["i"]]["c"] * (1 + TP1_PCT/100 if sd > 0 else 1 - TP1_PCT/100), 1),
            "sl": round(candles[f["i"]]["c"] * (1 - SL_PCT/100 if sd > 0 else 1 + SL_PCT/100), 1),
            "exit": round(tr["exit"], 1) if tr else None,
            "outcome": outcome,
            "reason": tr["reason"] if tr else "",
            "pnl_pct": tr["pnl_pct"] if tr else None,
            "pnl_u": tr["pnl"] if tr else None,
            "bars": tr["bars"] if tr else None,
        })

    n_tp = sum(1 for x in rows if x["outcome"] == "TP1触发")
    n_sl = sum(1 for x in rows if x["outcome"] == "止损")
    n_end = sum(1 for x in rows if x["outcome"] == "持有到结束")
    pnl_tp = sum(x["pnl_u"] for x in rows if x["outcome"] == "TP1触发" and x["pnl_u"])
    pnl_sl = sum(x["pnl_u"] for x in rows if x["outcome"] in ("止损", "持有到结束") and x["pnl_u"])
    print(f"\n  被拦截共 {len(rows)} 笔：TP1触发 {n_tp} 笔 / 止损 {n_sl} 笔 / 持有到结束 {n_end} 笔", flush=True)
    print(f"  若全放行：TP1组合计 +{pnl_tp:.2f}U，止损组合计 {pnl_sl:.2f}U", flush=True)
    hdr = (f"{'#':>3} {'日期':>10} {'方向':>4} {'评分':>5} {'入场':>10} {'TP1':>10} {'SL':>10} "
           f"{'出场':>10} {'归宿':>10} {'原因':>10} {'盈亏%':>7} {'盈亏U':>8} {'K':>4}")
    print(hdr); print("-" * len(hdr))
    for x in rows:
        ex = f"{x['exit']:.1f}" if x["exit"] is not None else "—"
        pp = f"{x['pnl_pct']:+.2f}" if x["pnl_pct"] is not None else "—"
        pu = f"{x['pnl_u']:+.2f}" if x["pnl_u"] is not None else "—"
        br = f"{x['bars']}" if x["bars"] is not None else "—"
        print(f"{x['idx']:>3} {x['date']:>10} {x['type']:>4} {x['score']:>5.1f} "
              f"{x['entry']:>10.1f} {x['tp1']:>10.1f} {x['sl']:>10.1f} {ex:>10} "
              f"{x['outcome']:>10} {x['reason']:>10} {pp:>7} {pu:>8} {br:>4}", flush=True)

    path = os.path.join(os.path.dirname(__file__), "_blocked_outcome_2026.json")
    with open(path, "w", encoding="utf-8") as fp:
        json.dump({"symbol": SYMBOL, "score_min": SCORE_MIN, "n_blocked": len(rows),
                   "n_tp1": n_tp, "n_stop": n_sl, "n_end": n_end,
                   "pnl_tp1": round(pnl_tp, 2), "pnl_stop_end": round(pnl_sl, 2),
                   "rows": rows}, fp, ensure_ascii=False, indent=2)
    print(f"\nWrote {path}")


if __name__ == "__main__":
    main()
