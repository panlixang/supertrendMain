"""豁免方案 vs 基线：分月/分季度对照，确认 Q3 亏损是市场还是豁免引入的。

扫描（_notrend_waive_scan）显示 mom12>=1.2 全年 +51U / dd -3.2%，
但 top6 全在 Q3（约 5月中~7月中）亏损。本脚本按日历季度/月份拆解，
把「基线 A」「只4h B」「mom12>=1.2」三条曲线并排对比。

用法：
  python _notrend_waive_verify.py
"""
from __future__ import annotations

import json
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
                                 ST_PERIODS, ST_MULT, INIT_CASH, START_2026,
                                 BARS_1H, BARS_4H)

WAIVE_N, WAIVE_X = 12, 1.2
MONTHLY = True


def _combine_mom(flips, candles, align4h, nt, mom_at, x, use_4h, use_nt):
    out = {}
    for f in flips:
        i = f["i"]
        if i >= len(candles):
            continue
        ts = candles[i]["ts"]
        sd = 1 if f["type"] == "buy" else -1
        ok4 = (align4h.get(ts) == sd) if use_4h else True
        m = mom_at.get(ts)
        okn = ((not nt.get(ts, False)) or (m is not None and m >= x)) \
            if use_nt else True
        out[ts] = sd if (ok4 and okn) else -sd
    return out


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

    print(f"=== 豁免验证 · {SYMBOL} · 2026 · {gate_tf} ===", flush=True)
    raw1 = fetch_candles(SYMBOL, gate_tf, BARS_1H)
    c4 = fetch_candles(SYMBOL, "4h", BARS_4H) if use_4h else []
    print(f"  fetch 1h={len(raw1)} 4h={len(c4)}", flush=True)

    candles = _slice_from(raw1, START_2026)
    nt_raw = _notrend_map(raw1, adx_th, gap_th)
    nt = {c["ts"]: nt_raw[c["ts"]] for c in candles}
    st1 = super_trend([c["o"] for c in raw1], [c["h"] for c in raw1],
                      [c["l"] for c in raw1], [c["c"] for c in raw1],
                      periods=ST_PERIODS, multiplier=ST_MULT, change_atr=True)
    b = len(raw1) - len(candles)
    flips = [{"i": f["i"] - b, "type": f["type"]}
             for f in (st1.get("flips") or []) if f["i"] >= b]
    align4h = _block4h_align(candles, flips, c4) if use_4h else {}

    mom_at = {}
    for f in flips:
        i = f["i"]
        if i < WAIVE_N or i >= len(candles):
            continue
        ts = candles[i]["ts"]
        d = 1 if f["type"] == "buy" else -1
        mom_at[ts] = (candles[i]["c"] / candles[i - WAIVE_N]["c"] - 1) * d * 100

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

    base_ok = {t: not v for t, v in nt.items()}
    cases = [
        ("A 线上", _combine(flips, candles, align4h, base_ok, use_4h, use_nt)),
        ("B 只4h", _combine(flips, candles, align4h, {}, use_4h, False)),
        (f"W mom{WAIVE_N}>={WAIVE_X}",
         _combine_mom(flips, candles, align4h, nt, mom_at, WAIVE_X,
                      use_4h, use_nt)),
    ]

    # 分段
    segs_q, segs_m = {}, {}
    for c in candles:
        segs_q.setdefault(_quarter_key(c["ts"]), []).append(c)
        if MONTHLY:
            segs_m.setdefault(_month_key(c["ts"]), []).append(c)

    def run_seg(segs):
        res = {}
        for name, al in cases:
            res[name] = {k: pnl_of(run(v, al)) for k, v in segs.items()}
        return res

    rq = run_seg(segs_q)
    print(f"\n--- 分季度 pnl(U) ---", flush=True)
    names = [c[0] for c in cases]
    hdr = f"{'季度':<10}" + "".join(f"{n:>18}" for n in names) + f"{'W-A':>10}"
    print(hdr)
    print("-" * len(hdr))
    for k in sorted(segs_q):
        vals = [rq[n][k] for n in names]
        print(f"{k:<10}" + "".join(f"{v:>18.1f}" for v in vals)
              + f"{vals[2]-vals[0]:>+10.1f}", flush=True)
    tot = [sum(rq[n].values()) for n in names]
    print(f"{'合计':<10}" + "".join(f"{v:>18.1f}" for v in tot)
          + f"{tot[2]-tot[0]:>+10.1f}", flush=True)

    if MONTHLY:
        rm = run_seg(segs_m)
        print(f"\n--- 分月 pnl(U) ---", flush=True)
        hdr2 = f"{'月':<10}" + "".join(f"{n:>18}" for n in names) + f"{'W-A':>10}"
        print(hdr2)
        print("-" * len(hdr2))
        for k in sorted(segs_m):
            vals = [rm[n][k] for n in names]
            print(f"{k:<10}" + "".join(f"{v:>18.1f}" for v in vals)
                  + f"{vals[2]-vals[0]:>+10.1f}", flush=True)

    # 全段汇总
    print(f"\n--- 全段 ---", flush=True)
    hdr3 = (f"{'方案':<18}{'pnlU':>8}{'收益%':>8}{'dd%':>7}{'笔':>5}{'wr%':>6}"
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
        print(f"{name:<18}{s['pnl_u']:>8}{s['ret_pct']:>8}{s['dd_pct']:>7}"
              f"{s['trades']:>5}{s['win_rate']:>6}{s['pf']:>6}{s['tp1']:>5}"
              f"{s['stops']:>5}{s['score']:>7}", flush=True)

    btc = full["A 线上"]["hold_pct"]
    print(f"\n  同期 BTC 买入持有 {btc}% → 名义敞口 1x 下 "
          f"W 方案 alpha = {full[f'W mom{WAIVE_N}>={WAIVE_X}']['ret_pct']-btc:+.2f}%"
          f"（A 方案 alpha {full['A 线上']['ret_pct']-btc:+.2f}%）", flush=True)

    out = {"symbol": SYMBOL, "tf": gate_tf, "waive": [WAIVE_N, WAIVE_X],
           "quarterly": rq, "monthly": rm if MONTHLY else None, "full": full,
           "btc_hold_pct": btc}
    path = os.path.join(os.path.dirname(__file__), "_notrend_waive_verify.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {path}", flush=True)


if __name__ == "__main__":
    main()
