"""启动豁免参数扫描：mom(N) >= X 网格 + 稳健性检验。

_notrend_waive_2026 已证明「启动豁免」有效：
    线上基线 183.29U / dd 14.38%  →  mom12>=1.2  234.62U / dd 11.16%
本脚本扫完整网格找最优，并做前后半段 + 分季度稳健性检验，排除过拟合。

豁免量定义（只用 i 及之前 K 线，无未来函数）：
    mom(N) = (close[i] / close[i-N] - 1) × 信号方向 × 100
  即「最近 N 根朝信号方向的累计涨幅」。

用法：
  python _notrend_waive_scan.py
"""
from __future__ import annotations

import json
import os
import sys
import time
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

MOM_NS = [6, 8, 12, 16, 24]
MOM_XS = [0.4, 0.6, 0.8, 1.0, 1.2, 1.5, 2.0, 2.5]


def _combine_mom(flips, candles, align4h, nt, mom_at, x, use_4h, use_nt):
    """放行 = 未被判无趋势  或  mom(N) >= x。"""
    out = {}
    for f in flips:
        i = f["i"]
        if i >= len(candles):
            continue
        ts = candles[i]["ts"]
        sd = 1 if f["type"] == "buy" else -1
        ok4 = (align4h.get(ts) == sd) if use_4h else True
        m = mom_at.get(ts)
        waived = (m is not None and m >= x)
        okn = ((not nt.get(ts, False)) or waived) if use_nt else True
        out[ts] = sd if (ok4 and okn) else -sd
    return out


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

    print(f"=== 启动豁免扫描 · {SYMBOL} · 2026 · {gate_tf} ===", flush=True)
    print(f"  闸门 ADX(14)<{adx_th} 且 间距<{gap_th}% | "
          f"TP1 {tp1_pct}%/{tp1_ratio}% SL {sl_pct}%", flush=True)

    t0 = time.time()
    raw1 = fetch_candles(SYMBOL, gate_tf, BARS_1H)
    c4 = fetch_candles(SYMBOL, "4h", BARS_4H) if use_4h else []
    print(f"  fetch 1h={len(raw1)} 4h={len(c4)} ({time.time()-t0:.0f}s)", flush=True)

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

    # 预计算 mom(N)
    mom_maps = {}
    for n in MOM_NS:
        m = {}
        for i, c in enumerate(candles):
            m[c["ts"]] = (c["c"] / candles[i - n]["c"] - 1) * 100 if i >= n else None
        mom_maps[n] = m
    # 方向修正：mom 需乘信号方向，在这里按 flip 逐个存
    mom_dir = {n: {} for n in MOM_NS}
    for f in flips:
        i = f["i"]
        if i >= len(candles):
            continue
        ts = candles[i]["ts"]
        d = 1 if f["type"] == "buy" else -1
        for n in MOM_NS:
            v = mom_maps[n].get(ts)
            mom_dir[n][ts] = v * d if v is not None else None

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

    def summ(r):
        pnl = round(r["final"] - r["init_cash"], 2)
        dd = r["max_dd_pct"]
        return {"pnl_u": pnl, "ret_pct": round(pnl / INIT_CASH * 100, 2),
                "dd_pct": dd, "trades": r["trades"], "win_rate": r["win_rate"],
                "pf": r["profit_factor"], "score": round(
                    pnl / notional * 100 / dd, 3) if dd > 0 else 0}

    # ── 基线 ────────────────────────────────────────────
    base_ok = {t: not v for t, v in nt.items()}
    align_base = _combine(flips, candles, align4h, base_ok, use_4h, use_nt)
    align_4h = _combine(flips, candles, align4h, {}, use_4h, False)
    sb = summ(run(candles, align_base))
    s4 = summ(run(candles, align_4h))
    print(f"\n  基线 A 线上(4h+无趋势) = {sb['pnl_u']}U dd {sb['dd_pct']}% "
          f"评分 {sb['score']}", flush=True)
    print(f"  基线 B 只4h(关无趋势)  = {s4['pnl_u']}U dd {s4['dd_pct']}% "
          f"评分 {s4['score']}", flush=True)

    # ── 网格 ────────────────────────────────────────────
    grid = []
    total = len(MOM_NS) * len(MOM_XS)
    print(f"\n--- 网格 {total} 组（mom(N) >= X）---", flush=True)
    t0 = time.time()
    k = 0
    for n in MOM_NS:
        for x in MOM_XS:
            k += 1
            al = _combine_mom(flips, candles, align4h, nt, mom_dir[n], x,
                              use_4h, use_nt)
            s = summ(run(candles, al))
            s["n"], s["x"] = n, x
            grid.append(s)
            if k % 10 == 0:
                print(f"  {k}/{total} … {time.time()-t0:.0f}s", flush=True)

    hdr = (f"{'mom':<12}{'pnlU':>8}{'收益%':>8}{'dd%':>7}{'笔':>5}{'wr%':>6}"
           f"{'PF':>6}{'评分':>7}{'vsA':>8}")
    print("\n=== 按评分排序 top15 ===")
    print(hdr)
    print("-" * len(hdr))
    grid.sort(key=lambda s: -s["score"])
    for s in grid[:15]:
        print(f"mom{s['n']}>={s['x']:<8}{s['pnl_u']:>8}{s['ret_pct']:>8}"
              f"{s['dd_pct']:>7}{s['trades']:>5}{s['win_rate']:>6}{s['pf']:>6}"
              f"{s['score']:>7}{s['pnl_u']-sb['pnl_u']:>+8.1f}", flush=True)

    # ── 稳健性：top6 做前后半段 + 季度 ──────────────────
    print("\n=== top6 稳健性（前后半段 + 分季度）===", flush=True)
    mid = len(candles) // 2
    segs = [("H1", candles[:mid]), ("H2", candles[mid:])]
    qs = []
    for qi in range(4):
        a0 = len(candles) * qi // 4
        a1 = len(candles) * (qi + 1) // 4
        if a1 - a0 > 300:
            qs.append((f"Q{qi+1}", candles[a0:a1]))
    robust = []
    for s in grid[:6]:
        al = _combine_mom(flips, candles, align4h, nt, mom_dir[s["n"]], s["x"],
                          use_4h, use_nt)
        row = {"n": s["n"], "x": s["x"], "full": s["pnl_u"], "dd": s["dd_pct"],
               "score": s["score"], "segs": {}}
        ok = True
        for nm, cd in segs + qs:
            r = summ(run(cd, al))
            row["segs"][nm] = r["pnl_u"]
            if nm.startswith("Q") and r["pnl_u"] <= 0:
                ok = False
        row["all_q_ok"] = ok
        robust.append(row)
        seg_txt = " ".join(f"{nm}={v:+.0f}U" for nm, v in row["segs"].items())
        print(f"  mom{s['n']}>={s['x']}: full={s['pnl_u']}U dd={s['dd_pct']}% "
              f"评分{s['score']} | {seg_txt} {'OK' if ok else 'X'}", flush=True)

    good = [r for r in robust if r["all_q_ok"]]
    rec = good[0] if good else robust[0]

    out = {
        "symbol": SYMBOL, "tf": gate_tf,
        "range": {"start": ts_fmt(candles[0]["ts"]),
                  "end": ts_fmt(candles[-1]["ts"]),
                  "bars": len(candles)},
        "live": {"tp1_pct": tp1_pct, "tp1_ratio": tp1_ratio, "sl_pct": sl_pct,
                 "adx_th": adx_th, "gap_th": gap_th, "margin": margin,
                 "leverage": lev, "notional": notional},
        "baseline": {"A_线上": sb, "B_只4h": s4},
        "grid": grid,
        "robust": robust,
        "recommend": rec,
    }
    path = os.path.join(os.path.dirname(__file__), "_notrend_waive_scan.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {path}", flush=True)
    print(f"\n=== RECOMMEND: mom{rec['n']} >= {rec['x']} ===", flush=True)
    print(f"  pnl {sb['pnl_u']}U → {rec['full']}U "
          f"({rec['full']-sb['pnl_u']:+.2f}U) | dd {sb['dd_pct']}% → {rec['dd']}%"
          f" | 评分 {sb['score']} → {rec['score']}", flush=True)


if __name__ == "__main__":
    main()
