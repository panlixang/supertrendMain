"""2025 年样本外验证：最新方案（mom12>=1.2 启动豁免 + 线上配置）跑 BTC 1h。

复用 _notrend_waive_verify 的逻辑，时间窗切到 2025 全年（加足预热 K 线）。
对比三方案：
  A 线上（4h + 无趋势拦截）
  B 只 4h（关无趋势）
  W mom12>=1.2（无趋势 + 启动豁免）

用法：
  python _notrend_waive_2025.py
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
                                 ST_PERIODS, ST_MULT, INIT_CASH, BARS_4H)

WAIVE_N, WAIVE_X = 12, 1.2
WAIVE_VOL_N, WAIVE_VOL_MULT = 20, 1.5
START_2025 = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
END_2025 = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
BARS_1H = 16000   # 覆盖 2025 全年 + 预热（约 666 天）


def _combine_mom(flips, candles, align4h, nt, mom_at, vol_at, x, use_4h, use_nt):
    out = {}
    for f in flips:
        i = f["i"]
        if i >= len(candles):
            continue
        ts = candles[i]["ts"]
        sd = 1 if f["type"] == "buy" else -1
        ok4 = (align4h.get(ts) == sd) if use_4h else True
        m = mom_at.get(ts)
        v = vol_at.get(ts, True)   # 缺省 True = 不要求量能（纯动量模式）
        okn = ((not nt.get(ts, False))
               or (m is not None and m >= x and v)) \
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

    print(f"=== 启动豁免 · 2025 样本外 · {SYMBOL} · {gate_tf} ===", flush=True)
    print(f"  闸门 ADX<{adx_th} 间距<{gap_th}% | 豁免 mom{WAIVE_N}>={WAIVE_X}% "
          f"且 vol{WAIVE_VOL_N}>={WAIVE_VOL_MULT}x "
          f"| TP1 {tp1_pct}%/{tp1_ratio}% SL {sl_pct}%", flush=True)

    raw1 = fetch_candles(SYMBOL, gate_tf, BARS_1H)
    c4 = fetch_candles(SYMBOL, "4h", BARS_4H) if use_4h else []
    print(f"  fetch 1h={len(raw1)} 4h={len(c4)}", flush=True)

    candles = _slice_from(raw1, START_2025)
    candles = [c for c in candles if c["ts"] < END_2025]
    print(f"  2025 区间 {len(candles)} 根 1h", flush=True)

    nt_raw = _notrend_map(raw1, adx_th, gap_th)
    nt = {c["ts"]: nt_raw[c["ts"]] for c in candles}
    st1 = super_trend([c["o"] for c in raw1], [c["h"] for c in raw1],
                      [c["l"] for c in raw1], [c["c"] for c in raw1],
                      periods=ST_PERIODS, multiplier=ST_MULT, change_atr=True)
    b = len(raw1) - len(candles)
    flips = [{"i": f["i"] - b, "type": f["type"]}
             for f in (st1.get("flips") or []) if f["i"] >= b]
    align4h = _block4h_align(candles, flips, c4) if use_4h else {}

    # mom / vol 基于 raw1 全量算（带预热）；flips 已偏移 b 对齐 candles，
    # 特征用 i_raw = i + b 取 raw1 原位置，ts 用 candles[i]（与 _combine 一致）
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

    base_ok = {t: not v for t, v in nt.items()}
    WNAME = (f"W mom{WAIVE_N}>={WAIVE_X}%&vol{WAIVE_VOL_N}x{WAIVE_VOL_MULT}")
    cases = [
        ("A 线上", _combine(flips, candles, align4h, base_ok, use_4h, use_nt)),
        ("B 只4h", _combine(flips, candles, align4h, {}, use_4h, False)),
        (WNAME,
         _combine_mom(flips, candles, align4h, nt, mom_at, vol_at, WAIVE_X,
                      use_4h, use_nt)),
    ]

    segs_q, segs_m = {}, {}
    for c in candles:
        segs_q.setdefault(_quarter_key(c["ts"]), []).append(c)
        segs_m.setdefault(_month_key(c["ts"]), []).append(c)

    def run_seg(segs):
        res = {}
        for name, al in cases:
            res[name] = {k: pnl_of(run(v, al)) for k, v in segs.items()}
        return res

    rq = run_seg(segs_q)
    print(f"\n--- 分季度 pnl(U) ---", flush=True)
    names = [c[0] for c in cases]
    hdr = f"{'季度':<10}" + "".join(f"{n:>16}" for n in names) + f"{'W-A':>9}"
    print(hdr)
    print("-" * len(hdr))
    for k in sorted(segs_q):
        vals = [rq[n][k] for n in names]
        print(f"{k:<10}" + "".join(f"{v:>16.1f}" for v in vals)
              + f"{vals[2]-vals[0]:>+9.1f}", flush=True)
    tot = [sum(rq[n].values()) for n in names]
    print(f"{'合计':<10}" + "".join(f"{v:>16.1f}" for v in tot)
          + f"{tot[2]-tot[0]:>+9.1f}", flush=True)

    rm = run_seg(segs_m)
    print(f"\n--- 分月 pnl(U) ---", flush=True)
    hdr2 = f"{'月':<10}" + "".join(f"{n:>16}" for n in names) + f"{'W-A':>9}"
    print(hdr2)
    print("-" * len(hdr2))
    for k in sorted(segs_m):
        vals = [rm[n][k] for n in names]
        print(f"{k:<10}" + "".join(f"{v:>16.1f}" for v in vals)
              + f"{vals[2]-vals[0]:>+9.1f}", flush=True)

    print(f"\n--- 全段 ---", flush=True)
    hdr3 = (f"{'方案':<16}{'pnlU':>8}{'收益%':>8}{'dd%':>7}{'笔':>5}{'wr%':>6}"
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
        print(f"{name:<16}{s['pnl_u']:>8}{s['ret_pct']:>8}{s['dd_pct']:>7}"
              f"{s['trades']:>5}{s['win_rate']:>6}{s['pf']:>6}{s['tp1']:>5}"
              f"{s['stops']:>5}{s['score']:>7}", flush=True)

    print(f"\n  同期 BTC 买入持有 {full['A 线上']['hold_pct']}% → "
          f"W alpha = {full[WNAME]['ret_pct']-full['A 线上']['hold_pct']:+.2f}%"
          f"（A alpha {full['A 线上']['ret_pct']-full['A 线上']['hold_pct']:+.2f}%）", flush=True)

    out = {"symbol": SYMBOL, "tf": gate_tf, "year": 2025, "waive": [WAIVE_N, WAIVE_X],
           "quarterly": rq, "monthly": rm, "full": full}
    path = os.path.join(os.path.dirname(__file__), "_notrend_waive_2025.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {path}", flush=True)


if __name__ == "__main__":
    main()
