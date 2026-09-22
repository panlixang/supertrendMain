"""2026 固定方案对比：量价双确认 vs 纯动量，看量能是否保住纯动量的增益。

复用 _notrend_waive_2026 的辅助与 _notrend_waive_2025 的 _combine_mom。
四方案：
  A 线上（4h + 无趋势）
  B 只 4h（关无趋势）
  Wm 纯动量 mom12>=1.2
  Wmv 量价 mom12>=1.2 且 vol20>=1.5x

用法：python _notrend_waive_2026_fixed.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest
from indicators import super_trend
from position import ExitRules
from _live_cfg_backtest import fetch_candles, ts_fmt
from _notrend_waive_2026 import (_get, _slice_from, _notrend_map,
                                 _block4h_align, _combine, CFG_URL, SYMBOL,
                                 ST_PERIODS, ST_MULT, INIT_CASH, BARS_4H)
from _notrend_waive_2025 import (_combine_mom, WAIVE_N, WAIVE_X,
                                 WAIVE_VOL_N, WAIVE_VOL_MULT)

START = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
END = int(datetime(2027, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
BARS_1H = 9000   # 覆盖 2026 全年 + 预热


def _month_key(ts):
    return datetime.fromtimestamp(ts / 1000, timezone.utc).strftime("%Y-%m")


def _quarter_key(ts):
    d = datetime.fromtimestamp(ts / 1000, timezone.utc)
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


def main():
    live = _get(CFG_URL)
    cfg = live["cfg"]
    sym = next(s for s in live["symbols"] if s["symbol"] == SYMBOL)
    gate_tf = sym["allow_tfs"][0]
    margin, lev = float(sym["margin_usdt"]), int(sym["leverage"])
    notional = margin * lev
    adx_th, gap_th = float(cfg["no_trend_adx"]), float(cfg["no_trend_ma_gap"])
    use_4h, use_nt = bool(cfg["block_4h"]), bool(cfg["no_trend_block"])

    def _v(a, b):
        return a if a is not None else b

    tp1_pct = _v(sym["tp1_pct"], cfg["tp1_pct"])
    tp1_ratio = _v(sym["tp1_ratio"], cfg["tp1_ratio"])
    sl_pct = _v(sym["sl_pct"], cfg["sl_pct"])

    print(f"=== 豁免对比 · 2026 · {SYMBOL} · {gate_tf} ===", flush=True)
    print(f"  闸门 ADX<{adx_th} 间距<{gap_th}% | mom{WAIVE_N}>={WAIVE_X}% "
          f"| vol{WAIVE_VOL_N}>={WAIVE_VOL_MULT}x", flush=True)

    raw1 = fetch_candles(SYMBOL, gate_tf, BARS_1H)
    c4 = fetch_candles(SYMBOL, "4h", BARS_4H) if use_4h else []
    print(f"  fetch 1h={len(raw1)} 4h={len(c4)}", flush=True)

    candles = _slice_from(raw1, START)
    candles = [c for c in candles if c["ts"] < END]
    print(f"  2026 区间 {len(candles)} 根 1h", flush=True)

    nt_raw = _notrend_map(raw1, adx_th, gap_th)
    nt = {c["ts"]: nt_raw[c["ts"]] for c in candles}
    st1 = super_trend([c["o"] for c in raw1], [c["h"] for c in raw1],
                      [c["l"] for c in raw1], [c["c"] for c in raw1],
                      periods=ST_PERIODS, multiplier=ST_MULT, change_atr=True)
    b = len(raw1) - len(candles)
    flips = [{"i": f["i"] - b, "type": f["type"]}
             for f in (st1.get("flips") or []) if f["i"] >= b]
    align4h = _block4h_align(candles, flips, c4) if use_4h else {}

    mom_at, vol_at = {}, {}
    for f in flips:
        i = f["i"]
        i_raw = i + b
        if i_raw < WAIVE_N or i_raw >= len(raw1):
            continue
        ts = candles[i]["ts"]
        d = 1 if f["type"] == "buy" else -1
        mom_at[ts] = (raw1[i_raw]["c"] / raw1[i_raw - WAIVE_N]["c"] - 1) * d * 100
        if i_raw >= WAIVE_VOL_N:
            window = [raw1[j]["vol"] for j in range(i_raw - WAIVE_VOL_N + 1,
                                                     i_raw + 1)]
            vma = sum(window) / WAIVE_VOL_N
            vol_at[ts] = (vma > 0 and raw1[i_raw]["vol"] >= vma * WAIVE_VOL_MULT)

    rules = ExitRules(tp1_pct=tp1_pct, tp1_ratio=tp1_ratio, sl_mode="st",
                      sl_pct=sl_pct,
                      move_sl_to_entry=_v(sym["move_sl_to_entry"],
                                          cfg["move_sl_to_entry"]),
                      trail_with_st=_v(sym["trail_with_st"], cfg["trail_with_st"]))
    p = {"periods": ST_PERIODS, "multiplier": ST_MULT,
         "src": "hl2", "change_atr": True}

    def run(cds, align):
        return run_backtest(cds, p, init_cash=INIT_CASH, fee_rate=0.0005,
                            allow_short=True, exit_rules=rules, sizing="fixed",
                            margin_usdt=margin, leverage=lev, trend_align=align)

    def pnl_of(r):
        return round(r["final"] - r["init_cash"], 2)

    WNAME_M = f"Wm mom{WAIVE_N}>={WAIVE_X}"
    WNAME_V = f"Wmv mom{WAIVE_N}>={WAIVE_X}&vol{WAIVE_VOL_N}x{WAIVE_VOL_MULT}"
    base_ok = {t: not v for t, v in nt.items()}
    cases = [
        ("A 线上", _combine(flips, candles, align4h, base_ok, use_4h, use_nt)),
        ("B 只4h", _combine(flips, candles, align4h, {}, use_4h, False)),
        (WNAME_M, _combine_mom(flips, candles, align4h, nt, mom_at, {},
                               WAIVE_X, use_4h, use_nt)),
        (WNAME_V, _combine_mom(flips, candles, align4h, nt, mom_at, vol_at,
                               WAIVE_X, use_4h, use_nt)),
    ]

    segs_q = {}
    segs_m = {}
    for c in candles:
        segs_q.setdefault(_quarter_key(c["ts"]), []).append(c)
        segs_m.setdefault(_month_key(c["ts"]), []).append(c)

    def run_seg(segs):
        return {name: {k: pnl_of(run(v, al)) for k, v in segs.items()}
                for name, al in cases}

    def print_seg(title, segs):
        print(f"\n--- {title} pnl(U) ---", flush=True)
        names = [c[0] for c in cases]
        hdr = f"{'段':<10}" + "".join(f"{n:>20}" for n in names) \
              + f"{'Wm-A':>9}{'Wmv-A':>9}"
        print(hdr)
        print("-" * len(hdr))
        for k in sorted(segs):
            vals = [run_seg(segs)[n][k] for n in names]
            print(f"{k:<10}" + "".join(f"{v:>20.1f}" for v in vals)
                  + f"{vals[2]-vals[0]:>+9.1f}{vals[3]-vals[0]:>+9.1f}",
                  flush=True)
        tot = [sum(run_seg(segs)[n].values()) for n in names]
        print(f"{'合计':<10}" + "".join(f"{v:>20.1f}" for v in tot)
              + f"{tot[2]-tot[0]:>+9.1f}{tot[3]-tot[0]:>+9.1f}", flush=True)

    print_seg("分季度", segs_q)
    print_seg("分月", segs_m)

    print(f"\n--- 全段 ---", flush=True)
    hdr3 = (f"{'方案':<22}{'pnlU':>8}{'收益%':>8}{'dd%':>7}{'笔':>5}{'wr%':>6}"
            f"{'PF':>6}{'TP1':>5}{'止损':>5}{'评分':>7}")
    print(hdr3)
    print("-" * len(hdr3))
    full = {}
    for name, al in cases:
        r = run(candles, al)
        pnl = pnl_of(r)
        dd = r["max_dd_pct"]
        full[name] = {"pnl_u": pnl, "ret_pct": round(pnl / INIT_CASH * 100, 2),
                      "dd_pct": dd, "trades": r["trades"],
                      "win_rate": r["win_rate"], "pf": r["profit_factor"],
                      "tp1": r["tp1_count"], "stops": r["stop_count"],
                      "score": round(pnl / notional * 100 / dd, 3) if dd else 0,
                      "hold_pct": r["hold_pct"]}
        s = full[name]
        print(f"{name:<22}{s['pnl_u']:>8}{s['ret_pct']:>8}{s['dd_pct']:>7}"
              f"{s['trades']:>5}{s['win_rate']:>6}{s['pf']:>6}{s['tp1']:>5}"
              f"{s['stops']:>5}{s['score']:>7}", flush=True)

    print(f"\n  同期 BTC 持有 {full['A 线上']['hold_pct']}% → "
          f"Wm alpha {full[WNAME_M]['ret_pct']-full['A 线上']['hold_pct']:+.2f}% | "
          f"Wmv alpha {full[WNAME_V]['ret_pct']-full['A 线上']['hold_pct']:+.2f}%",
          flush=True)
    print(f"\n  Wm-A={full[WNAME_M]['pnl_u']-full['A 线上']['pnl_u']:+.1f}U | "
          f"Wmv-A={full[WNAME_V]['pnl_u']-full['A 线上']['pnl_u']:+.1f}U | "
          f"Wmv-Wm={full[WNAME_V]['pnl_u']-full[WNAME_M]['pnl_u']:+.1f}U",
          flush=True)


if __name__ == "__main__":
    main()
