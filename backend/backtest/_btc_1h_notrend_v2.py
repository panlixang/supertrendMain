"""三配置对比：block_4h(基准) / +严格recognize无趋势(旧) / +新条件(ADX<15 且 间距<0.2%)。

新条件 = 用户指定的 1h 无趋势判定（相对 recognize 决策树，从「满足任一」改为「同时满足」）：
    ADX(14) < 15   且   |MA20 - MA60| / MA60 * 100 < 0.2   →  无趋势 → 不开新仓
两条必须【同时】成立才拦（AND）；任一不成立即放行。

口径与后端实盘 pattern_trade._allow_by_no_trend 完全一致：
  - 周期 = 当前下单周期 1h（不是 4h）
  - ADX 长度 14，MA 用 EMA 20 / 60
  - 数据未预热（None）一律放行
  - 只拦开新仓，反向平旧仓不受影响（trend_align 语义天然如此）

对照的 "旧" = recognize_pattern 决策树 dir==0/None（按 4h 调参、在 1h 上过严的那版，剩 43 单）。

信号/出场复用 _btc_pattern_tp_opt.py 口径：run_backtest + ExitRules
(TP1 分批 + 保本 + 跟随 ST + 轨道硬止损)。
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
from indicators import ma, super_trend, ta_adx
from pattern_recog import recognize as recognize_pattern
from position import ExitRules
from _live_cfg_backtest import fetch_candles, ts_fmt

SYMBOL = "BTC-USDT-SWAP"
GATE_TF = "1h"
BARS_N = 15600
MIN_TRADES = 15

ST_PERIODS = 10
ST_MULT = 3.0

# 无趋势判定参数（与后端 PatternConfig 默认值一致）
NT_ADX_LEN = 14
NT_ADX_TH = 15.0
NT_FAST = 20
NT_SLOW = 60
NT_MA_TYPE = "EMA"
NT_GAP_TH = 0.2          # 百分比

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


# ── 过滤器 1：block_4h（基准，只拦 4h 明确反向）──────────────
def _block4h_align(candles_1h, flips, candles_4h) -> dict[int, int]:
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


# ── 过滤器 2：1h recognize 决策树 dir==0/None（旧、严格版）──
def _h1_dir_map(candles_1h) -> dict[int, int | None]:
    pat = recognize_pattern(
        [{"ts": c["ts"], "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"]}
         for c in candles_1h]
    )["pattern"]
    return {p["ts"]: p.get("dir") for p in pat}


# ── 过滤器 3：新条件 ADX<15 且 MA20/60 间距<0.2%（同时满足）──
def _notrend_map(candles) -> dict[int, bool]:
    """逐根判定：True = 该根「无趋势」（应拦截）。未预热一律 False（放行）。"""
    h = [c["h"] for c in candles]
    l = [c["l"] for c in candles]
    cl = [c["c"] for c in candles]
    adx = ta_adx(h, l, cl, NT_ADX_LEN)
    m20 = ma(cl, NT_FAST, NT_MA_TYPE)
    m60 = ma(cl, NT_SLOW, NT_MA_TYPE)
    out: dict[int, bool] = {}
    for i, c in enumerate(candles):
        a, f, s = adx[i], m20[i], m60[i]
        if a is None or f is None or s is None or not s:
            out[c["ts"]] = False          # 数据不足 → 放行
            continue
        gap_pct = abs(f - s) / s * 100.0
        out[c["ts"]] = (a < NT_ADX_TH) and (gap_pct < NT_GAP_TH)
    return out


def _combine(flips, candles, align4h, ok_at) -> dict[int, int]:
    """block_4h 放行之上，再叠加一道「无趋势」拦截。

    ok_at: ts -> bool(该根是否放行开新仓)。True=放行，False=拦截。
    """
    out: dict[int, int] = {}
    for f in flips:
        i = f["i"]
        if i >= len(candles):
            continue
        ts = candles[i]["ts"]
        sig_dir = 1 if f["type"] == "buy" else -1
        four_ok = align4h.get(ts) == sig_dir
        trend_ok = bool(ok_at.get(ts, True))
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
    p = _params()
    rows = []
    for a in TP1_PCTS:
        for b in TP1_RATIOS:
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
    mid = len(candles) // 2
    c1, c2 = candles[:mid], candles[mid:]
    p = _params()
    for t in rows[:8]:
        a, b = t["tp1_pct"], t["tp1_ratio"]
        r1 = run_backtest(c1, p, init_cash=100.0, fee_rate=0.0005, allow_short=True,
                          sizing="fixed", margin_usdt=MARGIN, leverage=LEV,
                          exit_rules=_rules(a, b), trend_align=align)
        r2 = run_backtest(c2, p, init_cash=100.0, fee_rate=0.0005, allow_short=True,
                          sizing="fixed", margin_usdt=MARGIN, leverage=LEV,
                          exit_rules=_rules(a, b), trend_align=align)
        s1, s2 = _summarize(r1), _summarize(r2)
        t["half1"], t["half2"] = s1, s2
        t["stable"] = s1["pnl_u"] > 0 and s2["pnl_u"] > 0


candles: list = []


def main():
    global candles
    print(f"=== {SYMBOL} {GATE_TF} 无趋势过滤三配置对比 ===", flush=True)
    print(f"新条件: ADX({NT_ADX_LEN})<{NT_ADX_TH} 且 "
          f"MA{NT_FAST}/{NT_SLOW}间距<{NT_GAP_TH}% (同时满足才拦)", flush=True)

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
    n = len(flips)

    # 三个 align
    align4h = _block4h_align(candles, flips, c4)
    h1dir = _h1_dir_map(candles)
    align_recog = _combine(flips, candles, align4h,
                           {ts: (isinstance(d, int) and d != 0)
                            for ts, d in h1dir.items()})

    nt = _notrend_map(candles)
    align_new = _combine(flips, candles, align4h, {ts: not v for ts, v in nt.items()})

    # 判定率诊断
    def _rate(m):
        return sum(1 for v in m.values() if v) / max(1, len(m)) * 100
    nt_block_rate = _rate(nt)
    recog_notrend_rate = sum(
        1 for d in h1dir.values() if not (isinstance(d, int) and d != 0)
    ) / max(1, len(h1dir)) * 100
    print(f"\n[判定率 · 全样本 1h 根] 新条件判无趋势 {nt_block_rate:.1f}% "
          f"| recognize 判无趋势 {recog_notrend_rate:.1f}%", flush=True)

    # 拦截统计
    def _blocked(align):
        return sum(1 for f in flips
                   if align.get(candles[f["i"]]["ts"], 0) == -(1 if f["type"] == "buy" else -1))
    b4h, bg, nw = _blocked(align4h), _blocked(align_recog), _blocked(align_new)
    print(f"信号翻转 {n} 个 | block_4h 拦 {b4h} "
          f"| +recognize 再拦 {bg - b4h} (合计 {bg}) "
          f"| +新条件 再拦 {nw - b4h} (合计 {nw})", flush=True)

    # 当前线上默认档 1.5/70 三配置直接对比
    p = _params()
    def _run(align):
        return _summarize(run_backtest(
            candles, p, init_cash=100.0, fee_rate=0.0005, allow_short=True,
            sizing="fixed", margin_usdt=MARGIN, leverage=LEV,
            exit_rules=_rules(1.5, 70.0), trend_align=align))
    sb, sg, sn = _run(align4h), _run(align_recog), _run(align_new)
    print("\n--- 当前线上档 TP1 1.5% / 70% ---")
    for lab, s in [("仅block_4h      ", sb), ("+recognize(旧严)", sg), ("+新条件(ADX/MA) ", sn)]:
        print(f"{lab}: ret={s['ret_pct']}% dd={s['max_dd_pct']}% "
              f"trades={s['trades']} wr={s['win_rate']}% PF={s['profit_factor']} "
              f"止损={s['stops']}")

    # 完整网格
    grids = {}
    for lab, al in [("base", align4h), ("recog", align_recog), ("new", align_new)]:
        print(f"\n=== 网格 {lab} ===", flush=True)
        t0 = time.time()
        grids[lab] = _grid(al)
        print(f"  done {len(grids[lab])} rows in {time.time()-t0:.0f}s", flush=True)

    for lab, al in [("base", align4h), ("recog", align_recog), ("new", align_new)]:
        _stability(grids[lab], al)

    print("\n=== top8 对比 (score=ret/dd) ===", flush=True)
    hdr = (f"{'配置':<16}{'档位':<10}{'pnlU':>7}{'ret%':>8}{'dd%':>7}"
           f"{'笔':>5}{'wr%':>6}{'PF':>6}{'H1':>7}{'H2':>7}{'稳':>4}")
    print(hdr)
    print("-" * len(hdr))
    for lab, rows in [("仅block_4h", grids["base"]),
                      ("+recognize旧", grids["recog"]),
                      ("+新条件", grids["new"])]:
        for t in rows[:8]:
            a, b = t["tp1_pct"], t["tp1_ratio"]
            print(f"{lab:<16}{f'{a}/{b}':<10}{t['pnl_u']:>7}{t['ret_pct']:>8}"
                  f"{t['max_dd_pct']:>7}{t['trades']:>5}{t['win_rate']:>6}"
                  f"{t['profit_factor']:>6}{t['half1']['pnl_u']:>7}"
                  f"{t['half2']['pnl_u']:>7}{'Y' if t['stable'] else 'N':>4}")

    out = {
        "symbol": SYMBOL, "tf": GATE_TF, "st": f"{ST_PERIODS}x{ST_MULT}",
        "start": ts_fmt(start), "end": ts_fmt(end), "bars": len(candles),
        "no_trend_rule": {
            "logic": "AND（同时满足才拦）",
            "adx_len": NT_ADX_LEN, "adx_th": NT_ADX_TH,
            "ma": f"{NT_MA_TYPE}{NT_FAST}/{NT_SLOW}", "gap_th_pct": NT_GAP_TH,
        },
        "signals": n, "blocked_4h": b4h, "blocked_recog_total": bg,
        "blocked_new_total": nw,
        "bar_no_trend_rate_pct": round(nt_block_rate, 2),
        "bar_recog_no_trend_rate_pct": round(recog_notrend_rate, 2),
        "current_1.5_70": {"base": sb, "recog": sg, "new": sn},
        "recommend": {k: v[0] for k, v in grids.items()},
        "top8": {k: v[:8] for k, v in grids.items()},
    }
    path = os.path.join(os.path.dirname(__file__), "_btc_1h_notrend_v2.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {path}")
    print("\n=== RECOMMEND ===")
    for k, v in grids.items():
        print(f"{k:8}:", json.dumps(v[0], ensure_ascii=False))


if __name__ == "__main__":
    main()
