"""BTC-USDT-SWAP · 近半年(2026-03-23~) 1h · 逐笔信号明细表。

对每一个 SuperTrend(10×3.0) 翻转信号，输出：
  序号 / 时间 / 方向 / 综合评分 / 是否拦截 / 入场价 / 计划止盈(TP1) / 计划止损(SL)
  / 实际出场价 / 出场原因 / 盈亏% / 盈亏U / 持仓K数

过滤口径：仅「综合评分 >= SCORE_MIN」一个闸门（与形态识别页一致，默认 60）。
被拦截信号不实际成交：入场价取信号根收盘，计划止盈/止损照常给出，实际出场/盈亏标"拦截未成交"。

用法：python _score_signal_list_2026.py
"""
from __future__ import annotations

import bisect
import csv
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
NOTIONAL = MARGIN * LEV
TP1_PCT, TP1_RATIO, SL_PCT = 1.5, 70.0, 2.0
START = int(datetime(2026, 3, 23, tzinfo=timezone.utc).timestamp() * 1000)
BARS_1H, BARS_4H = 5300, 1500
SCORE_MIN = 60.0  # 形态识别页综合评分过滤门槛（寻优最优=60）；两阶段：<50 需下一根K确认才放行


def main():
    p = {"periods": ST_PERIODS, "multiplier": ST_MULT, "src": "hl2", "change_atr": True}
    rules = ExitRules(tp1_pct=TP1_PCT, tp1_ratio=TP1_RATIO, sl_mode="st", sl_pct=SL_PCT,
                      move_sl_to_entry=True, trail_with_st=True)

    t0 = time.time()
    raw1 = fetch_candles(SYMBOL, "1h", BARS_1H)
    c4 = fetch_candles(SYMBOL, "4h", BARS_4H)
    print(f"=== {SYMBOL} · 近半年 1h · 逐笔信号明细（拦截门槛 score_min={SCORE_MIN}）===", flush=True)
    print(f"  fetch 1h={len(raw1)} 4h={len(c4)} ({time.time()-t0:.0f}s)", flush=True)

    candles = [c for c in raw1 if c["ts"] >= START]
    if len(candles) < 300:
        print("  2026 上半年 K 线不足"); return
    c4_ts = [c["ts"] for c in c4]

    st1 = super_trend([c["o"] for c in raw1], [c["h"] for c in raw1],
                      [c["l"] for c in raw1], [c["c"] for c in raw1],
                      periods=ST_PERIODS, multiplier=ST_MULT, change_atr=True)
    base = len(raw1) - len(candles)
    flips = [{"i": f["i"] - base, "type": f["type"]}
             for f in (st1.get("flips") or []) if f["i"] >= base]
    print(f"  区间 {ts_fmt(candles[0]['ts'])} ~ {ts_fmt(candles[-1]['ts'])} "
          f"({len(candles)} 根 1h) | ST 翻转 {len(flips)} 个", flush=True)

    # ── 逐笔综合评分 ──
    t0 = time.time()
    score_by_ts = {}
    for f in flips:
        i = f["i"]; ts = candles[i]["ts"]
        sd = 1 if f["type"] == "buy" else -1
        cs = candles[:i + 1]
        j = bisect.bisect_right(c4_ts, ts)
        c4s = c4[:j + 1] if j >= 0 else []
        score_by_ts[ts] = pattern_score(
            [c["c"] for c in cs], [c["h"] for c in cs], [c["l"] for c in cs],
            [c["o"] for c in cs], [c["vol"] for c in cs],
            [c["c"] for c in c4s], [c["h"] for c in c4s], [c["l"] for c in c4s],
            sd, candles[i]["c"])
    print(f"  评分预计算 {len(flips)} 个 ({time.time()-t0:.0f}s)", flush=True)

    # ── 全量回测拿真实交易明细（trend_align=None → 不拦截，全部成交）──
    r = run_backtest(candles, p, init_cash=INIT_CASH, fee_rate=0.0005,
                     allow_short=True, exit_rules=rules, sizing="fixed",
                     margin_usdt=MARGIN, leverage=LEV, full_trades=True)
    if "error" in r:
        print("回测错误:", r["error"]); return
    trades = r.get("trade_list") or []
    trade_by_ts = {t["entry_ts"]: t for t in trades}
    print(f"  回测交易 {len(trades)} 笔", flush=True)

    # ── 组明细行 ──
    rows = []
    for k, f in enumerate(flips, 1):
        i = f["i"]; ts = candles[i]["ts"]; typ = f["type"]
        sd = 1 if typ == "buy" else -1
        score = score_by_ts[ts]
        # 两阶段确认：<SCORE_MIN 不立即交易，需下一根1h K 突破确认才放行
        confirmed = (i + 1 < len(candles)) and (
            (sd > 0 and candles[i + 1]["c"] > candles[i]["h"]) or
            (sd < 0 and candles[i + 1]["c"] < candles[i]["l"]))
        immediate = score >= SCORE_MIN
        if immediate:
            status, blocked = "放行", False
        elif score < 50 and confirmed:
            status, blocked = "等待确认", False
        else:
            status, blocked = "拦截", True
        entry = candles[i]["c"]
        tp1 = entry * (1 + TP1_PCT / 100) if sd > 0 else entry * (1 - TP1_PCT / 100)
        sl = entry * (1 - SL_PCT / 100) if sd > 0 else entry * (1 + SL_PCT / 100)
        tr = trade_by_ts.get(ts)
        if blocked:
            exit_px, reason, pnl_pct, pnl_u, bars = None, "评分拦截", None, None, None
        elif tr:
            exit_px = tr["exit"]; reason = tr["reason"]
            pnl_pct = tr["pnl_pct"]; pnl_u = tr["pnl"]; bars = tr["bars"]
            if status == "等待确认":
                reason = "评分确认·" + reason
        else:
            exit_px, reason, pnl_pct, pnl_u, bars = None, "无成交记录", None, None, None
        rows.append({
            "idx": k, "ts": ts, "date": ts_fmt(ts), "type": typ,
            "score": round(score, 1), "blocked": blocked, "status": status,
            "confirmed": confirmed,
            "entry": round(entry, 1), "tp1": round(tp1, 1), "sl": round(sl, 1),
            "exit": round(exit_px, 1) if exit_px is not None else None,
            "reason": reason, "pnl_pct": pnl_pct, "pnl_u": pnl_u, "bars": bars,
        })

    # ── 打印 ──
    n_pass = sum(1 for x in rows if not x["blocked"])
    n_block = len(rows) - n_pass
    n_conf = sum(1 for x in rows if x["status"] == "等待确认")
    print(f"\n  放行 {n_pass} 笔（含两阶段确认 {n_conf} 笔）/ 拦截 {n_block} 笔"
          f"（score_min={SCORE_MIN}）", flush=True)
    hdr = (f"{'#':>3} {'日期':>10} {'方向':>4} {'评分':>5} {'状态':>5} "
           f"{'入场':>10} {'TP1':>10} {'SL':>10} {'出场':>10} {'原因':>14} "
           f"{'盈亏%':>7} {'盈亏U':>8} {'K':>4}")
    print(hdr); print("-" * len(hdr))
    for x in rows:
        st = x["status"]
        ex = f"{x['exit']:.1f}" if x["exit"] is not None else "—"
        pp = f"{x['pnl_pct']:+.2f}" if x["pnl_pct"] is not None else "—"
        pu = f"{x['pnl_u']:+.2f}" if x["pnl_u"] is not None else "—"
        br = f"{x['bars']}" if x["bars"] is not None else "—"
        print(f"{x['idx']:>3} {x['date']:>10} {x['type']:>4} {x['score']:>5.1f} {st:>5} "
              f"{x['entry']:>10.1f} {x['tp1']:>10.1f} {x['sl']:>10.1f} {ex:>10} "
              f"{x['reason']:>14} {pp:>7} {pu:>8} {br:>4}", flush=True)

    # ── 落盘 CSV + JSON ──
    out_dir = os.path.dirname(__file__)
    csv_path = os.path.join(out_dir, "_score_signal_list_2026.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.writer(fp)
        w.writerow(["idx", "date", "type", "score", "blocked", "status", "confirmed",
                    "entry", "tp1", "sl", "exit", "reason", "pnl_pct", "pnl_u", "bars"])
        for x in rows:
            w.writerow([x["idx"], x["date"], x["type"], x["score"],
                        "Y" if x["blocked"] else "N", x["status"],
                        "Y" if x["confirmed"] else "N", x["entry"], x["tp1"], x["sl"],
                        x["exit"] if x["exit"] is not None else "",
                        x["reason"],
                        x["pnl_pct"] if x["pnl_pct"] is not None else "",
                        x["pnl_u"] if x["pnl_u"] is not None else "",
                        x["bars"] if x["bars"] is not None else ""])
    json_path = os.path.join(out_dir, "_score_signal_list_2026.json")
    with open(json_path, "w", encoding="utf-8") as fp:
        json.dump({"symbol": SYMBOL, "tf": "1h", "score_min": SCORE_MIN,
                   "range": {"start": ts_fmt(candles[0]["ts"]),
                             "end": ts_fmt(candles[-1]["ts"]), "bars": len(candles)},
                   "n_flips": len(rows), "n_pass": n_pass, "n_block": n_block,
                   "n_confirm": n_conf,
                   "rows": rows}, fp, ensure_ascii=False, indent=2)
    print(f"\nWrote {csv_path}\nWrote {json_path}")


if __name__ == "__main__":
    main()
