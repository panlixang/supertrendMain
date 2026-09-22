"""对比：当前 block_4h 过滤  vs  叠加「1h 形态无趋势不开单」过滤。

信号/出场完全复用 _btc_pattern_tp_opt.py 口径(run_backtest + ExitRules 默认档，
TP1 平 70% + 保本 + 跟随 ST + 轨道硬止损)。唯一变量：是否在 block_4h 之上再加
一道 1h 形态过滤 —— 在 1h 信号当根识别 1h 趋势方向，dir==0(无明显趋势) / None
(数据不足) 一律不开新仓，仅 dir==±1(清晰趋势) 才允许(且仍须过 block_4h)。

结论看末尾 RECOMMEND / 对比表：叠加后笔数、收益、回撤、稳定性如何变化。
"""
from __future__ import annotations

import bisect
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest
from indicators import super_trend
from pattern_recog import recognize as recognize_pattern
from position import ExitRules
from _live_cfg_backtest import fetch_candles, ts_fmt

SYMBOL = "BTC-USDT-SWAP"
GATE_TF = "1h"
BARS_N = 15600
MIN_TRADES = 15

ST_PERIODS = 10
ST_MULT = 3.0

LEV = 10
MARGIN = 10.0
NOTIONAL = MARGIN * LEV
SL_PCT = 2.0
MOVE_BE = True
TRAIL_ST = True

TP1_PCTS = [0.5, 0.8, 1.0, 1.2, 1.5, 1.8, 2.0, 2.5, 3.0, 4.0]
TP1_RATIOS = [20, 30, 40, 50, 60, 70, 80, 100]


def _rules(tp1_pct: float, tp1_ratio: float) -> ExitRules:
    return ExitRules(
        tp1_pct=tp1_pct, tp1_ratio=tp1_ratio,
        sl_mode="st", sl_pct=SL_PCT,
        move_sl_to_entry=MOVE_BE, trail_with_st=TRAIL_ST,
    )


def _params() -> dict:
    return {"periods": ST_PERIODS, "multiplier": ST_MULT,
            "src": "hl2", "change_atr": True}


def _block4h_align(candles_1h, flips, candles_4h) -> dict[int, int]:
    """block_4h：只拦 4h 明确反向；4h 无趋势/不清一律放行。"""
    if not candles_4h:
        return {}
    pat = recognize_pattern(
        [{"ts": c["ts"], "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"]}
         for c in candles_4h]
    )["pattern"]
    pts = [p["ts"] for p in pat]
    dir_by = {p["ts"]: p.get("dir") for p in pat}
    out: dict[int, int] = {}
    for f in flips:
        i = f["i"]
        if i >= len(candles_1h):
            continue
        ts = candles_1h[i]["ts"]
        sig_dir = 1 if f["type"] == "buy" else -1
        idx = bisect.bisect_right(pts, ts) - 1
        pdir = dir_by[pts[idx]] if idx >= 0 else None
        allowed = (pdir is None) or (pdir == 0) or (pdir == sig_dir)
        out[ts] = sig_dir if allowed else -sig_dir
    return out


def _h1_dir_map(candles_1h) -> dict[int, int | None]:
    """1h 形态方向：逐根 dir(±1/0/None)。"""
    pat = recognize_pattern(
        [{"ts": c["ts"], "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"]}
         for c in candles_1h]
    )["pattern"]
    return {p["ts"]: p.get("dir") for p in pat}


def _combine(flips, candles_1h, align4h, h1dir) -> dict[int, int]:
    """block_4h 放行之上，再加 1h 无趋势拦截：1h dir==0/None 也拦。"""
    out: dict[int, int] = {}
    for f in flips:
        i = f["i"]
        if i >= len(candles_1h):
            continue
        ts = candles_1h[i]["ts"]
        sig_dir = 1 if f["type"] == "buy" else -1
        four_ok = align4h.get(ts) == sig_dir
        d = h1dir.get(ts)
        trend_ok = isinstance(d, int) and d != 0
        out[ts] = sig_dir if (four_ok and trend_ok) else -sig_dir
    return out


def _summarize(r: dict) -> dict:
    pnl = round(r["final"] - r["init_cash"], 2)
    return {
        "pnl_u": pnl,
        "ret_pct": round(pnl / NOTIONAL * 100, 2),
        "max_dd_pct": r["max_dd_pct"],
        "trades": r["trades"],
        "win_rate": r["win_rate"],
        "profit_factor": r["profit_factor"],
        "avg_win": r["avg_win"], "avg_loss": r["avg_loss"],
        "tp1": r["tp1_count"], "stops": r["stop_count"],
        "reverses": r["reverse_count"],
    }


def _score(s: dict) -> float:
    if s["trades"] < MIN_TRADES or s["max_dd_pct"] <= 0:
        return -1e9
    return s["ret_pct"] / s["max_dd_pct"]


def _grid(align):
    """对给定 trend_align 跑完整网格，返回排序后的 rows。"""
    p = _params()
    combos = [(a, b) for a in TP1_PCTS for b in TP1_RATIOS]
    rows = []
    for a, b in combos:
        r = run_backtest(
            candles, p, init_cash=100.0, fee_rate=0.0005, allow_short=True,
            sizing="fixed", margin_usdt=MARGIN, leverage=LEV,
            exit_rules=_rules(a, b), trend_align=align,
        )
        if "error" in r:
            continue
        s = _summarize(r)
        s.update({"tp1_pct": a, "tp1_ratio": b, "score": round(_score(s), 3)})
        rows.append(s)
    rows.sort(key=lambda x: x["score"], reverse=True)
    return rows


def _stability(rows, align):
    """top8 前后半段稳定性。"""
    mid = len(candles) // 2
    c1, c2 = candles[:mid], candles[mid:]
    p = _params()
    for t in rows[:8]:
        a, b = t["tp1_pct"], t["tp1_ratio"]
        r1 = run_backtest(c1, p, init_cash=100.0, fee_rate=0.0005, allow_short=True,
                          sizing="fixed", margin_usdt=MARGIN, leverage=LEV,
                          exit_rules=_rules(a, b), trend_align=align)
        s1 = _summarize(r1)
        r2 = run_backtest(c2, p, init_cash=100.0, fee_rate=0.0005, allow_short=True,
                          sizing="fixed", margin_usdt=MARGIN, leverage=LEV,
                          exit_rules=_rules(a, b), trend_align=align)
        s2 = _summarize(r2)
        t["half1"] = s1
        t["half2"] = s2
        t["stable"] = s1["pnl_u"] > 0 and s2["pnl_u"] > 0


# ── 全局数据，main 里填充 ──
candles: list = []


def main():
    global candles
    print(f"=== {SYMBOL} {GATE_TF} block_4h vs block_4h+1h无趋势 对比 ===", flush=True)
    candles = fetch_candles(SYMBOL, GATE_TF, BARS_N)
    if len(candles) < ST_PERIODS + 5:
        print("K线不足", flush=True)
        return
    start, end = candles[0]["ts"], candles[-1]["ts"]
    print(f"1h bars={len(candles)} {ts_fmt(start)} ~ {ts_fmt(end)}", flush=True)

    c4 = fetch_candles(SYMBOL, "4h", 4200)
    print(f"4h bars={len(c4)} for block_4h", flush=True)

    st1 = super_trend(
        [c["o"] for c in candles], [c["h"] for c in candles],
        [c["l"] for c in candles], [c["c"] for c in candles],
        periods=ST_PERIODS, multiplier=ST_MULT, change_atr=True,
    )
    flips = st1.get("flips") or []

    align4h = _block4h_align(candles, flips, c4)
    h1dir = _h1_dir_map(candles)
    align_both = _combine(flips, candles, align4h, h1dir)

    # 各滤镜拦截统计（按信号根）
    n = len(flips)
    b4h_block = sum(1 for f in flips
                    if align4h.get(candles[f["i"]]["ts"], 0) == -(1 if f["type"] == "buy" else -1))
    both_block = sum(1 for f in flips
                     if align_both.get(candles[f["i"]]["ts"], 0) == -(1 if f["type"] == "buy" else -1))
    h1_only_block = both_block - b4h_block
    print(f"信号翻转 {n} 个 | block_4h 拦截 {b4h_block} "
          f"| 叠加1h无趋势后再额外拦截 {h1_only_block} "
          f"(合计拦截 {both_block})", flush=True)

    # 当前线上默认档(1.5%/70%) 两配置直接对比
    p = _params()
    r_b = run_backtest(candles, p, init_cash=100.0, fee_rate=0.0005, allow_short=True,
                       sizing="fixed", margin_usdt=MARGIN, leverage=LEV,
                       exit_rules=_rules(1.5, 70.0), trend_align=align4h)
    r_x = run_backtest(candles, p, init_cash=100.0, fee_rate=0.0005, allow_short=True,
                       sizing="fixed", margin_usdt=MARGIN, leverage=LEV,
                       exit_rules=_rules(1.5, 70.0), trend_align=align_both)
    sb, sx = _summarize(r_b), _summarize(r_x)
    print(f"\n[当前线上 1.5%/70%] 仅block_4h  : pnl={sb['pnl_u']}U ret={sb['ret_pct']}% "
          f"dd={sb['max_dd_pct']}% trades={sb['trades']} wr={sb['win_rate']}% PF={sb['profit_factor']}")
    print(f"[当前线上 1.5%/70%] +1h无趋势    : pnl={sx['pnl_u']}U ret={sx['ret_pct']}% "
          f"dd={sx['max_dd_pct']}% trades={sx['trades']} wr={sx['win_rate']}% PF={sx['profit_factor']}")

    # 完整网格
    print("\n=== 网格 block_4h (基准) ===", flush=True)
    t0 = time.time()
    rows_b = _grid(align4h)
    print(f"  done {len(rows_b)} rows in {time.time()-t0:.0f}s", flush=True)
    print("=== 网格 block_4h + 1h无趋势 ===", flush=True)
    t0 = time.time()
    rows_x = _grid(align_both)
    print(f"  done {len(rows_x)} rows in {time.time()-t0:.0f}s", flush=True)

    _stability(rows_b, align4h)
    _stability(rows_x, align_both)

    print("\n=== top8 对比 (score=ret/dd) ===", flush=True)
    hdr = (f"{'配置':<22}{'档位':<10}{'pnlU':>7}{'ret%':>8}{'dd%':>7}"
           f"{'笔':>5}{'wr%':>6}{'PF':>6}{'H1':>7}{'H2':>7}{'稳':>4}")
    print(hdr)
    print("-" * len(hdr))
    for label, rows in [("仅block_4h", rows_b), ("+1h无趋势", rows_x)]:
        for t in rows[:8]:
            a, b = t["tp1_pct"], t["tp1_ratio"]
            print(f"{label:<22}{f'{a}/{b}':<10}{t['pnl_u']:>7}{t['ret_pct']:>8}"
                  f"{t['max_dd_pct']:>7}{t['trades']:>5}{t['win_rate']:>6}"
                  f"{t['profit_factor']:>6}{t['half1']['pnl_u']:>7}"
                  f"{t['half2']['pnl_u']:>7}{'Y' if t['stable'] else 'N':>4}")

    rec_b = rows_b[0]
    rec_x = rows_x[0]
    out = {
        "symbol": SYMBOL, "tf": GATE_TF, "st": f"{ST_PERIODS}×{ST_MULT}",
        "start": ts_fmt(start), "end": ts_fmt(end), "bars": len(candles),
        "signals": n, "block4h_blocked": b4h_block,
        "plus1h_extra_blocked": h1_only_block, "both_blocked": both_block,
        "current_1.5_70": {"block4h": sb, "plus_1h": sx},
        "recommend_block4h": rec_b,
        "recommend_plus_1h": rec_x,
        "top8_block4h": rows_b[:8],
        "top8_plus_1h": rows_x[:8],
    }
    path = os.path.join(os.path.dirname(__file__), "_btc_1h_notrend_cmp.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {path}")
    print("\n=== RECOMMEND ===")
    print("block_4h 最优   :", json.dumps(rec_b, ensure_ascii=False))
    print("+1h无趋势 最优  :", json.dumps(rec_x, ensure_ascii=False))


if __name__ == "__main__":
    main()
