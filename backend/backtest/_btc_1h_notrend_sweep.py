"""1h 无趋势阈值扫描：ADX 阈值 × MA20/60 间距阈值 → 找甜点。

新条件家族：
    no_trend = (ADX(14) < adx_th) 且 (|MA20-MA60|/MA60*100 < gap_th)   → 拦截
扫描两个阈值，看「拦截力度 / 单量 / 收益 / 回撤 / 前后半段稳定性」如何变化，
目标：单量落在合理区间(约 80~150)、且前后半段都为正。

对照行：
  base  = 仅 block_4h
  recog = +recognize 决策树 dir==0/None（旧严格版，43 单）
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

ST_PERIODS = 10
ST_MULT = 3.0
ADX_LEN = 14
FAST, SLOW, MA_TYPE = 20, 60, "EMA"

LEV = 10
MARGIN = 10.0
NOTIONAL = MARGIN * LEV
SL_PCT = 2.0

# 扫描网格
ADX_THS = [15.0, 18.0, 20.0, 25.0, 30.0]
GAP_THS = [0.2, 0.3, 0.5, 0.8, 1.2, 2.0]

TP1_PCTS = [0.5, 0.8, 1.0, 1.2, 1.5, 1.8, 2.0, 2.5, 3.0, 4.0]
TP1_RATIOS = [20, 30, 40, 50, 60, 70, 80, 100]

candles: list = []


def _rules(a, b):
    return ExitRules(tp1_pct=a, tp1_ratio=b, sl_mode="st", sl_pct=SL_PCT,
                     move_sl_to_entry=True, trail_with_st=True)


def _params():
    return {"periods": ST_PERIODS, "multiplier": ST_MULT, "src": "hl2", "change_atr": True}


def _block4h_align(candles_1h, flips, candles_4h):
    if not candles_4h:
        return {}
    pat = recognize_pattern(
        [{"ts": c["ts"], "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"]}
         for c in candles_4h])["pattern"]
    pts = [p["ts"] for p in pat]
    dir_by = {p["ts"]: p.get("dir") for p in pat}
    out = {}
    for f in flips:
        i = f["i"]
        if i >= len(candles_1h):
            continue
        ts = candles_1h[i]["ts"]
        sd = 1 if f["type"] == "buy" else -1
        idx = bisect.bisect_right(pts, ts) - 1
        pdir = dir_by[pts[idx]] if idx >= 0 else None
        allowed = (pdir is None) or (pdir == 0) or (pdir == sd)
        out[ts] = sd if allowed else -sd
    return out


def _combine_mask(flips, align4h, mask):
    """mask: 逐根 bool，True=该根无趋势(拦截)。"""
    out = {}
    for f in flips:
        i = f["i"]
        if i >= len(candles):
            continue
        ts = candles[i]["ts"]
        sd = 1 if f["type"] == "buy" else -1
        no_trend = bool(mask[i]) if i < len(mask) else False
        out[ts] = sd if (align4h.get(ts) == sd and not no_trend) else -sd
    return out


def _summarize(r):
    pnl = round(r["final"] - r["init_cash"], 2)
    return {"pnl_u": pnl, "ret_pct": round(pnl / NOTIONAL * 100, 2),
            "max_dd_pct": r["max_dd_pct"], "trades": r["trades"],
            "win_rate": r["win_rate"], "profit_factor": r["profit_factor"],
            "stops": r["stop_count"], "tp1": r["tp1_count"]}


def _run(align, a=1.5, b=70.0, cs=None):
    return _summarize(run_backtest(
        cs if cs is not None else candles, _params(), init_cash=100.0,
        fee_rate=0.0005, allow_short=True, sizing="fixed",
        margin_usdt=MARGIN, leverage=LEV, exit_rules=_rules(a, b),
        trend_align=align))


def _halves(align, a=1.5, b=70.0):
    mid = len(candles) // 2
    return (_run(align, a, b, candles[:mid]), _run(align, a, b, candles[mid:]))


def _grid(align):
    rows = []
    for a in TP1_PCTS:
        for b in TP1_RATIOS:
            s = _run(align, a, b)
            if s["trades"] < 15 or s["max_dd_pct"] <= 0:
                sc = -1e9
            else:
                sc = s["ret_pct"] / s["max_dd_pct"]
            s.update({"tp1_pct": a, "tp1_ratio": b, "score": round(sc, 3)})
            rows.append(s)
    rows.sort(key=lambda x: x["score"], reverse=True)
    for t in rows[:5]:
        h1, h2 = _halves(align, t["tp1_pct"], t["tp1_ratio"])
        t["half1"], t["half2"] = h1, h2
        t["stable"] = h1["pnl_u"] > 0 and h2["pnl_u"] > 0
    return rows


def main():
    global candles
    print(f"=== {SYMBOL} {GATE_TF} 无趋势阈值扫描 ===", flush=True)
    candles = fetch_candles(SYMBOL, GATE_TF, BARS_N)
    c4 = fetch_candles(SYMBOL, "4h", 4200)
    print(f"1h bars={len(candles)} {ts_fmt(candles[0]['ts'])} ~ "
          f"{ts_fmt(candles[-1]['ts'])} | 4h={len(c4)}", flush=True)

    st1 = super_trend([c["o"] for c in candles], [c["h"] for c in candles],
                      [c["l"] for c in candles], [c["c"] for c in candles],
                      periods=ST_PERIODS, multiplier=ST_MULT, change_atr=True)
    flips = st1.get("flips") or []
    n = len(flips)
    align4h = _block4h_align(candles, flips, c4)

    # 预计算指标（只算一次）
    adx = ta_adx([c["h"] for c in candles], [c["l"] for c in candles],
                 [c["c"] for c in candles], ADX_LEN)
    m20 = ma([c["c"] for c in candles], FAST, MA_TYPE)
    m60 = ma([c["c"] for c in candles], SLOW, MA_TYPE)
    gap = []
    for f, s in zip(m20, m60):
        gap.append(abs(f - s) / s * 100.0 if (f is not None and s) else None)

    # 参照行
    print(f"\n信号翻转 {n} 个", flush=True)
    rows = []
    s0 = _run(align4h); h1, h2 = _halves(align4h)
    rows.append({"tag": "base(仅4h)", "adx_th": None, "gap_th": None, **s0,
                 "half1": h1, "half2": h2, "stable": h1["pnl_u"] > 0 and h2["pnl_u"] > 0})

    print("\n=== 扫描 (线上档 TP1 1.5%/70%) ===", flush=True)
    hdr = (f"{'ADX<':>6}{'间距<%':>7}{'无趋势%':>8}{'拦':>5}{'笔':>5}"
           f"{'ret%':>8}{'dd%':>7}{'wr%':>6}{'PF':>6}{'H1':>8}{'H2':>8}{'稳':>4}")
    print(hdr)
    print("-" * len(hdr))

    for a_th in ADX_THS:
        for g_th in GAP_THS:
            mask = []
            for i in range(len(candles)):
                a, g = adx[i], gap[i]
                mask.append(
                    False if (a is None or g is None)
                    else (a < a_th and g < g_th))
            align = _combine_mask(flips, align4h, mask)
            s = _run(align)
            h1, h2 = _halves(align)
            rate = sum(1 for v in mask if v) / len(mask) * 100
            blk = sum(1 for f in flips
                      if align.get(candles[f["i"]]["ts"], 0) == -(1 if f["type"] == "buy" else -1))
            stable = h1["pnl_u"] > 0 and h2["pnl_u"] > 0
            print(f"{a_th:>6}{g_th:>7}{rate:>8.1f}{blk:>5}{s['trades']:>5}"
                  f"{s['ret_pct']:>8}{s['max_dd_pct']:>7}{s['win_rate']:>6}"
                  f"{s['profit_factor']:>6}{h1['pnl_u']:>8}{h2['pnl_u']:>8}"
                  f"{'Y' if stable else 'N':>4}", flush=True)
            rows.append({"tag": f"adx{a_th}/gap{g_th}", "adx_th": a_th, "gap_th": g_th,
                         "bar_rate_pct": round(rate, 2), "blocked": blk,
                         **s, "half1": h1, "half2": h2, "stable": stable})

    # 甜点：单量 60~180 且两段为正
    sweet = [r for r in rows if r.get("trades")
             and 60 <= r["trades"] <= 180 and r["stable"]]
    sweet.sort(key=lambda x: x["ret_pct"] / max(0.01, x["max_dd_pct"]), reverse=True)
    print(f"\n=== 甜点候选(单量60~180 且 两段均正): {len(sweet)} 个 ===", flush=True)
    for r in sweet[:8]:
        print(f"  ADX<{r['adx_th']} 间距<{r['gap_th']}% : 笔={r['trades']} "
              f"ret={r['ret_pct']}% dd={r['max_dd_pct']}% PF={r['profit_factor']} "
              f"H1={r['half1']['pnl_u']} H2={r['half2']['pnl_u']}")

    # 对甜点 top3 跑完整网格
    print("\n=== 甜点 top3 完整网格 ===", flush=True)
    grids = {}
    for r in sweet[:3]:
        a_th, g_th = r["adx_th"], r["gap_th"]
        mask = [False if (adx[i] is None or gap[i] is None)
                else (adx[i] < a_th and gap[i] < g_th) for i in range(len(candles))]
        align = _combine_mask(flips, align4h, mask)
        t0 = time.time()
        g = _grid(align)
        grids[f"adx{a_th}/gap{g_th}"] = g
        print(f"--- ADX<{a_th} 间距<{g_th}% ({time.time()-t0:.0f}s) ---")
        for t in g[:5]:
            print(f"   {t['tp1_pct']}/{t['tp1_ratio']:<4} ret={t['ret_pct']}% "
                  f"dd={t['max_dd_pct']}% 笔={t['trades']} PF={t['profit_factor']} "
                  f"H1={t['half1']['pnl_u']} H2={t['half2']['pnl_u']} "
                  f"{'稳' if t['stable'] else '不稳'}")

    path = os.path.join(os.path.dirname(__file__), "_btc_1h_notrend_sweep.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"symbol": SYMBOL, "tf": GATE_TF, "signals": n,
                   "sweep": rows, "sweet": sweet[:8],
                   "grids": grids}, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {path}")


if __name__ == "__main__":
    main()
