"""无趋势闸门审计：no_trend 是否把真正的趋势启动也拦掉了？

判定口径（pattern_trade._allow_by_no_trend）：
    ADX(14) < no_trend_adx   且   |EMA20-EMA60|/EMA60*100 < no_trend_ma_gap
    两条【同时】成立 → 无趋势 → 拦开新仓

ADX 与均线间距都是滞后指标，趋势启动初期二者都还没张开，
理论上是「最容易被误判为无趋势」的时刻。本脚本实测验证。

输出三部分：
  1. 信号质量：被拦组 vs 放行组的前瞻 MFE / MAE / 终点收益分布
  2. 滞后性证据：被拦信号在信号后 12/24/48 根的 ADX 与均线间距走势
  3. A/B 回测：闸门全开 / 只 4h / 只无趋势 / 全关 的实际结果对比

用法：
  python _notrend_audit_2026.py
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
from indicators import ma, super_trend, ta_adx
from pattern_recog import recognize as recognize_pattern
from position import ExitRules
from _live_cfg_backtest import fetch_candles, ts_fmt

CFG_URL = "http://43.108.10.84:5174/api/pattern/trade/config"
SYMBOL = "BTC-USDT-SWAP"
ST_PERIODS, ST_MULT = 10, 3.0
NT_ADX_LEN = 14
NT_FAST, NT_SLOW, NT_MA_TYPE = 20, 60, "EMA"
INIT_CASH = 1000.0

START_2026 = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
BARS_1H, BARS_4H = 7300, 1900

# 前瞻窗口（根数，1h 即小时）
FORWARD = [12, 24, 48, 72]
# 「走出行情」档位：MFE_48 达到这些百分比算数
MFE_TIERS = [1.0, 2.0, 3.0, 5.0]


def _get(url: str, timeout: int = 25):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "supertrend-bt/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _slice_from(arr, ts):
    return [c for c in arr if c["ts"] >= ts]


def _ind(candles):
    """全量算 ADX / EMA，返回按 ts 索引的 dict。"""
    h = [c["h"] for c in candles]
    l = [c["l"] for c in candles]
    cl = [c["c"] for c in candles]
    adx = ta_adx(h, l, cl, NT_ADX_LEN)
    m20 = ma(cl, NT_FAST, NT_MA_TYPE)
    m60 = ma(cl, NT_SLOW, NT_MA_TYPE)
    return ({c["ts"]: adx[i] for i, c in enumerate(candles)},
            {c["ts"]: m20[i] for i, c in enumerate(candles)},
            {c["ts"]: m60[i] for i, c in enumerate(candles)})


def _notrend_map(candles, adx_th, gap_th):
    a, f, s = _ind(candles)
    out = {}
    diag = {}
    for c in candles:
        t = c["ts"]
        av, fv, sv = a[t], f[t], s[t]
        if av is None or fv is None or sv is None or not sv:
            out[t] = False
            diag[t] = (None, None, False, False)
            continue
        gap = abs(fv - sv) / sv * 100.0
        c_adx, c_gap = av < adx_th, gap < gap_th
        out[t] = c_adx and c_gap
        diag[t] = (av, gap, c_adx, c_gap)
    return out, diag


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


def _combine(flips, candles, align4h, ok_at, use_4h, use_nt):
    out = {}
    for f in flips:
        i = f["i"]
        if i >= len(candles):
            continue
        ts = candles[i]["ts"]
        sd = 1 if f["type"] == "buy" else -1
        ok4 = (align4h.get(ts) == sd) if use_4h else True
        okn = bool(ok_at.get(ts, True)) if use_nt else True
        out[ts] = sd if (ok4 and okn) else -sd
    return out


def _fwd(candles, i, d, n):
    """前瞻 n 根：MFE / MAE / 终点收益（%）。不做路径判定，纯区间极值。"""
    if i + n >= len(candles):
        return None
    entry = candles[i]["c"]
    seg = candles[i + 1:i + 1 + n]
    hi = max(c["h"] for c in seg)
    lo = min(c["l"] for c in seg)
    if d == 1:
        mfe, mae = hi / entry - 1, 1 - lo / entry
        ret = (seg[-1]["c"] / entry - 1)
    else:
        mfe, mae = 1 - lo / entry, hi / entry - 1
        ret = (1 - seg[-1]["c"] / entry)
    return mfe * 100, mae * 100, ret * 100


def _stats(vals):
    if not vals:
        return {"n": 0}
    v = sorted(vals)
    n = len(v)
    return {
        "n": n,
        "mean": round(sum(v) / n, 2),
        "median": round(v[n // 2], 2),
        "p25": round(v[int(n * 0.25)], 2),
        "p75": round(v[int(n * 0.75)], 2),
        "min": round(v[0], 2), "max": round(v[-1], 2),
    }


def main():
    live = _get(CFG_URL)
    cfg, sym = live["cfg"], next(
        s for s in live["symbols"] if s["symbol"] == SYMBOL)
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

    print(f"=== 无趋势闸门审计 · {SYMBOL} · 2026 · {gate_tf} ===", flush=True)
    print(f"  判定 ADX(14)<{adx_th} 且 EMA20/60 间距<{gap_th}%  "
          f"(block_4h={use_4h}, no_trend_block={use_nt})", flush=True)

    t0 = time.time()
    raw1 = fetch_candles(SYMBOL, gate_tf, BARS_1H)
    c4 = fetch_candles(SYMBOL, "4h", BARS_4H) if use_4h else []
    print(f"  fetch 1h={len(raw1)} 4h={len(c4)} ({time.time()-t0:.0f}s)", flush=True)

    candles = _slice_from(raw1, START_2026)
    if len(candles) < 300:
        print("  2026 K 线不足", flush=True)
        return

    # 在 raw1 上算指标（含 2025 预热），再切片 → 与实盘取 500 根预热一致
    nt_raw, diag_raw = _notrend_map(raw1, adx_th, gap_th)
    nt = {c["ts"]: nt_raw[c["ts"]] for c in candles}
    diag = {c["ts"]: diag_raw[c["ts"]] for c in candles}

    st1 = super_trend([c["o"] for c in raw1], [c["h"] for c in raw1],
                      [c["l"] for c in raw1], [c["c"] for c in raw1],
                      periods=ST_PERIODS, multiplier=ST_MULT, change_atr=True)
    base = len(raw1) - len(candles)
    flips = [{"i": f["i"] - base, "type": f["type"]}
             for f in (st1.get("flips") or []) if f["i"] >= base]

    align4h = _block4h_align(candles, flips, c4) if use_4h else {}
    align_on = _combine(flips, candles, align4h,
                        {t: not v for t, v in nt.items()}, use_4h, use_nt)
    align_4h_only = _combine(flips, candles, align4h, {}, use_4h, False)

    # ── 分组 ────────────────────────────────────────────
    blocked, passed = [], []
    for f in flips:
        i = f["i"]
        ts = candles[i]["ts"]
        d = 1 if f["type"] == "buy" else -1
        # 只统计「无趋势闸门」造成的拦截：4h 已拦的不算
        ok4 = (align4h.get(ts) == d) if use_4h else True
        if not ok4:
            continue
        rec = {"i": i, "ts": ts, "dir": d, "price": candles[i]["c"]}
        rec.update({f"mfe{n}": (_fwd(candles, i, d, n) or (None,) * 3)[0]
                    for n in FORWARD})
        rec.update({f"mae{n}": (_fwd(candles, i, d, n) or (None,) * 3)[1]
                    for n in FORWARD})
        rec.update({f"ret{n}": (_fwd(candles, i, d, n) or (None,) * 3)[2]
                    for n in FORWARD})
        (blocked if nt.get(ts) else passed).append(rec)

    n_flip = len(flips)
    n_blocked_all = sum(1 for f in flips
                        if align_on.get(candles[f["i"]]["ts"], 0)
                        == -(1 if f["type"] == "buy" else -1))
    bar_rate = sum(1 for v in nt.values() if v) / len(nt) * 100
    print(f"\n--- 闸门覆盖 ---", flush=True)
    print(f"  ST 翻转 {n_flip} 个 | 4h 拦 {n_flip-len(blocked)-len(passed)} | "
          f"无趋势拦 {len(blocked)} | 放行 {len(passed)}", flush=True)
    print(f"  闸门共拦 {n_blocked_all} 个 ({n_blocked_all/max(1,n_flip)*100:.0f}%)",
          flush=True)
    print(f"  时间占比：{len(nt)} 根中判无趋势 {sum(1 for v in nt.values() if v)} 根 "
          f"({bar_rate:.1f}%)", flush=True)

    # ── 1. 信号质量对比 ─────────────────────────────────
    print(f"\n--- 1. 前瞻质量：被拦 vs 放行（单位 %，正数=对信号方向有利）---",
          flush=True)
    hdr = (f"{'窗口':<8}{'组':<8}{'笔数':>5}{'MFE均':>8}{'MFE中位':>9}"
           f"{'MAE均':>8}{'终点均':>8}{'终点中位':>9}")
    print(hdr)
    print("-" * len(hdr))
    grp = {"被拦": blocked, "放行": passed}
    q = {}
    for n in FORWARD:
        for gname, g in grp.items():
            m = [r[f"mfe{n}"] for r in g if r[f"mfe{n}"] is not None]
            a = [r[f"mae{n}"] for r in g if r[f"mae{n}"] is not None]
            e = [r[f"ret{n}"] for r in g if r[f"ret{n}"] is not None]
            sm, sa, se = _stats(m), _stats(a), _stats(e)
            q[(n, gname)] = {"mfe": sm, "mae": sa, "ret": se}
            print(f"{str(n)+'h':<8}{gname:<8}{sm.get('n',0):>5}"
                  f"{sm.get('mean','-'):>8}{sm.get('median','-'):>9}"
                  f"{sa.get('mean','-'):>8}{se.get('mean','-'):>8}"
                  f"{se.get('median','-'):>9}", flush=True)

    # ── 2. 错杀大行情比例 ───────────────────────────────
    print(f"\n--- 2. 「走出行情」比例：48h 内 MFE 达到档位 ---", flush=True)
    print(f"{'档位':<10}{'被拦(占比)':>16}{'放行(占比)':>16}{'判定':>10}")
    tier_rows = {}
    for t in MFE_TIERS:
        b = [r for r in blocked if r["mfe48"] is not None]
        p = [r for r in passed if r["mfe48"] is not None]
        rb = sum(1 for r in b if r["mfe48"] >= t) / max(1, len(b)) * 100
        rp = sum(1 for r in p if r["mfe48"] >= t) / max(1, len(p)) * 100
        verdict = "错杀≥放行" if rb >= rp - 2 else "有效过滤"
        tier_rows[t] = {"blocked_pct": round(rb, 1), "passed_pct": round(rp, 1),
                        "n_blocked": len(b), "n_passed": len(p), "verdict": verdict}
        print(f"MFE≥{t}%{'':<{max(0,5-len(str(t)))}}{rb:>13.1f}%({len(b)})"
              f"{rp:>13.1f}%({len(p)}){verdict:>12}", flush=True)

    # ── 3. 滞后性证据 ───────────────────────────────────
    print(f"\n--- 3. 滞后性：被拦信号之后 ADX / 均线间距怎么走 ---", flush=True)
    idx_by_ts = {c["ts"]: i for i, c in enumerate(candles)}
    lag_rows = []
    for off in [0, 12, 24, 48]:
        ads, gps = [], []
        for r in blocked:
            j = r["i"] + off
            if j >= len(candles):
                continue
            d = diag.get(candles[j]["ts"])
            if d and d[0] is not None:
                ads.append(d[0])
                gps.append(d[1])
        if ads:
            sa, sg = _stats(ads), _stats(gps)
            lag_rows.append({"offset_h": off, "adx_mean": sa["mean"],
                             "adx_median": sa["median"],
                             "gap_mean": sg["mean"], "gap_median": sg["median"],
                             "n": sa["n"]})
            print(f"  信号后 {off:>2}h: ADX 均 {sa['mean']:>5.1f} "
                  f"(中位 {sa['median']:>5.1f}) | 间距均 {sg['mean']:>5.2f}% "
                  f"(中位 {sg['median']:>5.2f}%) | n={sa['n']}", flush=True)
    if lag_rows:
        rise = lag_rows[-1]["adx_mean"] - lag_rows[0]["adx_mean"]
        print(f"  → ADX 从 {lag_rows[0]['adx_mean']:.1f} 涨到 "
              f"{lag_rows[-1]['adx_mean']:.1f}（+{rise:.1f}），"
              f"说明拦截时刻正处于趋势「尚未展开」阶段", flush=True)

    # ── 4. A/B 回测 ─────────────────────────────────────
    print(f"\n--- 4. A/B 回测（同一 TP1/SL，只换闸门）---", flush=True)
    rules = ExitRules(tp1_pct=tp1_pct, tp1_ratio=tp1_ratio, sl_mode="st",
                      sl_pct=sl_pct,
                      move_sl_to_entry=_v(sym["move_sl_to_entry"],
                                          cfg["move_sl_to_entry"]),
                      trail_with_st=_v(sym["trail_with_st"], cfg["trail_with_st"]))
    p = {"periods": ST_PERIODS, "multiplier": ST_MULT,
         "src": "hl2", "change_atr": True}

    def run(align):
        return run_backtest(candles, p, init_cash=INIT_CASH, fee_rate=0.0005,
                            allow_short=True, exit_rules=rules, sizing="fixed",
                            margin_usdt=margin, leverage=lev, trend_align=align)

    def summ(r):
        pnl = round(r["final"] - r["init_cash"], 2)
        dd = r["max_dd_pct"]
        return {"pnl_u": pnl, "ret_pct": round(pnl / INIT_CASH * 100, 2),
                "dd_pct": dd, "trades": r["trades"], "win_rate": r["win_rate"],
                "pf": r["profit_factor"], "tp1": r["tp1_count"],
                "stops": r["stop_count"], "rev": r["reverse_count"],
                "score": round(pnl / notional * 100 / dd, 3) if dd > 0 else 0}

    align_none = _combine(flips, candles, {}, {}, False, False)
    ab = {}
    cases = [
        ("A 线上(4h+无趋势)", align_on),
        ("B 只 4h（关无趋势）", align_4h_only),
        ("C 只无趋势（关4h）", _combine(flips, candles, {},
                                    {t: not v for t, v in nt.items()},
                                    False, use_nt)),
        ("D 全关（裸 ST）", align_none),
    ]
    hdr2 = (f"{'方案':<22}{'pnlU':>8}{'收益%':>8}{'dd%':>7}{'笔':>5}{'wr%':>6}"
            f"{'PF':>6}{'TP1':>5}{'止损':>5}{'评分':>7}")
    print(hdr2)
    print("-" * len(hdr2))
    for name, al in cases:
        r = run(al)
        if "error" in r:
            print(f"  {name}: error {r['error']}", flush=True)
            continue
        s = summ(r)
        ab[name] = s
        print(f"{name:<22}{s['pnl_u']:>8}{s['ret_pct']:>8}{s['dd_pct']:>7}"
              f"{s['trades']:>5}{s['win_rate']:>6}{s['pf']:>6}"
              f"{s['tp1']:>5}{s['stops']:>5}{s['score']:>7}", flush=True)

    if "A 线上(4h+无趋势)" in ab and "B 只 4h（关无趋势）" in ab:
        a, b = ab["A 线上(4h+无趋势)"], ab["B 只 4h（关无趋势）"]
        print(f"\n  → 关掉无趋势闸门：pnl {a['pnl_u']}U → {b['pnl_u']}U "
              f"(差 {b['pnl_u']-a['pnl_u']:+.2f}U)，交易 {a['trades']} → "
              f"{b['trades']} 笔，回撤 {a['dd_pct']}% → {b['dd_pct']}%",
              flush=True)

    # ── 5. 错杀 TOP 案例 ────────────────────────────────
    print(f"\n--- 5. 被拦信号中 MFE_48 最大的 10 个（疑似错杀）---", flush=True)
    top = sorted([r for r in blocked if r["mfe48"] is not None],
                 key=lambda r: -r["mfe48"])[:10]
    print(f"{'时间':<18}{'方向':<6}{'价格':>10}{'MFE12%':>8}{'MFE24%':>8}"
          f"{'MFE48%':>8}{'MAE48%':>8}{'终点48%':>9}")
    for r in top:
        print(f"{ts_fmt(r['ts']):<18}{'多' if r['dir']==1 else '空':<6}"
              f"{r['price']:>10.1f}{r['mfe12'] or 0:>8.2f}{r['mfe24'] or 0:>8.2f}"
              f"{r['mfe48'] or 0:>8.2f}{r['mae48'] or 0:>8.2f}"
              f"{r['ret48'] or 0:>9.2f}", flush=True)

    out = {
        "symbol": SYMBOL, "tf": gate_tf,
        "range": {"start": ts_fmt(candles[0]["ts"]),
                  "end": ts_fmt(candles[-1]["ts"]), "bars": len(candles)},
        "gate": {"adx_th": adx_th, "gap_th": gap_th,
                 "block_4h": use_4h, "no_trend_block": use_nt},
        "coverage": {"flips": n_flip, "blocked_4h": n_flip - len(blocked) - len(passed),
                     "blocked_notrend": len(blocked), "passed": len(passed),
                     "blocked_total": n_blocked_all,
                     "bar_notrend_rate_pct": round(bar_rate, 2)},
        "quality": {f"{n}h_{g}": q[(n, g)] for n in FORWARD
                    for g in ("被拦", "放行")},
        "tiers": tier_rows,
        "lag": lag_rows,
        "ab": ab,
        "killed_top": [{k: (round(v, 2) if isinstance(v, float) else v)
                        for k, v in r.items() if k != "i"} for r in top],
    }
    path = os.path.join(os.path.dirname(__file__), "_notrend_audit_2026.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {path}", flush=True)


if __name__ == "__main__":
    main()
