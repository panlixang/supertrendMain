"""无趋势闸门「启动豁免」测试。

问题：ADX / 均线间距都是滞后指标，趋势刚启动（尚未展开）时二者都还没张开，
      于是最干净的单边启动被判成「无趋势」拦掉（见 _notrend_audit_2026）。

方案：保留 no_trend 判定，但增加「启动豁免」——
      若信号当下已出现明确的启动迹象（不滞后的量），即使 ADX/间距未达标也放行。

豁免特征（全部只用 i 及之前的 K 线，无未来函数）：
  consN  最近 N 根中收盘方向(c-o)与信号同向的根数
  momN   最近 N 根累计收益 × 信号方向 (%)
  atrr   ATR(7) / ATR(28) 波动扩张率
  brkN   收盘价突破前 N 根极值（多=破前高 / 空=破前低）

流程：
  1. 打印被拦信号的特征表（按 MFE_48 降序）→ 看大行情的共同特征
  2. 候选豁免规则的 recall（捞回几笔大行情）/ precision（捞回的成色）
  3. 对入围规则跑 A/B 回测，与线上基线对比

用法：
  python _notrend_waive_2026.py
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

BIG_MFE = 5.0      # MFE_48 ≥ 此值定义为「大行情」
GOOD_MFE = 2.0     # MFE_48 ≥ 此值算「成色达标」


def _get(url: str, timeout: int = 25):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "supertrend-bt/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _slice_from(arr, ts):
    return [c for c in arr if c["ts"] >= ts]


def _atr(candles, n):
    """简单 ATR(n)：TR 的 SMA，前 n-1 根为 None。"""
    tr = []
    for i, c in enumerate(candles):
        if i == 0:
            tr.append(c["h"] - c["l"])
        else:
            pc = candles[i - 1]["c"]
            tr.append(max(c["h"] - c["l"], abs(c["h"] - pc), abs(c["l"] - pc)))
    out = [None] * len(candles)
    s = 0.0
    for i, v in enumerate(tr):
        s += v
        if i >= n:
            s -= tr[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def _notrend_map(candles, adx_th, gap_th):
    h = [c["h"] for c in candles]
    l = [c["l"] for c in candles]
    cl = [c["c"] for c in candles]
    adx = ta_adx(h, l, cl, NT_ADX_LEN)
    m20 = ma(cl, NT_FAST, NT_MA_TYPE)
    m60 = ma(cl, NT_SLOW, NT_MA_TYPE)
    out = {}
    for i, c in enumerate(candles):
        a, f, s = adx[i], m20[i], m60[i]
        if a is None or f is None or s is None or not s:
            out[c["ts"]] = False
            continue
        out[c["ts"]] = (a < adx_th) and (abs(f - s) / s * 100.0 < gap_th)
    return out


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
        out[ts] = sd if ((pdir is None) or (pdir == 0) or (pdir == sd)) else -sd
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
    if i + n >= len(candles):
        return None, None, None
    entry = candles[i]["c"]
    seg = candles[i + 1:i + 1 + n]
    hi = max(c["h"] for c in seg)
    lo = min(c["l"] for c in seg)
    if d == 1:
        mfe, mae = hi / entry - 1, 1 - lo / entry
        ret = seg[-1]["c"] / entry - 1
    else:
        mfe, mae = 1 - lo / entry, hi / entry - 1
        ret = 1 - seg[-1]["c"] / entry
    return mfe * 100, mae * 100, ret * 100


def _feats(candles, i, d, atr7, atr28):
    """只用 i 及之前的 K 线。返回启动特征 dict。"""
    f = {}
    for n in (6, 8, 12):
        if i - n < 0:
            f[f"cons{n}"] = None
        else:
            f[f"cons{n}"] = sum(
                1 for c in candles[i - n + 1:i + 1]
                if (c["c"] - c["o"]) * d > 0)
    for n in (6, 8, 12):
        if i - n < 0:
            f[f"mom{n}"] = None
        else:
            f[f"mom{n}"] = (candles[i]["c"] / candles[i - n]["c"] - 1) * d * 100
    a7, a28 = atr7[i], atr28[i]
    f["atr7"], f["atr28"] = a7, a28
    f["atrr"] = (a7 / a28) if (a7 and a28) else None
    for n in (12, 24, 48):
        if i - n < 0:
            f[f"brk{n}"] = None
        else:
            prev = candles[i - n:i]
            if d == 1:
                f[f"brk{n}"] = candles[i]["c"] > max(c["h"] for c in prev)
            else:
                f[f"brk{n}"] = candles[i]["c"] < min(c["l"] for c in prev)
    return f


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

    print(f"=== 启动豁免测试 · {SYMBOL} · 2026 · {gate_tf} ===", flush=True)
    print(f"  基础闸门 ADX(14)<{adx_th} 且 EMA20/60间距<{gap_th}%", flush=True)

    t0 = time.time()
    raw1 = fetch_candles(SYMBOL, gate_tf, BARS_1H)
    c4 = fetch_candles(SYMBOL, "4h", BARS_4H) if use_4h else []
    print(f"  fetch 1h={len(raw1)} 4h={len(c4)} ({time.time()-t0:.0f}s)", flush=True)

    candles = _slice_from(raw1, START_2026)
    nt_raw = _notrend_map(raw1, adx_th, gap_th)
    nt = {c["ts"]: nt_raw[c["ts"]] for c in candles}
    atr7 = _atr(raw1, 7)
    atr28 = _atr(raw1, 28)
    b = len(raw1) - len(candles)
    atr7 = atr7[b:]
    atr28 = atr28[b:]

    st1 = super_trend([c["o"] for c in raw1], [c["h"] for c in raw1],
                      [c["l"] for c in raw1], [c["c"] for c in raw1],
                      periods=ST_PERIODS, multiplier=ST_MULT, change_atr=True)
    flips = [{"i": f["i"] - b, "type": f["type"]}
             for f in (st1.get("flips") or []) if f["i"] >= b]
    align4h = _block4h_align(candles, flips, c4) if use_4h else {}

    # ── 被拦信号 + 特征 ─────────────────────────────────
    blocked = []
    for f in flips:
        i = f["i"]
        ts = candles[i]["ts"]
        d = 1 if f["type"] == "buy" else -1
        if use_4h and align4h.get(ts) != d:
            continue
        if not nt.get(ts):
            continue
        mfe48, mae48, ret48 = _fwd(candles, i, d, 48)
        if mfe48 is None:
            continue
        r = {"i": i, "ts": ts, "dir": d, "price": candles[i]["c"],
             "mfe48": mfe48, "mae48": mae48, "ret48": ret48}
        r.update(_feats(candles, i, d, atr7, atr28))
        blocked.append(r)

    blocked.sort(key=lambda r: -r["mfe48"])
    big = [r for r in blocked if r["mfe48"] >= BIG_MFE]
    print(f"\n  被 no_trend 拦掉 {len(blocked)} 笔，其中 MFE_48≥{BIG_MFE}% 的"
          f"大行情 {len(big)} 笔", flush=True)

    print(f"\n--- 1. 被拦信号特征表（按 MFE_48 降序；B=大行情）---", flush=True)
    hdr = (f"{'时间':<12}{'向':<4}{'MFE48':>7}{'MAE48':>7}{'终点':>7}"
           f"{'c6':>4}{'c8':>4}{'c12':>5}{'m6%':>6}{'m8%':>6}{'m12%':>7}"
           f"{'ATRr':>6}{'b12':>5}{'b24':>5}{'b48':>5}  B")
    print(hdr)
    print("-" * len(hdr))
    for r in blocked:
        print(f"{ts_fmt(r['ts'])[:10]:<12}{'多' if r['dir']==1 else '空':<4}"
              f"{r['mfe48']:>7.2f}{r['mae48']:>7.2f}{r['ret48']:>7.2f}"
              f"{r['cons6'] if r['cons6'] is not None else '-':>4}"
              f"{r['cons8'] if r['cons8'] is not None else '-':>4}"
              f"{r['cons12'] if r['cons12'] is not None else '-':>5}"
              f"{r['mom6']:>6.2f}{r['mom8']:>6.2f}{r['mom12']:>7.2f}"
              f"{r['atrr']:>6.2f}"
              f"{'Y' if r['brk12'] else '.':>5}"
              f"{'Y' if r['brk24'] else '.':>5}"
              f"{'Y' if r['brk48'] else '.':>5}"
              f"  {'B' if r['mfe48'] >= BIG_MFE else ''}", flush=True)

    if big:
        print(f"\n  大行情均值 vs 其余 —— "
              f"cons8: {sum(r['cons8'] for r in big)/len(big):.1f} vs "
              f"{sum(r['cons8'] for r in blocked if r not in big)/max(1,len(blocked)-len(big)):.1f} | "
              f"mom8: {sum(r['mom8'] for r in big)/len(big):.2f}% vs "
              f"{sum(r['mom8'] for r in blocked if r not in big)/max(1,len(blocked)-len(big)):.2f}% | "
              f"ATRr: {sum(r['atrr'] for r in big)/len(big):.2f} vs "
              f"{sum(r['atrr'] for r in blocked if r not in big)/max(1,len(blocked)-len(big)):.2f}",
              flush=True)

    # ── 候选豁免规则 ────────────────────────────────────
    CANDS = [
        ("cons8>=6", lambda r: (r["cons8"] or 0) >= 6),
        ("cons8>=7", lambda r: (r["cons8"] or 0) >= 7),
        ("cons12>=8", lambda r: (r["cons12"] or 0) >= 8),
        ("cons12>=9", lambda r: (r["cons12"] or 0) >= 9),
        ("mom8>=0.3", lambda r: (r["mom8"] or -9) >= 0.3),
        ("mom8>=0.5", lambda r: (r["mom8"] or -9) >= 0.5),
        ("mom12>=0.8", lambda r: (r["mom12"] or -9) >= 0.8),
        ("mom12>=1.2", lambda r: (r["mom12"] or -9) >= 1.2),
        ("atrr>=1.15", lambda r: (r["atrr"] or 0) >= 1.15),
        ("atrr>=1.30", lambda r: (r["atrr"] or 0) >= 1.30),
        ("brk24", lambda r: bool(r["brk24"])),
        ("brk48", lambda r: bool(r["brk48"])),
        ("cons8>=6&mom8>=0.3", lambda r: (r["cons8"] or 0) >= 6 and (r["mom8"] or -9) >= 0.3),
        ("cons8>=6&brk24", lambda r: (r["cons8"] or 0) >= 6 and bool(r["brk24"])),
        ("cons8>=6|mom8>=0.5", lambda r: (r["cons8"] or 0) >= 6 or (r["mom8"] or -9) >= 0.5),
        ("cons12>=8&mom12>=0.5", lambda r: (r["cons12"] or 0) >= 8 and (r["mom12"] or -9) >= 0.5),
        ("cons8>=6&atrr>=1.1", lambda r: (r["cons8"] or 0) >= 6 and (r["atrr"] or 0) >= 1.1),
        ("brk24&mom8>=0.3", lambda r: bool(r["brk24"]) and (r["mom8"] or -9) >= 0.3),
    ]

    print(f"\n--- 2. 候选豁免规则：捞回大行情的能力 ---", flush=True)
    print(f"{'规则':<24}{'捞回':>5}{'大行情':>8}{'漏掉':>6}{'成色%':>8}"
          f"{'净MFE':>8}{'放行后MFE':>10}")
    cand_rows = []
    for name, fn in CANDS:
        waived = [r for r in blocked if fn(r)]
        got = [r for r in waived if r["mfe48"] >= BIG_MFE]
        missed = len(big) - len(got)
        prec = (sum(1 for r in waived if r["mfe48"] >= GOOD_MFE)
                / max(1, len(waived)) * 100) if waived else 0.0
        # 净 MFE：捞回的 MFE 总和 - 捞回的 MAE 总和（粗略衡量值不值）
        net = sum(r["mfe48"] for r in waived) - sum(r["mae48"] for r in waived)
        avg_mfe = sum(r["mfe48"] for r in waived) / max(1, len(waived))
        cand_rows.append({"rule": name, "waived": len(waived),
                          "got_big": len(got), "missed_big": missed,
                          "precision": round(prec, 1), "net_mfe": round(net, 2),
                          "avg_mfe": round(avg_mfe, 2)})
        print(f"{name:<24}{len(waived):>5}{len(got):>8}{missed:>6}"
              f"{prec:>8.1f}{net:>8.1f}{avg_mfe:>10.2f}", flush=True)

    # ── 回测入围规则 ────────────────────────────────────
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

    base_ok = {t: not v for t, v in nt.items()}
    align_base = _combine(flips, candles, align4h, base_ok, use_4h, use_nt)
    align_4h_only = _combine(flips, candles, align4h, {}, use_4h, False)

    # 入围：捞回 ≥2 笔大行情，或成色 ≥60%
    shortlist = [c for c in cand_rows
                 if (c["got_big"] >= 2 and c["waived"] <= 12)
                 or (c["precision"] >= 60 and c["waived"] >= 2)]
    seen = set()
    picked = []
    for c in sorted(shortlist, key=lambda x: (-x["got_big"], -x["precision"]))[:8]:
        if c["rule"] in seen:
            continue
        seen.add(c["rule"])
        picked.append(c)
    if not picked:
        picked = sorted(cand_rows, key=lambda x: -x["net_mfe"])[:5]

    print(f"\n--- 3. A/B 回测（TP1 {tp1_pct}%/{tp1_ratio}% SL {sl_pct}%，只换闸门）---",
          flush=True)
    hdr2 = (f"{'方案':<26}{'pnlU':>8}{'收益%':>8}{'dd%':>7}{'笔':>5}{'wr%':>6}"
            f"{'PF':>6}{'TP1':>5}{'止损':>5}{'评分':>7}")
    print(hdr2)
    print("-" * len(hdr2))
    ab = {}
    cases = [("A 线上(4h+无趋势)", align_base),
             ("B 只4h(关无趋势)", align_4h_only)]
    fn_by = dict(CANDS)
    for c in picked:
        fn = fn_by[c["rule"]]
        waive_ts = {candles[r["i"]]["ts"] for r in blocked if fn(r)}
        ok = {t: (base_ok.get(t, True) or (t in waive_ts)) for t in nt}
        cases.append((f"W {c['rule']}", _combine(flips, candles, align4h, ok,
                                                 use_4h, use_nt)))
    for name, al in cases:
        r = run(al)
        if "error" in r:
            print(f"  {name}: error {r['error']}", flush=True)
            continue
        s = summ(r)
        ab[name] = s
        print(f"{name:<26}{s['pnl_u']:>8}{s['ret_pct']:>8}{s['dd_pct']:>7}"
              f"{s['trades']:>5}{s['win_rate']:>6}{s['pf']:>6}"
              f"{s['tp1']:>5}{s['stops']:>5}{s['score']:>7}", flush=True)

    a = ab.get("A 线上(4h+无趋势)")
    best = max(((k, v) for k, v in ab.items() if k.startswith("W ")),
               key=lambda kv: kv[1]["score"], default=(None, None))
    if a and best[0]:
        d = best[1]["pnl_u"] - a["pnl_u"]
        print(f"\n  最优豁免 = {best[0]}：pnl {a['pnl_u']}U → {best[1]['pnl_u']}U "
              f"({d:+.2f}U)，回撤 {a['dd_pct']}% → {best[1]['dd_pct']}%，"
              f"评分 {a['score']} → {best[1]['score']}", flush=True)

    out = {
        "symbol": SYMBOL, "tf": gate_tf,
        "range": {"start": ts_fmt(candles[0]["ts"]),
                  "end": ts_fmt(candles[-1]["ts"]), "bars": len(candles)},
        "gate": {"adx_th": adx_th, "gap_th": gap_th},
        "blocked": [{k: (round(v, 2) if isinstance(v, float) else v)
                     for k, v in r.items() if k != "i"} for r in blocked],
        "big_n": len(big),
        "candidates": cand_rows,
        "ab": ab,
        "best": best[0],
    }
    path = os.path.join(os.path.dirname(__file__), "_notrend_waive_2026.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {path}", flush=True)


if __name__ == "__main__":
    main()
