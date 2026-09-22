"""BTC-USDT-SWAP · 2026 年 · 形态识别页线上参数回测 + TP1 寻优。

配置源 = 形态识别页自己的接口（不是首页 /api/trade/*）：
  http://43.108.10.84:5174/api/pattern/trade/config
    cfg      全局：block_4h / no_trend_block / no_trend_adx / no_trend_ma_gap /
                  tp1_pct / tp1_ratio / sl_pct / move_sl_to_entry / trail_with_st
    symbols  品种级：margin_usdt / leverage / allow_tfs（出场字段为 null 时回落全局）

SuperTrend = 10 × 3.0，来自 pattern_trade.py:44-45 的 ST_PERIODS / ST_MULTIPLIER ——
形态页基础信号是硬编码常量，既不取 /api/params（首页 18×3.0），也不取交易页品种 params。

区间：2026-01-01 ~ 今。--warm 可跑含 2025-12 预热月的对照。

用法：
  python _live_btc_2026_tp1.py            # 只跑线上基准档
  python _live_btc_2026_tp1.py --warm     # + 预热对照
  python _live_btc_2026_tp1.py --grid     # + TP1 网格
"""
from __future__ import annotations

import bisect
import json
import os
import sys
import time
import urllib.request
from dataclasses import replace
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest
from indicators import ma, super_trend, ta_adx
from pattern_recog import recognize as recognize_pattern
from position import ExitRules
from _live_cfg_backtest import fetch_candles, ts_fmt

CFG_URL = "http://43.108.10.84:5174/api/pattern/trade/config"
SYMBOL = "BTC-USDT-SWAP"

# 形态页基础信号常量（pattern_trade.py:44-45）
ST_PERIODS = 10
ST_MULT = 3.0

# 无趋势判定口径（pattern_trade._allow_by_no_trend）：固定 ADX(14) + EMA20/60，
# 两个阈值由线上 cfg 提供
NT_ADX_LEN = 14
NT_FAST, NT_SLOW, NT_MA_TYPE = 20, 60, "EMA"

# 账户本金：线上保证金 100U，取 1000U 让等效敞口 = 名义 1000U / 本金 1000U = 1.0x
INIT_CASH = 1000.0
MIN_TRADES = 10

START_2026 = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
WARM_START = int(datetime(2025, 12, 1, tzinfo=timezone.utc).timestamp() * 1000)

BARS_1H = 7300
BARS_4H = 1900

TP1_PCTS = [0.5, 0.8, 1.0, 1.2, 1.5, 1.8, 2.0, 2.5, 3.0]
TP1_RATIOS = [20, 30, 40, 50, 60, 70, 80, 100]


def _get(url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers={"User-Agent": "supertrend-bt/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _slice_from(arr: list[dict], ts: int) -> list[dict]:
    return [c for c in arr if c["ts"] >= ts]


# ── 过滤器 ────────────────────────────────────────────────
def _block4h_align(candles_1h, flips, candles_4h) -> dict[int, int]:
    """block_4h：4h 形态明确反向才拦；4h 无趋势/方向不清一律放行。"""
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


def _notrend_map(candles, adx_th: float, gap_th: float) -> dict[int, bool]:
    """True = 该根判「无趋势」（应拦）。ADX(14)<adx_th 且 EMA20/60 间距<gap_th，
    两条同时成立；数据未预热一律放行。"""
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
            out[c["ts"]] = False
            continue
        out[c["ts"]] = (a < adx_th) and (abs(f - s) / s * 100.0 < gap_th)
    return out


def _combine(flips, candles, align4h, ok_at) -> dict[int, int]:
    out: dict[int, int] = {}
    for f in flips:
        i = f["i"]
        if i >= len(candles):
            continue
        ts = candles[i]["ts"]
        sig_dir = 1 if f["type"] == "buy" else -1
        out[ts] = sig_dir if (align4h.get(ts) == sig_dir
                              and bool(ok_at.get(ts, True))) else -sig_dir
    return out


def _summarize(r: dict, notional: float, margin: float) -> dict:
    pnl = round(r["final"] - r["init_cash"], 2)
    eq_ret = pnl / INIT_CASH * 100
    return {
        "pnl_u": pnl,
        "equity_ret_pct": round(eq_ret, 2),
        "notional_ret_pct": round(pnl / notional * 100, 2),
        "margin_roi_pct": round(pnl / margin * 100, 1),
        "hold_pct": r["hold_pct"],
        "alpha_pct": round(eq_ret - r["hold_pct"], 2),
        "max_dd_pct": r["max_dd_pct"],
        "trades": r["trades"],
        "win_rate": r["win_rate"],
        "profit_factor": r["profit_factor"],
        "avg_win": r["avg_win"], "avg_loss": r["avg_loss"],
        "tp1": r["tp1_count"], "stops": r["stop_count"],
        "reverses": r["reverse_count"],
        "align_blocked": r["align_blocked"],
    }


def _score(s: dict) -> float:
    if s["trades"] < MIN_TRADES or s["max_dd_pct"] <= 0:
        return -1e9
    return s["notional_ret_pct"] / s["max_dd_pct"]


def main():
    args = set(sys.argv[1:])
    do_grid = "--grid" in args
    do_warm = "--warm" in args

    live = _get(CFG_URL)
    cfg = live["cfg"]
    sym = next((s for s in live["symbols"] if s["symbol"] == SYMBOL), None)
    if sym is None:
        print(f"线上形态页无 {SYMBOL}", flush=True)
        return

    gate_tf = sym["allow_tfs"][0]
    margin = float(sym["margin_usdt"])
    lev = int(sym["leverage"])
    notional = margin * lev

    def _v(a, b):
        return a if a is not None else b

    tp1_pct = _v(sym["tp1_pct"], cfg["tp1_pct"])
    tp1_ratio = _v(sym["tp1_ratio"], cfg["tp1_ratio"])
    sl_pct = _v(sym["sl_pct"], cfg["sl_pct"])
    mv_be = _v(sym["move_sl_to_entry"], cfg["move_sl_to_entry"])
    trail = _v(sym["trail_with_st"], cfg["trail_with_st"])

    adx_th = float(cfg["no_trend_adx"])
    gap_th = float(cfg["no_trend_ma_gap"])
    use_4h = bool(cfg["block_4h"])
    use_nt = bool(cfg["no_trend_block"])

    print(f"=== {SYMBOL} · 2026 年 · 形态识别页线上参数 ===", flush=True)
    print(f"  信号 ST = {ST_PERIODS}×{ST_MULT} (pattern_trade.py 硬编码常量)", flush=True)
    print(f"  下单值  = margin {margin}U × {lev}x → 名义 {notional}U/笔 "
          f"(本金 {INIT_CASH}U, 等效敞口 {notional/INIT_CASH:.2f}x)", flush=True)
    print(f"  出场    = TP1 {tp1_pct}%/{tp1_ratio}%  SL {sl_pct}% "
          f"保本={mv_be} 跟随ST={trail} (ExitRules 基础版, 无 TP2/TP3)", flush=True)
    print(f"  闸门    = block_4h={use_4h} | no_trend_block={use_nt} "
          f"ADX(14)<{adx_th} 且 EMA20/60间距<{gap_th}%", flush=True)

    p = {"periods": ST_PERIODS, "multiplier": ST_MULT,
         "src": "hl2", "change_atr": True}

    def _rules(a, b):
        return ExitRules(tp1_pct=a, tp1_ratio=b, sl_mode="st", sl_pct=sl_pct,
                         move_sl_to_entry=mv_be, trail_with_st=trail)

    # ── 抓数 ──────────────────────────────────────────────
    t0 = time.time()
    raw1 = fetch_candles(SYMBOL, gate_tf, BARS_1H)
    c4 = fetch_candles(SYMBOL, "4h", BARS_4H) if use_4h else []
    print(f"  fetch 1h={len(raw1)} 4h={len(c4)} ({time.time()-t0:.0f}s)", flush=True)

    candles = _slice_from(raw1, START_2026)
    if len(candles) < 300:
        print("  2026 年 K 线不足", flush=True)
        return

    st1 = super_trend([c["o"] for c in raw1], [c["h"] for c in raw1],
                      [c["l"] for c in raw1], [c["c"] for c in raw1],
                      periods=ST_PERIODS, multiplier=ST_MULT, change_atr=True)
    # 翻转索引相对 raw1，需换算到切片后的 candles
    base = len(raw1) - len(candles)
    flips = [{"i": f["i"] - base, "type": f["type"]}
             for f in (st1.get("flips") or []) if f["i"] >= base]

    align4h = _block4h_align(candles, flips, c4) if use_4h else {}
    if use_nt:
        nt = _notrend_map(candles, adx_th, gap_th)
        align = _combine(flips, candles, align4h,
                         {ts: not v for ts, v in nt.items()})
    else:
        align = align4h

    n_flip = len(flips)
    n_block = sum(1 for f in flips
                  if align.get(candles[f["i"]]["ts"], 0)
                  == -(1 if f["type"] == "buy" else -1))
    print(f"  回测区间 {ts_fmt(candles[0]['ts'])} ~ {ts_fmt(candles[-1]['ts'])} "
          f"({len(candles)} 根 {gate_tf})", flush=True)
    print(f"  翻转信号 {n_flip} 个，闸门拦截 {n_block} 个 "
          f"({n_block/max(1,n_flip)*100:.0f}%)", flush=True)

    def run(cds, rules):
        return run_backtest(
            cds, p, init_cash=INIT_CASH, fee_rate=0.0005, allow_short=True,
            exit_rules=rules, sizing="fixed", margin_usdt=margin,
            leverage=lev, trend_align=align,
        )

    # ── 基准 ──────────────────────────────────────────────
    base_rules = _rules(tp1_pct, tp1_ratio)
    t0 = time.time()
    rb = run(candles, base_rules)
    if "error" in rb:
        print("  error:", rb["error"], flush=True)
        return
    sb = _summarize(rb, notional, margin)
    print(f"\n--- 线上基准 TP1 {tp1_pct}%/{tp1_ratio}% (单次 {time.time()-t0:.0f}s) ---",
          flush=True)
    print(f"  pnl={sb['pnl_u']}U | 本金{sb['equity_ret_pct']}% "
          f"名义{sb['notional_ret_pct']}% 保证金ROI {sb['margin_roi_pct']}% "
          f"| dd={sb['max_dd_pct']}%", flush=True)
    print(f"  trades={sb['trades']} wr={sb['win_rate']}% PF={sb['profit_factor']} "
          f"| TP1={sb['tp1']} 止损={sb['stops']} 反手={sb['reverses']}", flush=True)
    print(f"  同期买入持有 {sb['hold_pct']}% → alpha {sb['alpha_pct']}%", flush=True)

    warm = None
    if do_warm:
        cw = _slice_from(raw1, WARM_START)
        bw = len(raw1) - len(cw)
        fw = [{"i": f["i"] - bw, "type": f["type"]}
              for f in (st1.get("flips") or []) if f["i"] >= bw]
        a4 = _block4h_align(cw, fw, c4) if use_4h else {}
        if use_nt:
            ntw = _notrend_map(cw, adx_th, gap_th)
            aw = _combine(fw, cw, a4, {ts: not v for ts, v in ntw.items()})
        else:
            aw = a4
        rw = run_backtest(
            cw, p, init_cash=INIT_CASH, fee_rate=0.0005, allow_short=True,
            exit_rules=base_rules, sizing="fixed", margin_usdt=margin,
            leverage=lev, trend_align=aw,
        )
        sw = _summarize(rw, notional, margin)
        warm = {"start": ts_fmt(cw[0]["ts"]), "end": ts_fmt(cw[-1]["ts"]),
                "bars": len(cw), **sw}
        print(f"\n--- 预热对照 含 2025-12 ({warm['start']} ~ {warm['end']}) ---",
              flush=True)
        print(f"  pnl={sw['pnl_u']}U 名义{sw['notional_ret_pct']}% "
              f"dd={sw['max_dd_pct']}% trades={sw['trades']} "
              f"(严格2026 = {sb['pnl_u']}U / {sb['trades']}笔)", flush=True)

    rows = []
    if do_grid:
        combos = [(a, b) for a in TP1_PCTS for b in TP1_RATIOS]
        print(f"\n--- TP1 网格 {len(combos)} 组 ---", flush=True)
        t0 = time.time()
        for i, (a, b) in enumerate(combos, 1):
            r = run(candles, _rules(a, b))
            if "error" in r:
                continue
            s = _summarize(r, notional, margin)
            s.update({"tp1_pct": a, "tp1_ratio": b, "score": round(_score(s), 3)})
            rows.append(s)
            if i % 12 == 0:
                print(f"  {i}/{len(combos)} … {time.time()-t0:.0f}s", flush=True)
        rows.sort(key=lambda x: x["score"], reverse=True)
        print(f"  grid done {len(rows)} rows in {time.time()-t0:.0f}s", flush=True)

        mid = len(candles) // 2
        c1, c2 = candles[:mid], candles[mid:]
        print("\n=== top8 前后半段稳定性 ===", flush=True)
        for t in rows[:8]:
            ru = _rules(t["tp1_pct"], t["tp1_ratio"])
            s1 = _summarize(run(c1, ru), notional, margin)
            s2 = _summarize(run(c2, ru), notional, margin)
            t["half1"], t["half2"] = s1, s2
            t["stable"] = s1["pnl_u"] > 0 and s2["pnl_u"] > 0
            print(f"  {t['tp1_pct']}%/{t['tp1_ratio']}%: full={t['pnl_u']}U "
                  f"dd={t['max_dd_pct']}% tr={t['trades']} | "
                  f"H1={s1['pnl_u']}U({s1['trades']}) H2={s2['pnl_u']}U({s2['trades']}) "
                  f"{'Y' if t['stable'] else 'N'}", flush=True)

        hdr = (f"{'tp1':<12}{'pnlU':>8}{'名义%':>8}{'dd%':>7}{'笔':>5}"
               f"{'wr%':>6}{'PF':>6}{'TP1':>5}{'止损':>5}{'评分':>8}")
        print("\n" + hdr)
        print("-" * len(hdr))
        for t in rows[:15]:
            print(f"{str(t['tp1_pct'])+'/'+str(t['tp1_ratio']):<12}"
                  f"{t['pnl_u']:>8}{t['notional_ret_pct']:>8}{t['max_dd_pct']:>7}"
                  f"{t['trades']:>5}{t['win_rate']:>6}{t['profit_factor']:>6}"
                  f"{t['tp1']:>5}{t['stops']:>5}{t['score']:>8}", flush=True)

    stable = [t for t in rows[:8] if t.get("stable")] if rows else []
    rec = (stable or rows or [None])[0]

    out = {
        "source": CFG_URL,
        "symbol": SYMBOL, "tf": gate_tf,
        "signal": {"st": f"{ST_PERIODS}x{ST_MULT}",
                   "note": "pattern_trade.py:44-45 硬编码常量"},
        "live_cfg": {
            "margin_usdt": margin, "leverage": lev, "notional": notional,
            "init_cash": INIT_CASH, "exposure_x": round(notional / INIT_CASH, 2),
            "tp1_pct": tp1_pct, "tp1_ratio": tp1_ratio, "sl_pct": sl_pct,
            "move_sl_to_entry": mv_be, "trail_with_st": trail,
            "block_4h": use_4h, "no_trend_block": use_nt,
            "no_trend_adx": adx_th, "no_trend_ma_gap": gap_th,
        },
        "range": {"start": ts_fmt(candles[0]["ts"]), "end": ts_fmt(candles[-1]["ts"]),
                  "bars": len(candles)},
        "flips": n_flip, "gate_blocked": n_block,
        "current": {**sb, "tp1_pct": tp1_pct, "tp1_ratio": tp1_ratio},
        "warm_control": warm,
        "recommend": rec,
        "top15": rows[:15],
        "all": rows,
    }
    path = os.path.join(os.path.dirname(__file__), "_live_btc_2026_tp1.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {path}", flush=True)
    if rec:
        print("\n=== RECOMMEND ===")
        print(json.dumps({k: v for k, v in rec.items()
                          if k not in ("half1", "half2")},
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
