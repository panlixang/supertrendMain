# -*- coding: utf-8 -*-
"""SuperTrend Anti-Chop Lab V1.0.

固定 ST10x3，先跑 A0-A3 / B0-B3 共 8 组入场+过滤组合，不调出场。
数据源说明：Binance 合约 REST 在当前网络被代理阻断，使用 data-api.binance.vision
的 BTCUSDT 现货 K 线；15m/1h 同源，避免跨交易所口径混杂。

用法：
    py -3 backend/antichop_lab.py fetch
    py -3 backend/antichop_lab.py run
    py -3 backend/antichop_lab.py all
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from indicators import super_trend, ta_adx, ta_atr  # noqa: E402

API = "https://data-api.binance.vision/api/v3/klines"
CACHE_DIR = os.path.join(HERE, "_antichop_data")
SYMBOL = "BTCUSDT"
FETCH_START = int(datetime(2020, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
FETCH_END = int(datetime(2026, 11, 29, tzinfo=timezone.utc).timestamp() * 1000)
TF_MS = {"15m": 900_000, "30m": 1_800_000, "1h": 3_600_000}
SEGMENTS = [
    ("dev", 2021, 2024),
    ("val", 2024, 2025),
    ("oos", 2025, 2100),
]

ST_P, ST_M = 10, 3.0
ADX_LEN, ADX_MIN = 14, 20.0
ER_LEN, ER_MIN = 20, 0.25
RISK_PCT = 0.005
STOP_ATR = 1.5
TP_R = 1.5
LEVERAGE_CAP = 3.0
PULLBACK_ATR = 0.25
INIT = 10_000.0
BASE_FEE_BPS, BASE_SLIP_BPS = 4.0, 2.0      # 0.04%/side + 0.02%/side
STRESS_FEE_BPS, STRESS_SLIP_BPS = 8.0, 5.0


def _ms(year: int) -> int:
    return int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def cache_path(tf: str, symbol: str = SYMBOL) -> str:
    return os.path.join(CACHE_DIR, f"{symbol}_{tf}_2020-2026.csv.gz")


def _get_json(url: str, retries: int = 6) -> list:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "antichop-lab/1.0"})
            with urllib.request.urlopen(req, timeout=25) as resp:
                return json.loads(resp.read())
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(min(1.5 * (attempt + 1), 8.0))
    raise RuntimeError(f"fetch failed after {retries} retries: {last}")


def fetch_tf(tf: str, symbol: str = SYMBOL) -> list[dict]:
    """拉取 [FETCH_START, FETCH_END) 的已收盘 K 线，写入缓存。"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    out: dict[int, dict] = {}
    cursor = FETCH_START
    now_ms = int(time.time() * 1000)
    pages = 0
    while cursor < FETCH_END:
        qs = (f"?symbol={symbol}&interval={tf}&startTime={cursor}"
              f"&endTime={FETCH_END - 1}&limit=1000")
        rows = _get_json(API + qs)
        if not rows:
            break
        for row in rows:
            ts = int(row[0])
            close_ms = int(row[6])
            if ts < FETCH_START or ts >= FETCH_END or close_ms > now_ms:
                continue
            out[ts] = {
                "ts": ts,
                "o": float(row[1]),
                "h": float(row[2]),
                "l": float(row[3]),
                "c": float(row[4]),
                "vol": float(row[5]),
            }
        pages += 1
        nxt = int(rows[-1][0]) + TF_MS[tf]
        if nxt <= cursor or len(rows) < 1000:
            break
        cursor = nxt
        time.sleep(0.06)
    candles = [out[k] for k in sorted(out)]
    with gzip.open(cache_path(tf, symbol), "wt", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ts", "o", "h", "l", "c", "vol"])
        for c in candles:
            w.writerow([c["ts"], c["o"], c["h"], c["l"], c["c"], c["vol"]])
    print(f"[fetch] {symbol} {tf}: {len(candles)} bars, {pages} pages -> {cache_path(tf, symbol)}")
    return candles


def load_tf(tf: str, symbol: str = SYMBOL) -> list[dict]:
    path = cache_path(tf, symbol)
    if not os.path.exists(path):
        return fetch_tf(tf, symbol)
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return [{k: float(v) if k != "ts" else int(v) for k, v in r.items()} for r in rows]


def er_series(closes: list[float], length: int = ER_LEN) -> list[float | None]:
    """Kaufman ER，窗口严格为 length 根收盘价，输出与 K 线对齐。"""
    n = len(closes)
    out: list[float | None] = [None] * n
    for i in range(length - 1, n):
        seg = closes[i - length + 1:i + 1]
        net = abs(seg[-1] - seg[0])
        path = sum(abs(seg[k] - seg[k - 1]) for k in range(1, len(seg)))
        out[i] = net / path if path > 0 else 0.0
    return out


def filter_ok(fid: int, i: int, adx: list[float | None], er: list[float | None]) -> bool:
    if fid == 0:
        return True
    a = adx[i] is not None and adx[i] > ADX_MIN
    e = er[i] is not None and er[i] >= ER_MIN
    if fid == 1:
        return a
    if fid == 2:
        return e
    return a and e


def build_candidates(candles, st, atr, adx, er, mode: str, fid: int,
                     stop_atr: float = STOP_ATR,
                     atr_min_ratio: float = 0.0,
                     atr_min_z: float | None = None,
                     atr_min_pct: float | None = None,
                     atr_min_chg: float | None = None) -> list[dict]:
    """生成入场候选。A=翻转收盘入场；B=翻转后趋势内首次回踩入场。

    关键时序：入场在信号/回踩根的收盘确认；止损单在该根收盘之后才挂出，
    因此回测中从下一根 bar 才开始检查止损/止盈。
    """
    side_of = {"buy": 1, "sell": -1}
    out = []
    n = len(candles)
    for flip in st["flips"]:
        i = flip["i"]
        side = side_of[flip["type"]]
        if i >= n or atr[i] is None:
            continue
        entry_i = -1
        if mode == "A":
            if filter_ok(fid, i, adx, er):
                entry_i = i
        else:
            for j in range(i + 1, n):
                if st["trend"][j] != side or atr[j] is None:
                    break
                if not filter_ok(fid, j, adx, er):
                    continue
                line = st["up"][j] if side == 1 else st["dn"][j]
                pull = candles[j]["l"] if side == 1 else candles[j]["h"]
                if line is None:
                    continue
                hit = (pull <= line + PULLBACK_ATR * atr[j]) if side == 1 \
                    else (pull >= line - PULLBACK_ATR * atr[j])
                if hit:
                    entry_i = j
                    break
        if entry_i < 0 or atr[entry_i] is None:
            continue
        if atr_min_ratio > 0:
            if entry_i < 50:
                continue
            base_atr = [x for x in atr[entry_i - 50:entry_i] if x is not None]
            if not base_atr or atr[entry_i] < atr_min_ratio * sum(base_atr) / len(base_atr):
                continue
        if atr_min_z is not None:
            if entry_i < 100:
                continue
            win_atr = [x for x in atr[entry_i - 100:entry_i] if x is not None]
            if not win_atr:
                continue
            mean_a = sum(win_atr) / len(win_atr)
            var_a = sum((x - mean_a) ** 2 for x in win_atr) / len(win_atr)
            sd_a = math.sqrt(var_a)
            z = (atr[entry_i] - mean_a) / sd_a if sd_a > 0 else 0.0
            if z < atr_min_z:
                continue
        if atr_min_pct is not None:
            if entry_i < 100:
                continue
            win_atr = [x for x in atr[entry_i - 100:entry_i] if x is not None]
            if not win_atr:
                continue
            pct = sum(1 for x in win_atr if x <= atr[entry_i]) / len(win_atr)
            if pct < atr_min_pct:
                continue
        if atr_min_chg is not None:
            if entry_i < 10 or not atr[entry_i - 10]:
                continue
            chg = atr[entry_i] / atr[entry_i - 10] - 1
            if chg < atr_min_chg:
                continue
        entry = candles[entry_i]["c"]
        dist = stop_atr * atr[entry_i]
        if dist <= 0:
            continue
        out.append({
            "flip_i": i,
            "entry_i": entry_i,
            "side": side,
            "entry": entry,
            "stop": entry - dist if side == 1 else entry + dist,
            "tp": entry + TP_R * dist if side == 1 else entry - TP_R * dist,
            "atr": atr[entry_i],
        })
    return out


def simulate(candles, candidates, fee_bps: float, slip_bps: float) -> dict:
    """单仓、无加仓；入场收盘确认，止损/止盈从下一根开始；同根先止损后止盈。"""
    fee = fee_bps / 10_000.0
    slip = slip_bps / 10_000.0
    by_entry: dict[int, list[dict]] = {}
    for c in candidates:
        by_entry.setdefault(c["entry_i"], []).append(c)

    pos = None
    realized = 0.0
    equity = [INIT]
    trades = []

    def fee_usd(notional: float) -> float:
        return notional * fee

    def close_pos(i: int, reason: str, raw_px: float) -> None:
        nonlocal pos, realized
        if pos is None:
            return
        side = pos["side"]
        fill = raw_px * (1 - slip) if side == 1 else raw_px * (1 + slip)
        pnl = (fill - pos["entry_fill"]) * pos["qty"] * side
        out_fee = fee_usd(fill * pos["qty"])
        net = pnl - pos["entry_fee"] - out_fee
        realized += net
        trades.append({
            "flip_i": pos["flip_i"], "entry_i": pos["entry_i"], "exit_i": i,
            "flip_ts": candles[pos["flip_i"]]["ts"],
            "entry_ts": candles[pos["entry_i"]]["ts"],
            "exit_ts": candles[i]["ts"],
            "side": "long" if side == 1 else "short",
            "reason": reason, "pnl": net, "gross": pnl,
            "fees": pos["entry_fee"] + out_fee,
            "entry": pos["entry"], "exit": raw_px,
            "bars_since_flip": i - pos["flip_i"],
        })
        pos = None

    for i, bar in enumerate(candles):
        if pos is not None and i > pos["entry_i"]:
            side = pos["side"]
            if side == 1 and bar["l"] <= pos["stop"]:
                close_pos(i, "stop", pos["stop"])
            elif side == -1 and bar["h"] >= pos["stop"]:
                close_pos(i, "stop", pos["stop"])
            elif side == 1 and bar["h"] >= pos["tp"]:
                close_pos(i, "tp", pos["tp"])
            elif side == -1 and bar["l"] <= pos["tp"]:
                close_pos(i, "tp", pos["tp"])

        for cand in by_entry.get(i, []):
            if pos is not None:
                break
            eq = INIT + realized
            risk = eq * RISK_PCT
            dist = abs(cand["entry"] - cand["stop"])
            qty = risk / dist
            cap_qty = eq * LEVERAGE_CAP / cand["entry"]
            qty = min(qty, cap_qty)
            if qty <= 0:
                continue
            side = cand["side"]
            entry_fill = cand["entry"] * (1 + slip if side == 1 else 1 - slip)
            pos = {
                "flip_i": cand["flip_i"], "entry_i": i, "side": side,
                "entry": cand["entry"], "entry_fill": entry_fill,
                "qty": qty, "stop": cand["stop"], "tp": cand["tp"],
                "entry_fee": fee_usd(qty * cand["entry"]),
            }

        float_pnl = 0.0
        if pos is not None:
            float_pnl = (bar["c"] - pos["entry_fill"]) * pos["qty"] * pos["side"]
        equity.append(INIT + realized + float_pnl)

    if pos is not None:
        close_pos(len(candles) - 1, "end", candles[-1]["c"])
        equity[-1] = INIT + realized

    peak = INIT
    max_dd = 0.0
    for eq in equity:
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak if peak > 0 else 0.0)
    return {"trades": trades, "equity": equity, "max_dd_pct": max_dd * 100}


def simulate_st_trail(candles, candidates, st, fee_bps: float, slip_bps: float,
                      cooldown_bars: int = 0) -> dict:
    """A0 入场 + 1.5ATR 初始硬止损 + ST 轨道接管跟踪，取消固定止盈。

    轨道接管规则：多头 st.up 超过初始止损后改跟 st.up；空头 st.dn 低于
    初始止损后改跟 st.dn。同根先判硬止损/跟踪止损，再判 ST 翻转收盘出场。
    入场当根不检查出场，止损单在收盘后生效。
    cooldown_bars>0 时，任何出场后 K 根内禁止新开仓。
    """
    fee = fee_bps / 10_000.0
    slip = slip_bps / 10_000.0
    by_entry: dict[int, list[dict]] = {}
    for c in candidates:
        by_entry.setdefault(c["entry_i"], []).append(c)

    pos = None
    realized = 0.0
    equity = [INIT]
    trades = []
    cooldown_until = -1
    skipped = 0

    def fee_usd(notional: float) -> float:
        return notional * fee

    def close_pos(i: int, reason: str, raw_px: float) -> None:
        nonlocal pos, realized, cooldown_until
        if pos is None:
            return
        side = pos["side"]
        fill = raw_px * (1 - slip) if side == 1 else raw_px * (1 + slip)
        pnl = (fill - pos["entry_fill"]) * pos["qty"] * side
        out_fee = fee_usd(fill * pos["qty"])
        net = pnl - pos["entry_fee"] - out_fee
        realized += net
        trades.append({
            "flip_i": pos["flip_i"], "entry_i": pos["entry_i"], "exit_i": i,
            "flip_ts": candles[pos["flip_i"]]["ts"],
            "entry_ts": candles[pos["entry_i"]]["ts"],
            "exit_ts": candles[i]["ts"],
            "side": "long" if side == 1 else "short",
            "reason": reason, "pnl": net, "gross": pnl,
            "fees": pos["entry_fee"] + out_fee,
            "entry": pos["entry"], "exit": raw_px,
            "bars_since_flip": i - pos["flip_i"],
        })
        pos = None
        cooldown_until = i + cooldown_bars

    for i, bar in enumerate(candles):
        if pos is not None and i > pos["entry_i"]:
            side = pos["side"]
            line = st["up"][i] if side == 1 else st["dn"][i]
            trail = line is not None and (
                (side == 1 and line > pos["init_stop"]) or
                (side == -1 and line < pos["init_stop"])
            )
            eff_stop = line if trail else pos["init_stop"]
            hit = (bar["l"] <= eff_stop) if side == 1 else (bar["h"] >= eff_stop)
            if hit:
                close_pos(i, "st_trail" if trail else "stop", eff_stop)
            if pos is not None and st["trend"][i] not in (None, side):
                close_pos(i, "st_flip", bar["c"])

        for cand in by_entry.get(i, []):
            if pos is not None:
                break
            if cooldown_bars > 0 and i <= cooldown_until:
                skipped += 1
                continue
            eq = INIT + realized
            risk = eq * RISK_PCT
            dist = abs(cand["entry"] - cand["stop"])
            qty = min(risk / dist, eq * LEVERAGE_CAP / cand["entry"])
            if qty <= 0:
                continue
            side = cand["side"]
            entry_fill = cand["entry"] * (1 + slip if side == 1 else 1 - slip)
            pos = {
                "flip_i": cand["flip_i"], "entry_i": i, "side": side,
                "entry": cand["entry"], "entry_fill": entry_fill,
                "qty": qty, "init_stop": cand["stop"],
                "entry_fee": fee_usd(qty * cand["entry"]),
            }

        float_pnl = 0.0
        if pos is not None:
            float_pnl = (bar["c"] - pos["entry_fill"]) * pos["qty"] * pos["side"]
        equity.append(INIT + realized + float_pnl)

    if pos is not None:
        close_pos(len(candles) - 1, "end", candles[-1]["c"])
        equity[-1] = INIT + realized

    peak = INIT
    max_dd = 0.0
    for eq in equity:
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak if peak > 0 else 0.0)
    return {"trades": trades, "equity": equity, "max_dd_pct": max_dd * 100,
            "skipped": skipped}


def max_stop_streak(trades: list[dict]) -> int:
    best = cur = 0
    for t in trades:
        if t["reason"] == "stop":
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def stat_block(trades: list[dict], flips: list[dict], candles: list[dict],
               equity: list[float], seg: tuple[str, int, int]) -> dict:
    name, y0, y1 = seg
    lo, hi = _ms(y0), _ms(y1)
    sub = [t for t in trades if lo <= t["entry_ts"] < hi]
    sub_flips = [f for f in flips if lo <= candles[f["i"]]["ts"] < hi]
    if not sub:
        return {"n": 0, "pnl": 0.0, "ret_pct": 0.0, "pf": 0.0, "winrate": 0.0,
                "max_dd_pct": 0.0, "max_stop_streak": 0, "stops_per_100": 0.0,
                "stop_within_3_per_100_flips": 0.0, "stop_within_5_per_100_flips": 0.0,
                "stop_within_10_per_100_flips": 0.0, "flips": len(sub_flips)}

    gp = sum(t["pnl"] for t in sub if t["pnl"] > 0)
    gl = -sum(t["pnl"] for t in sub if t["pnl"] < 0)
    stops = sum(1 for t in sub if t["reason"] == "stop")
    wins = sum(1 for t in sub if t["pnl"] > 0)

    by_flip = {}
    for t in sub:
        by_flip[t["flip_i"]] = t
    within = {3: 0, 5: 0, 10: 0}
    for f in sub_flips:
        t = by_flip.get(f["i"])
        if t and t["reason"] == "stop":
            for w in within:
                if 1 <= t["exit_i"] - f["i"] <= w:
                    within[w] += 1

    seg_equity = [equity[k] for k in range(1, min(len(equity), len(candles) + 1))
                  if lo <= candles[k - 1]["ts"] < hi]
    if seg_equity:
        peak = seg_equity[0]
        dd = 0.0
        for eq in seg_equity:
            peak = max(peak, eq)
            dd = max(dd, (peak - eq) / peak if peak > 0 else 0.0)
    else:
        dd = 0.0

    denom = len(sub_flips) or 1
    return {
        "n": len(sub),
        "pnl": sum(t["pnl"] for t in sub),
        "ret_pct": sum(t["pnl"] for t in sub) / INIT * 100,
        "pf": gp / gl if gl > 0 else (float("inf") if gp > 0 else 0.0),
        "winrate": wins / len(sub) * 100,
        "max_dd_pct": dd * 100,
        "max_stop_streak": max_stop_streak(sub),
        "stops_per_100": stops / len(sub) * 100,
        "stop_within_3_per_100_flips": within[3] / denom * 100,
        "stop_within_5_per_100_flips": within[5] / denom * 100,
        "stop_within_10_per_100_flips": within[10] / denom * 100,
        "flips": len(sub_flips),
    }


def run_variant(tf: str, candles: list[dict], mode: str, fid: int) -> dict:
    o = [c["o"] for c in candles]
    h = [c["h"] for c in candles]
    l = [c["l"] for c in candles]
    c = [c["c"] for c in candles]
    st = super_trend(o, h, l, c, periods=ST_P, multiplier=ST_M,
                     src="hl2", change_atr=True)
    atr = ta_atr(h, l, c, ST_P)
    adx = ta_adx(h, l, c, ADX_LEN)
    er = er_series(c, ER_LEN)
    candidates = build_candidates(candles, st, atr, adx, er, mode, fid)
    base = simulate(candles, candidates, BASE_FEE_BPS, BASE_SLIP_BPS)
    stress = simulate(candles, candidates, STRESS_FEE_BPS, STRESS_SLIP_BPS)
    segments = {seg[0]: stat_block(base["trades"], st["flips"], candles,
                                   base["equity"], seg) for seg in SEGMENTS}
    all_block = stat_block(base["trades"], st["flips"], candles, base["equity"],
                           ("all", 2021, 2100))
    stress_segments = {seg[0]: stat_block(stress["trades"], st["flips"], candles,
                                          stress["equity"], seg) for seg in SEGMENTS}
    return {
        "tf": tf, "mode": mode, "fid": fid,
        "label": f"{mode}{fid}",
        "candidates": len(candidates),
        "segments": segments, "all": all_block,
        "stress": stress_segments,
        "trades": base["trades"],
    }


def fmt_pf(v: float) -> str:
    return "inf" if math.isinf(v) else f"{v:.2f}"


def print_table(results: list[dict], tf: str) -> None:
    print(f"\n=== BTCUSDT {tf} | ST10x3 | 入场A=翻转收盘 B=回踩0.25ATR | 出场1.5ATR/1.5R ===")
    print(f"{'组':<4} {'全样本笔数':>8} {'全PF':>7} {'DEV PF':>7} {'VAL PF':>7} "
          f"{'OOS PF':>7} {'OOS笔':>6} {'最长连损':>7} {'损/百笔':>7} "
          f"{'10根止损/百翻转':>13} {'OOS DD%':>8} {'压力OOS PF':>9}")
    for r in results:
        a, dv, va, oo = r["all"], r["segments"]["dev"], r["segments"]["val"], r["segments"]["oos"]
        print(f"{r['label']:<4} {a['n']:>8} {fmt_pf(a['pf']):>7} {fmt_pf(dv['pf']):>7} "
              f"{fmt_pf(va['pf']):>7} {fmt_pf(oo['pf']):>7} {oo['n']:>6} "
              f"{a['max_stop_streak']:>7} {a['stops_per_100']:>7.1f} "
              f"{a['stop_within_10_per_100_flips']:>13.1f} {oo['max_dd_pct']:>8.2f} "
              f"{fmt_pf(r['stress']['oos']['pf']):>9}")

    print("\n-- 过滤后交易数变化（相对同入场 ST0） --")
    for mode in ("A", "B"):
        base_n = next(x["all"]["n"] for x in results if x["label"] == f"{mode}0")
        for fid in range(4):
            r = next(x for x in results if x["label"] == f"{mode}{fid}")
            n = r["all"]["n"]
            delta = n - base_n
            pct = delta / base_n * 100 if base_n else 0.0
            print(f"  {mode}{fid}: {n:4d} 笔  Δ={delta:+4d} ({pct:+6.1f}%)")


def _reason_counts(trades: list[dict]) -> dict:
    out = {}
    for t in trades:
        out[t["reason"]] = out.get(t["reason"], 0) + 1
    return out


def _gross_pf(trades: list[dict]) -> float:
    gp = sum(t["gross"] for t in trades if t["gross"] > 0)
    gl = -sum(t["gross"] for t in trades if t["gross"] < 0)
    return gp / gl if gl > 0 else (float("inf") if gp > 0 else 0.0)


def run_exit_study() -> None:
    """只动出场：A0 固定1.5R止盈 vs A0 ST轨道跟随。"""
    results = []
    for tf in TF_MS:
        candles = load_tf(tf)
        o = [c["o"] for c in candles]
        h = [c["h"] for c in candles]
        l = [c["l"] for c in candles]
        c = [c["c"] for c in candles]
        st = super_trend(o, h, l, c, periods=ST_P, multiplier=ST_M,
                         src="hl2", change_atr=True)
        atr = ta_atr(h, l, c, ST_P)
        adx = ta_adx(h, l, c, ADX_LEN)
        er = er_series(c, ER_LEN)
        candidates = build_candidates(candles, st, atr, adx, er, "A", 0)

        variants = [
            ("A0-TP", lambda sim: sim(candles, candidates, BASE_FEE_BPS, BASE_SLIP_BPS),
             lambda sim: sim(candles, candidates, STRESS_FEE_BPS, STRESS_SLIP_BPS)),
            ("A0-ST", lambda sim: sim(candles, candidates, st, BASE_FEE_BPS, BASE_SLIP_BPS),
             lambda sim: sim(candles, candidates, st, STRESS_FEE_BPS, STRESS_SLIP_BPS)),
        ]
        for label, base_fn, stress_fn in variants:
            fn = simulate if label == "A0-TP" else simulate_st_trail
            sfn = simulate if label == "A0-TP" else simulate_st_trail
            base = base_fn(fn)
            stress = stress_fn(sfn)
            results.append({
                "tf": tf, "label": label,
                "segments": {seg[0]: stat_block(base["trades"], st["flips"], candles,
                                                base["equity"], seg) for seg in SEGMENTS},
                "all": stat_block(base["trades"], st["flips"], candles, base["equity"],
                                  ("all", 2021, 2100)),
                "stress": {seg[0]: stat_block(stress["trades"], st["flips"], candles,
                                              stress["equity"], seg) for seg in SEGMENTS},
                "reasons": _reason_counts(base["trades"]),
                "gross_pf": _gross_pf(base["trades"]),
                "max_dd_pct": base["max_dd_pct"],
                "trades": base["trades"],
            })

    os.makedirs(CACHE_DIR, exist_ok=True)
    rp = os.path.join(CACHE_DIR, "exit_report.json")
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump({
            "source": "data-api.binance.vision BTCUSDT spot",
            "exit_study": "A0 entry fixed; 1.5ATR initial stop; A0-TP=1.5R TP, A0-ST=ST trail",
            "params": {"st": "10x3", "risk_pct": RISK_PCT, "stop_atr": STOP_ATR,
                       "tp_r": TP_R, "leverage_cap": LEVERAGE_CAP,
                       "fee_bps": BASE_FEE_BPS, "slip_bps": BASE_SLIP_BPS},
            "results": results,
        }, fh, ensure_ascii=False, indent=2)

    for tf in TF_MS:
        print(f"\n=== {tf} 出场单变量对比 ===")
        print(f"{'组':<6} {'笔数':>6} {'毛PF':>6} {'全PF':>6} {'DEV':>6} {'VAL':>6} "
              f"{'OOS':>6} {'收益%':>8} {'OOS DD%':>8} {'连损':>5} {'损/百笔':>7} "
              f"{'压力OOS':>7}  出场原因")
        for r in [x for x in results if x["tf"] == tf]:
            a = r["all"]
            s = r["segments"]
            rc = r["reasons"]
            reason_s = "/".join(f"{k}:{v}" for k, v in sorted(rc.items()))
            print(f"{r['label']:<6} {a['n']:>6} {fmt_pf(r['gross_pf']):>6} {fmt_pf(a['pf']):>6} "
                  f"{fmt_pf(s['dev']['pf']):>6} {fmt_pf(s['val']['pf']):>6} "
                  f"{fmt_pf(s['oos']['pf']):>6} {a['ret_pct']:>+8.1f} "
                  f"{s['oos']['max_dd_pct']:>8.2f} {a['max_stop_streak']:>5} "
                  f"{a['stops_per_100']:>7.1f} {fmt_pf(r['stress']['oos']['pf']):>7}  {reason_s}")
    print(f"\n[done] exit report: {rp}")


def run_stop_sweep() -> None:
    """A0 入场 + ST 轨道跟随不变，只扫初始止损宽度。"""
    stops = [1.5, 2.0, 2.5, 3.0]
    results = []
    for tf in TF_MS:
        candles = load_tf(tf)
        o = [c["o"] for c in candles]
        h = [c["h"] for c in candles]
        l = [c["l"] for c in candles]
        c = [c["c"] for c in candles]
        st = super_trend(o, h, l, c, periods=ST_P, multiplier=ST_M,
                         src="hl2", change_atr=True)
        atr = ta_atr(h, l, c, ST_P)
        adx = ta_adx(h, l, c, ADX_LEN)
        er = er_series(c, ER_LEN)
        for stop_atr in stops:
            candidates = build_candidates(candles, st, atr, adx, er, "A", 0,
                                          stop_atr=stop_atr)
            base = simulate_st_trail(candles, candidates, st, BASE_FEE_BPS, BASE_SLIP_BPS)
            stress = simulate_st_trail(candles, candidates, st, STRESS_FEE_BPS, STRESS_SLIP_BPS)
            results.append({
                "tf": tf, "stop_atr": stop_atr, "label": f"{stop_atr:.1f}ATR",
                "segments": {seg[0]: stat_block(base["trades"], st["flips"], candles,
                                                base["equity"], seg) for seg in SEGMENTS},
                "all": stat_block(base["trades"], st["flips"], candles, base["equity"],
                                  ("all", 2021, 2100)),
                "stress": {seg[0]: stat_block(stress["trades"], st["flips"], candles,
                                              stress["equity"], seg) for seg in SEGMENTS},
                "reasons": _reason_counts(base["trades"]),
                "gross_pf": _gross_pf(base["trades"]),
                "gross_pnl": sum(t["gross"] for t in base["trades"]),
                "fees": sum(t["fees"] for t in base["trades"]),
                "avg_hold": sum(t["exit_i"] - t["entry_i"] for t in base["trades"]) /
                            max(1, len(base["trades"])),
                "max_dd_pct": base["max_dd_pct"],
                "trades": base["trades"],
            })

    os.makedirs(CACHE_DIR, exist_ok=True)
    rp = os.path.join(CACHE_DIR, "stop_sweep_report.json")
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump({
            "source": "data-api.binance.vision BTCUSDT spot",
            "study": "A0 entry + ST trail exit; only initial stop ATR varies",
            "params": {"st": "10x3", "risk_pct": RISK_PCT, "leverage_cap": LEVERAGE_CAP,
                       "fee_bps": BASE_FEE_BPS, "slip_bps": BASE_SLIP_BPS,
                       "stress_fee_bps": STRESS_FEE_BPS, "stress_slip_bps": STRESS_SLIP_BPS},
            "results": results,
        }, fh, ensure_ascii=False, indent=2)

    for tf in TF_MS:
        print(f"\n=== {tf} 初始止损单变量扫描（A0 + ST跟随） ===")
        print(f"{'止损':<7} {'笔数':>6} {'毛PF':>6} {'净PF':>6} {'DEV':>6} {'VAL':>6} "
              f"{'OOS':>6} {'收益%':>8} {'OOSDD%':>7} {'连损':>5} {'损/百':>6} "
              f"{'费用':>7} {'压力OOS':>7}  出场原因")
        for r in [x for x in results if x["tf"] == tf]:
            a, s = r["all"], r["segments"]
            rc = r["reasons"]
            reason_s = "/".join(f"{k}:{v}" for k, v in sorted(rc.items()))
            print(f"{r['label']:<7} {a['n']:>6} {fmt_pf(r['gross_pf']):>6} {fmt_pf(a['pf']):>6} "
                  f"{fmt_pf(s['dev']['pf']):>6} {fmt_pf(s['val']['pf']):>6} "
                  f"{fmt_pf(s['oos']['pf']):>6} {a['ret_pct']:>+8.1f} "
                  f"{s['oos']['max_dd_pct']:>7.2f} {a['max_stop_streak']:>5} "
                  f"{a['stops_per_100']:>6.1f} {r['fees']:>7.0f} "
                  f"{fmt_pf(r['stress']['oos']['pf']):>7}  {reason_s}")
    print(f"\n[done] stop sweep report: {rp}")


def run_cooldown_sweep() -> None:
    """1h + 3.0ATR初始止损 + ST跟随不变，只扫退出后冷却。"""
    cooldowns = [0, 4, 8, 16]
    tf = "1h"
    stop_atr = 3.0
    candles = load_tf(tf)
    o = [c["o"] for c in candles]
    h = [c["h"] for c in candles]
    l = [c["l"] for c in candles]
    c = [c["c"] for c in candles]
    st = super_trend(o, h, l, c, periods=ST_P, multiplier=ST_M,
                     src="hl2", change_atr=True)
    atr = ta_atr(h, l, c, ST_P)
    adx = ta_adx(h, l, c, ADX_LEN)
    er = er_series(c, ER_LEN)
    candidates = build_candidates(candles, st, atr, adx, er, "A", 0,
                                  stop_atr=stop_atr)
    results = []
    for cd in cooldowns:
        base = simulate_st_trail(candles, candidates, st, BASE_FEE_BPS, BASE_SLIP_BPS,
                                 cooldown_bars=cd)
        stress = simulate_st_trail(candles, candidates, st, STRESS_FEE_BPS, STRESS_SLIP_BPS,
                                   cooldown_bars=cd)
        results.append({
            "cooldown": cd, "label": f"CD{cd}",
            "segments": {seg[0]: stat_block(base["trades"], st["flips"], candles,
                                            base["equity"], seg) for seg in SEGMENTS},
            "all": stat_block(base["trades"], st["flips"], candles, base["equity"],
                              ("all", 2021, 2100)),
            "stress": {seg[0]: stat_block(stress["trades"], st["flips"], candles,
                                          stress["equity"], seg) for seg in SEGMENTS},
            "reasons": _reason_counts(base["trades"]),
            "gross_pf": _gross_pf(base["trades"]),
            "gross_pnl": sum(t["gross"] for t in base["trades"]),
            "fees": sum(t["fees"] for t in base["trades"]),
            "skipped": base["skipped"],
            "avg_hold": sum(t["exit_i"] - t["entry_i"] for t in base["trades"]) /
                        max(1, len(base["trades"])),
            "max_dd_pct": base["max_dd_pct"],
            "trades": base["trades"],
        })

    os.makedirs(CACHE_DIR, exist_ok=True)
    rp = os.path.join(CACHE_DIR, "cooldown_report.json")
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump({
            "source": "data-api.binance.vision BTCUSDT spot",
            "study": "1h A0 + 3.0ATR initial stop + ST trail; only cooldown varies",
            "params": {"st": "10x3", "stop_atr": stop_atr, "risk_pct": RISK_PCT,
                       "leverage_cap": LEVERAGE_CAP, "fee_bps": BASE_FEE_BPS,
                       "slip_bps": BASE_SLIP_BPS, "stress_fee_bps": STRESS_FEE_BPS,
                       "stress_slip_bps": STRESS_SLIP_BPS},
            "results": results,
        }, fh, ensure_ascii=False, indent=2)

    print("\n=== 1h 退出冷却扫描（A0 + 3.0ATR + ST跟随） ===")
    print(f"{'冷却':<6} {'笔数':>6} {'跳过':>6} {'毛PF':>6} {'净PF':>6} {'DEV':>6} "
          f"{'VAL':>6} {'OOS':>6} {'收益%':>8} {'OOSDD%':>7} {'连损':>5} "
          f"{'损/百':>6} {'费用':>7} {'压力OOS':>7}  出场原因")
    for r in results:
        a, s = r["all"], r["segments"]
        rc = r["reasons"]
        reason_s = "/".join(f"{k}:{v}" for k, v in sorted(rc.items()))
        print(f"{r['label']:<6} {a['n']:>6} {r['skipped']:>6} {fmt_pf(r['gross_pf']):>6} "
              f"{fmt_pf(a['pf']):>6} {fmt_pf(s['dev']['pf']):>6} "
              f"{fmt_pf(s['val']['pf']):>6} {fmt_pf(s['oos']['pf']):>6} "
              f"{a['ret_pct']:>+8.1f} {s['oos']['max_dd_pct']:>7.2f} "
              f"{a['max_stop_streak']:>5} {a['stops_per_100']:>6.1f} "
              f"{r['fees']:>7.0f} {fmt_pf(r['stress']['oos']['pf']):>7}  {reason_s}")
    print(f"\n[done] cooldown report: {rp}")


def run_factor_sweep(factors=None, report_name="factor_report.json", tf="1h",
                     atr_min_ratio: float = 0.0) -> None:
    """A0 + 3.0ATR初始止损 + ST跟随不变，只扫 SuperTrend Factor。"""
    factors = factors or [2.5, 3.0, 3.5, 4.0]
    stop_atr = 3.0
    candles = load_tf(tf)
    o = [c["o"] for c in candles]
    h = [c["h"] for c in candles]
    l = [c["l"] for c in candles]
    c = [c["c"] for c in candles]
    atr = ta_atr(h, l, c, ST_P)
    adx = ta_adx(h, l, c, ADX_LEN)
    er = er_series(c, ER_LEN)
    results = []
    for factor in factors:
        st = super_trend(o, h, l, c, periods=ST_P, multiplier=factor,
                         src="hl2", change_atr=True)
        candidates = build_candidates(candles, st, atr, adx, er, "A", 0,
                                      stop_atr=stop_atr, atr_min_ratio=atr_min_ratio)
        base = simulate_st_trail(candles, candidates, st, BASE_FEE_BPS, BASE_SLIP_BPS)
        stress = simulate_st_trail(candles, candidates, st, STRESS_FEE_BPS, STRESS_SLIP_BPS)
        results.append({
            "factor": factor, "label": f"F{factor}",
            "flips": len(st["flips"]),
            "segments": {seg[0]: stat_block(base["trades"], st["flips"], candles,
                                            base["equity"], seg) for seg in SEGMENTS},
            "all": stat_block(base["trades"], st["flips"], candles, base["equity"],
                              ("all", 2021, 2100)),
            "stress": {seg[0]: stat_block(stress["trades"], st["flips"], candles,
                                          stress["equity"], seg) for seg in SEGMENTS},
            "reasons": _reason_counts(base["trades"]),
            "gross_pf": _gross_pf(base["trades"]),
            "gross_pnl": sum(t["gross"] for t in base["trades"]),
            "fees": sum(t["fees"] for t in base["trades"]),
            "avg_hold": sum(t["exit_i"] - t["entry_i"] for t in base["trades"]) /
                        max(1, len(base["trades"])),
            "max_dd_pct": base["max_dd_pct"],
            "trades": base["trades"],
        })

    os.makedirs(CACHE_DIR, exist_ok=True)
    rp = os.path.join(CACHE_DIR, report_name)
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump({
            "source": "data-api.binance.vision BTCUSDT spot",
            "study": f"{tf} A0 + 3.0ATR initial stop + ST trail; only ST factor varies"
                     + (f"; atr_min_ratio={atr_min_ratio}" if atr_min_ratio else ""),
            "params": {"atr_period": ST_P, "stop_atr": stop_atr, "risk_pct": RISK_PCT,
                       "atr_min_ratio": atr_min_ratio, "leverage_cap": LEVERAGE_CAP,
                       "fee_bps": BASE_FEE_BPS, "slip_bps": BASE_SLIP_BPS,
                       "stress_fee_bps": STRESS_FEE_BPS,
                       "stress_slip_bps": STRESS_SLIP_BPS},
            "results": results,
        }, fh, ensure_ascii=False, indent=2)

    atr_tag = f" + ATR>={atr_min_ratio:.1f}" if atr_min_ratio else ""
    print(f"\n=== {tf} ST Factor 扫描（A0 + 3.0ATR + ST跟随{atr_tag}） ===")
    print(f"{'Factor':<7} {'翻转':>6} {'笔数':>6} {'毛PF':>6} {'净PF':>6} {'DEV':>6} "
          f"{'VAL':>6} {'OOS':>6} {'收益%':>8} {'OOSDD%':>7} {'连损':>5} "
          f"{'损/百':>6} {'费用':>7} {'压力OOS':>7}  出场原因")
    for r in results:
        a, s = r["all"], r["segments"]
        rc = r["reasons"]
        reason_s = "/".join(f"{k}:{v}" for k, v in sorted(rc.items()))
        print(f"F{r['factor']:<6.1f} {r['flips']:>6} {a['n']:>6} "
              f"{fmt_pf(r['gross_pf']):>6} {fmt_pf(a['pf']):>6} "
              f"{fmt_pf(s['dev']['pf']):>6} {fmt_pf(s['val']['pf']):>6} "
              f"{fmt_pf(s['oos']['pf']):>6} {a['ret_pct']:>+8.1f} "
              f"{s['oos']['max_dd_pct']:>7.2f} {a['max_stop_streak']:>5} "
              f"{a['stops_per_100']:>6.1f} {r['fees']:>7.0f} "
              f"{fmt_pf(r['stress']['oos']['pf']):>7}  {reason_s}")
    print(f"\n[done] factor report: {rp}")


def _corr(xs: list[float], ys: list[float]) -> float:
    n = min(len(xs), len(ys))
    if n < 3:
        return 0.0
    xs, ys = xs[:n], ys[:n]
    mx, my = sum(xs) / n, sum(ys) / n
    sx = sum((x - mx) ** 2 for x in xs)
    sy = sum((y - my) ** 2 for y in ys)
    if sx <= 0 or sy <= 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sx * sy)


def run_signal_diag() -> None:
    """F3 原始翻转的无效信号诊断：只用 ST 自身和价格结构，不做交易。"""
    tf, factor = "1h", 3.0
    stop_atr, target_atr = 1.5, 2.25
    horizon, reverse_horizon = 20, 10
    candles = load_tf(tf)
    o = [c["o"] for c in candles]
    h = [c["h"] for c in candles]
    l = [c["l"] for c in candles]
    c = [c["c"] for c in candles]
    st = super_trend(o, h, l, c, periods=ST_P, multiplier=factor,
                     src="hl2", change_atr=True)
    atr = ta_atr(h, l, c, ST_P)
    flips = st["flips"]
    flip_types = {f["i"]: f["type"] for f in flips}

    def segment(ts: int) -> str:
        if _ms(2021) <= ts < _ms(2024):
            return "dev"
        if _ms(2024) <= ts < _ms(2025):
            return "val"
        if ts >= _ms(2025):
            return "oos"
        return "warmup"

    rows = []
    for k, flip in enumerate(flips):
        i = flip["i"]
        side = 1 if flip["type"] == "buy" else -1
        a = atr[i]
        if not a or a <= 0:
            continue
        entry = c[i]

        if side == 1:
            ref = st["dn"][i - 1] if i > 0 else None
        else:
            ref = st["up"][i - 1] if i > 0 else None
        penetration = ((entry - ref) / a) if ref is not None else None

        body = abs(c[i] - o[i]) / a

        j0 = max(0, i - 5)
        if side == 1 and st["up"][j0] is not None and st["up"][i] is not None:
            slope = (st["up"][i] - st["up"][j0]) / a
        elif side == -1 and st["dn"][j0] is not None and st["dn"][i] is not None:
            slope = (st["dn"][j0] - st["dn"][i]) / a
        else:
            slope = None

        cross_count = 0
        line_key = "dn" if side == 1 else "up"
        last_sign = None
        for j in range(max(0, i - 10), i):
            line = st[line_key][j]
            if line is None:
                continue
            sign = 1 if c[j] > line else -1
            if last_sign is not None and sign != last_sign:
                cross_count += 1
            last_sign = sign

        bars_since = (i - flips[k - 1]["i"]) if k > 0 else None
        prev_trend_len = (flips[k - 1]["i"] - flips[k - 2]["i"]) if k > 1 else None

        if i >= 50:
            base_atr = [x for x in atr[i - 50:i] if x is not None]
            atr_ratio = a / (sum(base_atr) / len(base_atr)) if base_atr else None
        else:
            atr_ratio = None

        atr_pct_100 = atr_z_100 = atr_chg_10 = None
        if i >= 100:
            win_atr = [x for x in atr[i - 100:i] if x is not None]
            if win_atr:
                atr_pct_100 = sum(1 for x in win_atr if x <= a) / len(win_atr)
                mean_a = sum(win_atr) / len(win_atr)
                var_a = sum((x - mean_a) ** 2 for x in win_atr) / len(win_atr)
                sd_a = math.sqrt(var_a)
                atr_z_100 = (a - mean_a) / sd_a if sd_a > 0 else 0.0
        if i >= 10 and atr[i - 10]:
            atr_chg_10 = a / atr[i - 10] - 1

        lo20 = min(l[max(0, i - 19):i + 1])
        hi20 = max(h[max(0, i - 19):i + 1])
        pos20 = (entry - lo20) / (hi20 - lo20) if hi20 > lo20 else 0.5

        fav_i = adv_i = None
        mfe = mae = 0.0
        for j in range(i + 1, min(len(candles), i + horizon + 1)):
            if side == 1:
                fav = h[j] >= entry + target_atr * a
                adv = l[j] <= entry - stop_atr * a
                mfe = max(mfe, (h[j] - entry) / a)
                mae = max(mae, (entry - l[j]) / a)
            else:
                fav = l[j] <= entry - target_atr * a
                adv = h[j] >= entry + stop_atr * a
                mfe = max(mfe, (entry - l[j]) / a)
                mae = max(mae, (h[j] - entry) / a)
            if fav and fav_i is None:
                fav_i = j
            if adv and adv_i is None:
                adv_i = j

        rev_i = None
        for f in flips[k + 1:]:
            if f["i"] > i + reverse_horizon:
                break
            if flip_types.get(f["i"]) != flip["type"]:
                rev_i = f["i"]
                break

        if adv_i is not None and (fav_i is None or adv_i < fav_i):
            label = "bad"
        elif rev_i is not None and (fav_i is None or rev_i < fav_i):
            label = "bad"
        elif fav_i is not None:
            label = "good"
        else:
            label = "neutral"

        end_i = min(len(candles) - 1, i + horizon)
        ret20 = (c[end_i] - entry) / entry * 100 * side
        rows.append({
            "i": i, "ts": candles[i]["ts"], "segment": segment(candles[i]["ts"]),
            "side": "buy" if side == 1 else "sell", "label": label,
            "penetration_atr": penetration, "body_atr": body, "band_slope": slope,
            "bars_since_last_flip": bars_since, "prev_trend_len": prev_trend_len,
            "cross_count_10": cross_count, "atr_ratio": atr_ratio,
            "atr_pct_100": atr_pct_100, "atr_z_100": atr_z_100,
            "atr_chg_10": atr_chg_10,
            "close_position_20": pos20, "mfe_atr": mfe, "mae_atr": mae,
            "fav_i": fav_i, "adv_i": adv_i, "reverse_i": rev_i,
            "ret20_pct": ret20, "prev_label": "unknown",
        })

    by_i = {r["i"]: r for r in rows}
    for r in rows:
        if r["bars_since_last_flip"] is None:
            continue
        prev = by_i.get(r["i"] - r["bars_since_last_flip"])
        if prev and r["bars_since_last_flip"] >= horizon:
            r["prev_label"] = prev["label"]

    print(f"\n=== {tf} ST{factor:.0f} 无效信号诊断（{len(rows)} 次翻转） ===")
    print("标签定义：20根内先到-1.5ATR，或10根内反向翻转且未先到+2.25ATR = bad")
    for seg in ("dev", "val", "oos"):
        sub = [r for r in rows if r["segment"] == seg]
        if not sub:
            continue
        bad = sum(1 for r in sub if r["label"] == "bad")
        good = sum(1 for r in sub if r["label"] == "good")
        print(f"  {seg}: n={len(sub)}, bad={bad/len(sub)*100:.1f}%, "
              f"good={good/len(sub)*100:.1f}%, neutral={(len(sub)-bad-good)/len(sub)*100:.1f}%")

    features = [
        ("penetration_atr", [0, 0.25, 0.5, 1.0, 2.0, 99]),
        ("body_atr", [0, 0.2, 0.5, 1.0, 2.0, 99]),
        ("band_slope", [-99, -0.2, 0.0, 0.2, 99]),
        ("bars_since_last_flip", [0, 5, 10, 20, 40, 99999]),
        ("prev_trend_len", [0, 5, 10, 20, 40, 99999]),
        ("cross_count_10", [0, 1, 2, 3, 99]),
        ("atr_ratio", [0, 0.8, 1.0, 1.2, 99]),
        ("atr_pct_100", [0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.01]),
        ("atr_chg_10", [-1, -0.15, -0.05, 0.05, 0.15, 99]),
        ("atr_z_100", [-99, -1, 0, 1, 2, 99]),
        ("close_position_20", [0, 0.25, 0.5, 0.75, 1.01]),
    ]
    dev = [r for r in rows if r["segment"] == "dev"]

    def in_bin(v, lo, hi):
        return v is not None and lo <= v < hi

    print("\n-- DEV 分桶坏信号率（n<20 的桶不显示） --")
    for name, edges in features:
        print(f"  [{name}]")
        for idx in range(len(edges) - 1):
            lo, hi = edges[idx], edges[idx + 1]
            sub = [r for r in dev if in_bin(r[name], lo, hi)]
            if len(sub) < 20:
                continue
            bad = sum(1 for r in sub if r["label"] == "bad") / len(sub) * 100
            good = sum(1 for r in sub if r["label"] == "good") / len(sub) * 100
            avg = sum(r["ret20_pct"] for r in sub) / len(sub)
            print(f"    {lo:>7.2f}~{hi:<7.2f} n={len(sub):>4} bad={bad:>5.1f}% "
                  f"good={good:>5.1f}% avg20={avg:>+6.2f}%")

    print("\n-- ATR 状态跨段坏信号率（格式=样本数/坏信号率） --")
    atr_features = [
        ("atr_ratio", [0, 0.8, 1.0, 1.2, 99]),
        ("atr_pct_100", [0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.01]),
        ("atr_chg_10", [-1, -0.15, -0.05, 0.05, 0.15, 99]),
        ("atr_z_100", [-99, -1, 0, 1, 2, 99]),
    ]
    for name, edges in atr_features:
        print(f"  [{name}]")
        for idx in range(len(edges) - 1):
            lo, hi = edges[idx], edges[idx + 1]
            dsub = [r for r in dev if in_bin(r[name], lo, hi)]
            if len(dsub) < 20:
                continue
            parts = []
            for seg in ("dev", "val", "oos"):
                sub = [r for r in rows if r["segment"] == seg and in_bin(r[name], lo, hi)]
                bad = sum(1 for r in sub if r["label"] == "bad") / len(sub) * 100 if sub else 0.0
                parts.append(f"{seg}={len(sub):>3}/{bad:>5.1f}%")
            print(f"    {lo:>7.2f}~{hi:<7.2f} " + " ".join(parts))

    print("\n-- DEV 特征与坏信号相关性（正相关=越大越坏） --")
    corr = []
    for name, _ in features:
        pairs = [(r[name], 1 if r["label"] == "bad" else 0)
                 for r in dev if r[name] is not None]
        if len(pairs) >= 20:
            corr.append((name, _corr([p[0] for p in pairs], [p[1] for p in pairs])))
    for name, v in sorted(corr, key=lambda x: -abs(x[1])):
        print(f"  {name:<24} {v:+.3f}")

    print("\n-- 上一笔翻转结果对当前翻转的影响（全样本） --")
    for cat in ("bad", "good", "neutral", "unknown"):
        sub = [r for r in rows if r["prev_label"] == cat]
        if len(sub) < 20:
            continue
        bad = sum(1 for r in sub if r["label"] == "bad") / len(sub) * 100
        print(f"  prev={cat:<8} n={len(sub):>4} bad={bad:>5.1f}%")

    os.makedirs(CACHE_DIR, exist_ok=True)
    rp = os.path.join(CACHE_DIR, "signal_quality_F3_1h.json")
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump({"source": "data-api.binance.vision BTCUSDT spot",
                   "study": "1h ST10x3 flip-level signal quality diagnostic",
                   "label_rule": {"horizon": horizon, "stop_atr": stop_atr,
                                  "target_atr": target_atr,
                                  "reverse_horizon": reverse_horizon},
                   "rows": rows}, fh, ensure_ascii=False, indent=2)
    print(f"\n[done] signal report: {rp}")


def run_atr_filter_study() -> None:
    """1h F3 实际回测：只加 ATR 收缩过滤，其他条件不变。"""
    tf, factor, stop_atr = "1h", 3.0, 3.0
    candles = load_tf(tf)
    o = [c["o"] for c in candles]
    h = [c["h"] for c in candles]
    l = [c["l"] for c in candles]
    c = [c["c"] for c in candles]
    st = super_trend(o, h, l, c, periods=ST_P, multiplier=factor,
                     src="hl2", change_atr=True)
    atr = ta_atr(h, l, c, ST_P)
    adx = ta_adx(h, l, c, ADX_LEN)
    er = er_series(c, ER_LEN)
    all_candidates = build_candidates(candles, st, atr, adx, er, "A", 0,
                                      stop_atr=stop_atr)
    results = []
    for label, ratio in (("base", 0.0), ("atr>=1", 1.0)):
        candidates = all_candidates if ratio == 0 else build_candidates(
            candles, st, atr, adx, er, "A", 0, stop_atr=stop_atr,
            atr_min_ratio=ratio)
        base = simulate_st_trail(candles, candidates, st, BASE_FEE_BPS, BASE_SLIP_BPS)
        stress = simulate_st_trail(candles, candidates, st, STRESS_FEE_BPS, STRESS_SLIP_BPS)
        results.append({
            "label": label, "candidates": len(candidates),
            "skipped": len(all_candidates) - len(candidates),
            "segments": {seg[0]: stat_block(base["trades"], st["flips"], candles,
                                            base["equity"], seg) for seg in SEGMENTS},
            "all": stat_block(base["trades"], st["flips"], candles, base["equity"],
                              ("all", 2021, 2100)),
            "stress": {seg[0]: stat_block(stress["trades"], st["flips"], candles,
                                          stress["equity"], seg) for seg in SEGMENTS},
            "reasons": _reason_counts(base["trades"]),
            "gross_pf": _gross_pf(base["trades"]),
            "gross_pnl": sum(t["gross"] for t in base["trades"]),
            "fees": sum(t["fees"] for t in base["trades"]),
            "max_dd_pct": base["max_dd_pct"],
            "trades": base["trades"],
        })

    os.makedirs(CACHE_DIR, exist_ok=True)
    rp = os.path.join(CACHE_DIR, "atr_filter_report.json")
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump({
            "source": "data-api.binance.vision BTCUSDT spot",
            "study": "1h F3 A0 + 3.0ATR initial stop + ST trail; ATR contraction filter only",
            "params": {"st": "10x3", "stop_atr": stop_atr, "risk_pct": RISK_PCT,
                       "atr_filter": "ATR10 >= mean(ATR10 previous 50 bars)",
                       "fee_bps": BASE_FEE_BPS, "slip_bps": BASE_SLIP_BPS},
            "results": results,
        }, fh, ensure_ascii=False, indent=2)

    print("\n=== 1h F3 ATR 收缩过滤对比 ===")
    print(f"{'组':<8} {'候选':>6} {'过滤':>6} {'笔数':>6} {'毛PF':>6} {'净PF':>6} "
          f"{'DEV':>6} {'VAL':>6} {'OOS':>6} {'收益%':>8} {'OOSDD%':>7} "
          f"{'连损':>5} {'损/百':>6} {'费用':>7} {'压力OOS':>7}")
    for r in results:
        a, s = r["all"], r["segments"]
        print(f"{r['label']:<8} {r['candidates']:>6} {r['skipped']:>6} {a['n']:>6} "
              f"{fmt_pf(r['gross_pf']):>6} {fmt_pf(a['pf']):>6} "
              f"{fmt_pf(s['dev']['pf']):>6} {fmt_pf(s['val']['pf']):>6} "
              f"{fmt_pf(s['oos']['pf']):>6} {a['ret_pct']:>+8.1f} "
              f"{s['oos']['max_dd_pct']:>7.2f} {a['max_stop_streak']:>5} "
              f"{a['stops_per_100']:>6.1f} {r['fees']:>7.0f} "
              f"{fmt_pf(r['stress']['oos']['pf']):>7}")
    print(f"\n[done] ATR filter report: {rp}")


def run_atr_threshold_sweep(ratios=None, report_name="atr_threshold_report.json",
                            factor: float = 3.0) -> None:
    """1h 固定 Factor，只扫 ATR 过滤阈值，入场/出场不变。"""
    tf, stop_atr = "1h", 3.0
    ratios = ratios or [0.0, 1.0, 1.1, 1.2, 1.3]
    candles = load_tf(tf)
    o = [c["o"] for c in candles]
    h = [c["h"] for c in candles]
    l = [c["l"] for c in candles]
    c = [c["c"] for c in candles]
    st = super_trend(o, h, l, c, periods=ST_P, multiplier=factor,
                     src="hl2", change_atr=True)
    atr = ta_atr(h, l, c, ST_P)
    adx = ta_adx(h, l, c, ADX_LEN)
    er = er_series(c, ER_LEN)
    all_candidates = build_candidates(candles, st, atr, adx, er, "A", 0,
                                      stop_atr=stop_atr)
    results = []
    for ratio in ratios:
        candidates = all_candidates if ratio == 0 else build_candidates(
            candles, st, atr, adx, er, "A", 0, stop_atr=stop_atr,
            atr_min_ratio=ratio)
        base = simulate_st_trail(candles, candidates, st, BASE_FEE_BPS, BASE_SLIP_BPS)
        stress = simulate_st_trail(candles, candidates, st, STRESS_FEE_BPS, STRESS_SLIP_BPS)
        results.append({
            "ratio": ratio, "label": "base" if ratio == 0 else f"atr>={ratio:g}",
            "candidates": len(candidates),
            "skipped": len(all_candidates) - len(candidates),
            "segments": {seg[0]: stat_block(base["trades"], st["flips"], candles,
                                            base["equity"], seg) for seg in SEGMENTS},
            "all": stat_block(base["trades"], st["flips"], candles, base["equity"],
                              ("all", 2021, 2100)),
            "stress": {seg[0]: stat_block(stress["trades"], st["flips"], candles,
                                          stress["equity"], seg) for seg in SEGMENTS},
            "gross_pf": _gross_pf(base["trades"]),
            "gross_pnl": sum(t["gross"] for t in base["trades"]),
            "fees": sum(t["fees"] for t in base["trades"]),
            "trades": base["trades"],
        })

    os.makedirs(CACHE_DIR, exist_ok=True)
    rp = os.path.join(CACHE_DIR, report_name)
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump({
            "source": "data-api.binance.vision BTCUSDT spot",
            "study": f"{tf} F{factor:g} A0 + 3.0ATR initial stop + ST trail; "
                     "only ATR min ratio varies",
            "params": {"st": f"10x{factor:g}", "stop_atr": stop_atr, "risk_pct": RISK_PCT,
                       "atr_window": 50, "fee_bps": BASE_FEE_BPS,
                       "slip_bps": BASE_SLIP_BPS},
            "results": results,
        }, fh, ensure_ascii=False, indent=2)

    print(f"\n=== {tf} F{factor:g} ATR 阈值扫描 ===")
    print(f"{'组':<9} {'候选':>6} {'过滤':>6} {'笔数':>6} {'毛PF':>6} {'净PF':>6} "
          f"{'DEV':>6} {'VAL':>6} {'OOS':>6} {'收益%':>8} {'OOSDD%':>7} "
          f"{'连损':>5} {'损/百':>6} {'费用':>7} {'压力OOS':>7}")
    for r in results:
        a, s = r["all"], r["segments"]
        print(f"{r['label']:<9} {r['candidates']:>6} {r['skipped']:>6} {a['n']:>6} "
              f"{fmt_pf(r['gross_pf']):>6} {fmt_pf(a['pf']):>6} "
              f"{fmt_pf(s['dev']['pf']):>6} {fmt_pf(s['val']['pf']):>6} "
              f"{fmt_pf(s['oos']['pf']):>6} {a['ret_pct']:>+8.1f} "
              f"{s['oos']['max_dd_pct']:>7.2f} {a['max_stop_streak']:>5} "
              f"{a['stops_per_100']:>6.1f} {r['fees']:>7.0f} "
              f"{fmt_pf(r['stress']['oos']['pf']):>7}")
    print(f"\n[done] ATR threshold report: {rp}")


def run_atr_z_study() -> None:
    """1h F3.5：比较 ATR ratio 过滤与 ATR z-score 异常低波动过滤。"""
    tf, factor, stop_atr = "1h", 3.5, 3.0
    candles = load_tf(tf)
    o = [c["o"] for c in candles]
    h = [c["h"] for c in candles]
    l = [c["l"] for c in candles]
    c = [c["c"] for c in candles]
    st = super_trend(o, h, l, c, periods=ST_P, multiplier=factor,
                     src="hl2", change_atr=True)
    atr = ta_atr(h, l, c, ST_P)
    adx = ta_adx(h, l, c, ADX_LEN)
    er = er_series(c, ER_LEN)
    base_candidates = build_candidates(candles, st, atr, adx, er, "A", 0,
                                       stop_atr=stop_atr)
    variants = [
        ("base", {}),
        ("ratio>=1.05", {"atr_min_ratio": 1.05}),
        ("z>=-1", {"atr_min_z": -1.0}),
    ]
    results = []
    for label, kwargs in variants:
        candidates = base_candidates if not kwargs else build_candidates(
            candles, st, atr, adx, er, "A", 0, stop_atr=stop_atr, **kwargs)
        base = simulate_st_trail(candles, candidates, st, BASE_FEE_BPS, BASE_SLIP_BPS)
        stress = simulate_st_trail(candles, candidates, st, STRESS_FEE_BPS, STRESS_SLIP_BPS)
        results.append({
            "label": label, "candidates": len(candidates),
            "skipped": len(base_candidates) - len(candidates),
            "segments": {seg[0]: stat_block(base["trades"], st["flips"], candles,
                                            base["equity"], seg) for seg in SEGMENTS},
            "all": stat_block(base["trades"], st["flips"], candles, base["equity"],
                              ("all", 2021, 2100)),
            "stress": {seg[0]: stat_block(stress["trades"], st["flips"], candles,
                                          stress["equity"], seg) for seg in SEGMENTS},
            "gross_pf": _gross_pf(base["trades"]),
            "gross_pnl": sum(t["gross"] for t in base["trades"]),
            "fees": sum(t["fees"] for t in base["trades"]),
            "max_dd_pct": base["max_dd_pct"],
            "trades": base["trades"],
        })

    os.makedirs(CACHE_DIR, exist_ok=True)
    rp = os.path.join(CACHE_DIR, "atr_z_report.json")
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump({
            "source": "data-api.binance.vision BTCUSDT spot",
            "study": "1h F3.5 A0 + 3.0ATR initial stop + ST trail; ATR regime filters",
            "params": {"st": "10x3.5", "stop_atr": stop_atr, "risk_pct": RISK_PCT,
                       "ratio_filter": "ATR10 >= 1.05 * mean(ATR10 previous 50)",
                       "z_filter": "z(ATR10 vs previous 100) >= -1",
                       "fee_bps": BASE_FEE_BPS, "slip_bps": BASE_SLIP_BPS},
            "results": results,
        }, fh, ensure_ascii=False, indent=2)

    print("\n=== 1h F3.5 ATR 状态过滤对比 ===")
    print(f"{'组':<13} {'候选':>6} {'过滤':>6} {'笔数':>6} {'毛PF':>6} {'净PF':>6} "
          f"{'DEV':>6} {'VAL':>6} {'OOS':>6} {'收益%':>8} {'OOSDD%':>7} "
          f"{'连损':>5} {'损/百':>6} {'费用':>7} {'压力OOS':>7}")
    for r in results:
        a, s = r["all"], r["segments"]
        print(f"{r['label']:<13} {r['candidates']:>6} {r['skipped']:>6} {a['n']:>6} "
              f"{fmt_pf(r['gross_pf']):>6} {fmt_pf(a['pf']):>6} "
              f"{fmt_pf(s['dev']['pf']):>6} {fmt_pf(s['val']['pf']):>6} "
              f"{fmt_pf(s['oos']['pf']):>6} {a['ret_pct']:>+8.1f} "
              f"{s['oos']['max_dd_pct']:>7.2f} {a['max_stop_streak']:>5} "
              f"{a['stops_per_100']:>6.1f} {r['fees']:>7.0f} "
              f"{fmt_pf(r['stress']['oos']['pf']):>7}")
    print(f"\n[done] ATR z report: {rp}")


def run_atr_pct_study() -> None:
    """1h F3.5：比较 ATR 分位数过滤与 ATR ratio 过滤。"""
    tf, factor, stop_atr = "1h", 3.5, 3.0
    candles = load_tf(tf)
    o = [c["o"] for c in candles]
    h = [c["h"] for c in candles]
    l = [c["l"] for c in candles]
    c = [c["c"] for c in candles]
    st = super_trend(o, h, l, c, periods=ST_P, multiplier=factor,
                     src="hl2", change_atr=True)
    atr = ta_atr(h, l, c, ST_P)
    adx = ta_adx(h, l, c, ADX_LEN)
    er = er_series(c, ER_LEN)
    base_candidates = build_candidates(candles, st, atr, adx, er, "A", 0,
                                       stop_atr=stop_atr)
    variants = [
        ("base", {}),
        ("ratio>=1.05", {"atr_min_ratio": 1.05}),
        ("pct>=0.3", {"atr_min_pct": 0.3}),
    ]
    results = []
    for label, kwargs in variants:
        candidates = base_candidates if not kwargs else build_candidates(
            candles, st, atr, adx, er, "A", 0, stop_atr=stop_atr, **kwargs)
        base = simulate_st_trail(candles, candidates, st, BASE_FEE_BPS, BASE_SLIP_BPS)
        stress = simulate_st_trail(candles, candidates, st, STRESS_FEE_BPS, STRESS_SLIP_BPS)
        results.append({
            "label": label, "candidates": len(candidates),
            "skipped": len(base_candidates) - len(candidates),
            "segments": {seg[0]: stat_block(base["trades"], st["flips"], candles,
                                            base["equity"], seg) for seg in SEGMENTS},
            "all": stat_block(base["trades"], st["flips"], candles, base["equity"],
                              ("all", 2021, 2100)),
            "stress": {seg[0]: stat_block(stress["trades"], st["flips"], candles,
                                          stress["equity"], seg) for seg in SEGMENTS},
            "gross_pf": _gross_pf(base["trades"]),
            "gross_pnl": sum(t["gross"] for t in base["trades"]),
            "fees": sum(t["fees"] for t in base["trades"]),
            "max_dd_pct": base["max_dd_pct"],
            "trades": base["trades"],
        })

    os.makedirs(CACHE_DIR, exist_ok=True)
    rp = os.path.join(CACHE_DIR, "atr_pct_report.json")
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump({
            "source": "data-api.binance.vision BTCUSDT spot",
            "study": "1h F3.5 A0 + 3.0ATR initial stop + ST trail; ATR percentile filter",
            "params": {"st": "10x3.5", "stop_atr": stop_atr, "risk_pct": RISK_PCT,
                       "ratio_filter": "ATR10 >= 1.05 * mean(ATR10 previous 50)",
                       "pct_filter": "percentile(ATR10 vs previous 100) >= 0.3",
                       "fee_bps": BASE_FEE_BPS, "slip_bps": BASE_SLIP_BPS},
            "results": results,
        }, fh, ensure_ascii=False, indent=2)

    print("\n=== 1h F3.5 ATR 分位数过滤对比 ===")
    print(f"{'组':<13} {'候选':>6} {'过滤':>6} {'笔数':>6} {'毛PF':>6} {'净PF':>6} "
          f"{'DEV':>6} {'VAL':>6} {'OOS':>6} {'收益%':>8} {'OOSDD%':>7} "
          f"{'连损':>5} {'损/百':>6} {'费用':>7} {'压力OOS':>7}")
    for r in results:
        a, s = r["all"], r["segments"]
        print(f"{r['label']:<13} {r['candidates']:>6} {r['skipped']:>6} {a['n']:>6} "
              f"{fmt_pf(r['gross_pf']):>6} {fmt_pf(a['pf']):>6} "
              f"{fmt_pf(s['dev']['pf']):>6} {fmt_pf(s['val']['pf']):>6} "
              f"{fmt_pf(s['oos']['pf']):>6} {a['ret_pct']:>+8.1f} "
              f"{s['oos']['max_dd_pct']:>7.2f} {a['max_stop_streak']:>5} "
              f"{a['stops_per_100']:>6.1f} {r['fees']:>7.0f} "
              f"{fmt_pf(r['stress']['oos']['pf']):>7}")
    print(f"\n[done] ATR percentile report: {rp}")


def run_atr_chg_study() -> None:
    """1h F3.5：比较 ATR 扩张速度过滤与 ATR ratio 过滤。"""
    tf, factor, stop_atr = "1h", 3.5, 3.0
    candles = load_tf(tf)
    o = [c["o"] for c in candles]
    h = [c["h"] for c in candles]
    l = [c["l"] for c in candles]
    c = [c["c"] for c in candles]
    st = super_trend(o, h, l, c, periods=ST_P, multiplier=factor,
                     src="hl2", change_atr=True)
    atr = ta_atr(h, l, c, ST_P)
    adx = ta_adx(h, l, c, ADX_LEN)
    er = er_series(c, ER_LEN)
    base_candidates = build_candidates(candles, st, atr, adx, er, "A", 0,
                                       stop_atr=stop_atr)
    variants = [
        ("base", {}),
        ("ratio>=1.05", {"atr_min_ratio": 1.05}),
        ("chg>=-0.05", {"atr_min_chg": -0.05}),
    ]
    results = []
    for label, kwargs in variants:
        candidates = base_candidates if not kwargs else build_candidates(
            candles, st, atr, adx, er, "A", 0, stop_atr=stop_atr, **kwargs)
        base = simulate_st_trail(candles, candidates, st, BASE_FEE_BPS, BASE_SLIP_BPS)
        stress = simulate_st_trail(candles, candidates, st, STRESS_FEE_BPS, STRESS_SLIP_BPS)
        results.append({
            "label": label, "candidates": len(candidates),
            "skipped": len(base_candidates) - len(candidates),
            "segments": {seg[0]: stat_block(base["trades"], st["flips"], candles,
                                            base["equity"], seg) for seg in SEGMENTS},
            "all": stat_block(base["trades"], st["flips"], candles, base["equity"],
                              ("all", 2021, 2100)),
            "stress": {seg[0]: stat_block(stress["trades"], st["flips"], candles,
                                          stress["equity"], seg) for seg in SEGMENTS},
            "gross_pf": _gross_pf(base["trades"]),
            "gross_pnl": sum(t["gross"] for t in base["trades"]),
            "fees": sum(t["fees"] for t in base["trades"]),
            "max_dd_pct": base["max_dd_pct"],
            "trades": base["trades"],
        })

    os.makedirs(CACHE_DIR, exist_ok=True)
    rp = os.path.join(CACHE_DIR, "atr_chg_report.json")
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump({
            "source": "data-api.binance.vision BTCUSDT spot",
            "study": "1h F3.5 A0 + 3.0ATR initial stop + ST trail; ATR expansion speed filter",
            "params": {"st": "10x3.5", "stop_atr": stop_atr, "risk_pct": RISK_PCT,
                       "ratio_filter": "ATR10 >= 1.05 * mean(ATR10 previous 50)",
                       "chg_filter": "ATR10 / ATR10[10 bars ago] - 1 >= -0.05",
                       "fee_bps": BASE_FEE_BPS, "slip_bps": BASE_SLIP_BPS},
            "results": results,
        }, fh, ensure_ascii=False, indent=2)

    print("\n=== 1h F3.5 ATR 扩张速度过滤对比 ===")
    print(f"{'组':<13} {'候选':>6} {'过滤':>6} {'笔数':>6} {'毛PF':>6} {'净PF':>6} "
          f"{'DEV':>6} {'VAL':>6} {'OOS':>6} {'收益%':>8} {'OOSDD%':>7} "
          f"{'连损':>5} {'损/百':>6} {'费用':>7} {'压力OOS':>7}")
    for r in results:
        a, s = r["all"], r["segments"]
        print(f"{r['label']:<13} {r['candidates']:>6} {r['skipped']:>6} {a['n']:>6} "
              f"{fmt_pf(r['gross_pf']):>6} {fmt_pf(a['pf']):>6} "
              f"{fmt_pf(s['dev']['pf']):>6} {fmt_pf(s['val']['pf']):>6} "
              f"{fmt_pf(s['oos']['pf']):>6} {a['ret_pct']:>+8.1f} "
              f"{s['oos']['max_dd_pct']:>7.2f} {a['max_stop_streak']:>5} "
              f"{a['stops_per_100']:>6.1f} {r['fees']:>7.0f} "
              f"{fmt_pf(r['stress']['oos']['pf']):>7}")
    print(f"\n[done] ATR expansion report: {rp}")


def run_val15() -> None:
    """把 1h 最佳低滞后方案搬到 15m 做跨周期验证。"""
    tf, factor, stop_atr = "15m", 3.5, 3.0
    candles = load_tf(tf)
    o = [c["o"] for c in candles]
    h = [c["h"] for c in candles]
    l = [c["l"] for c in candles]
    c = [c["c"] for c in candles]
    st = super_trend(o, h, l, c, periods=ST_P, multiplier=factor,
                     src="hl2", change_atr=True)
    atr = ta_atr(h, l, c, ST_P)
    adx = ta_adx(h, l, c, ADX_LEN)
    er = er_series(c, ER_LEN)
    base_candidates = build_candidates(candles, st, atr, adx, er, "A", 0,
                                       stop_atr=stop_atr)
    variants = [
        ("base", {}),
        ("ratio>=1.05", {"atr_min_ratio": 1.05}),
    ]
    results = []
    for label, kwargs in variants:
        candidates = base_candidates if not kwargs else build_candidates(
            candles, st, atr, adx, er, "A", 0, stop_atr=stop_atr, **kwargs)
        base = simulate_st_trail(candles, candidates, st, BASE_FEE_BPS, BASE_SLIP_BPS)
        stress = simulate_st_trail(candles, candidates, st, STRESS_FEE_BPS, STRESS_SLIP_BPS)
        results.append({
            "label": label, "candidates": len(candidates),
            "skipped": len(base_candidates) - len(candidates),
            "segments": {seg[0]: stat_block(base["trades"], st["flips"], candles,
                                            base["equity"], seg) for seg in SEGMENTS},
            "all": stat_block(base["trades"], st["flips"], candles, base["equity"],
                              ("all", 2021, 2100)),
            "stress": {seg[0]: stat_block(stress["trades"], st["flips"], candles,
                                          stress["equity"], seg) for seg in SEGMENTS},
            "gross_pf": _gross_pf(base["trades"]),
            "gross_pnl": sum(t["gross"] for t in base["trades"]),
            "fees": sum(t["fees"] for t in base["trades"]),
            "max_dd_pct": base["max_dd_pct"],
            "trades": base["trades"],
        })

    os.makedirs(CACHE_DIR, exist_ok=True)
    rp = os.path.join(CACHE_DIR, "val15_report.json")
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump({
            "source": "data-api.binance.vision BTCUSDT spot",
            "study": "15m cross-check of F3.5 + ATR>=1.05 low-lag candidate",
            "params": {"st": "10x3.5", "stop_atr": stop_atr, "risk_pct": RISK_PCT,
                       "ratio_filter": "ATR10 >= 1.05 * mean(ATR10 previous 50)",
                       "fee_bps": BASE_FEE_BPS, "slip_bps": BASE_SLIP_BPS},
            "results": results,
        }, fh, ensure_ascii=False, indent=2)

    print("\n=== 15m 跨周期验证（F3.5） ===")
    print(f"{'组':<13} {'候选':>6} {'过滤':>6} {'笔数':>6} {'毛PF':>6} {'净PF':>6} "
          f"{'DEV':>6} {'VAL':>6} {'OOS':>6} {'收益%':>8} {'OOSDD%':>7} "
          f"{'连损':>5} {'损/百':>6} {'费用':>7} {'压力OOS':>7}")
    for r in results:
        a, s = r["all"], r["segments"]
        print(f"{r['label']:<13} {r['candidates']:>6} {r['skipped']:>6} {a['n']:>6} "
              f"{fmt_pf(r['gross_pf']):>6} {fmt_pf(a['pf']):>6} "
              f"{fmt_pf(s['dev']['pf']):>6} {fmt_pf(s['val']['pf']):>6} "
              f"{fmt_pf(s['oos']['pf']):>6} {a['ret_pct']:>+8.1f} "
              f"{s['oos']['max_dd_pct']:>7.2f} {a['max_stop_streak']:>5} "
              f"{a['stops_per_100']:>6.1f} {r['fees']:>7.0f} "
              f"{fmt_pf(r['stress']['oos']['pf']):>7}")
    print(f"\n[done] 15m validation report: {rp}")


def run_factor_atr_matrix(tf: str = "15m",
                          report_name: str = "val15_matrix_report.json") -> None:
    """Factor 3-9 × ATR 状态过滤矩阵，寻找是否存在可用组合。"""
    stop_atr = 3.0
    factors = [3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]
    filters = [
        ("none", {}),
        ("ratio1.05", {"atr_min_ratio": 1.05}),
        ("z-1", {"atr_min_z": -1.0}),
        ("pct0.3", {"atr_min_pct": 0.3}),
        ("chg-0.05", {"atr_min_chg": -0.05}),
    ]
    candles = load_tf(tf)
    o = [c["o"] for c in candles]
    h = [c["h"] for c in candles]
    l = [c["l"] for c in candles]
    c = [c["c"] for c in candles]
    atr = ta_atr(h, l, c, ST_P)
    adx = ta_adx(h, l, c, ADX_LEN)
    er = er_series(c, ER_LEN)

    results = []
    for factor in factors:
        st = super_trend(o, h, l, c, periods=ST_P, multiplier=factor,
                         src="hl2", change_atr=True)
        all_candidates = build_candidates(candles, st, atr, adx, er, "A", 0,
                                          stop_atr=stop_atr)
        for fname, kwargs in filters:
            candidates = all_candidates if not kwargs else build_candidates(
                candles, st, atr, adx, er, "A", 0, stop_atr=stop_atr, **kwargs)
            base = simulate_st_trail(candles, candidates, st, BASE_FEE_BPS, BASE_SLIP_BPS)
            stress = simulate_st_trail(candles, candidates, st, STRESS_FEE_BPS,
                                       STRESS_SLIP_BPS)
            results.append({
                "factor": factor, "filter": fname,
                "label": f"F{factor:g}/{fname}",
                "flips": len(st["flips"]),
                "candidates": len(candidates),
                "skipped": len(all_candidates) - len(candidates),
                "segments": {seg[0]: stat_block(base["trades"], st["flips"], candles,
                                                base["equity"], seg) for seg in SEGMENTS},
                "all": stat_block(base["trades"], st["flips"], candles, base["equity"],
                                  ("all", 2021, 2100)),
                "stress": {seg[0]: stat_block(stress["trades"], st["flips"], candles,
                                              stress["equity"], seg) for seg in SEGMENTS},
                "gross_pf": _gross_pf(base["trades"]),
                "fees": sum(t["fees"] for t in base["trades"]),
                "max_dd_pct": base["max_dd_pct"],
            })

    os.makedirs(CACHE_DIR, exist_ok=True)
    rp = os.path.join(CACHE_DIR, report_name)
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump({
            "source": "data-api.binance.vision BTCUSDT spot",
            "study": f"{tf} Factor 3-9 x ATR regime filters; A0 + 3.0ATR stop + ST trail",
            "params": {"atr_period": ST_P, "stop_atr": stop_atr, "risk_pct": RISK_PCT,
                       "leverage_cap": LEVERAGE_CAP, "fee_bps": BASE_FEE_BPS,
                       "slip_bps": BASE_SLIP_BPS},
            "filters": {name: kwargs for name, kwargs in filters},
            "results": results,
        }, fh, ensure_ascii=False, indent=2)

    print(f"\n=== {tf} Factor×ATR 矩阵（共 {len(results)} 组，按净PF排序） ===")
    print(f"{'组合':<16} {'翻转':>6} {'笔数':>6} {'毛PF':>6} {'净PF':>6} "
          f"{'DEV':>6} {'VAL':>6} {'OOS':>6} {'收益%':>8} {'OOSDD%':>7} "
          f"{'连损':>5} {'费用':>7} {'压力OOS':>7}")
    for r in sorted(results, key=lambda x: -x["all"]["pf"]):
        a, s = r["all"], r["segments"]
        print(f"{r['label']:<16} {r['flips']:>6} {a['n']:>6} "
              f"{fmt_pf(r['gross_pf']):>6} {fmt_pf(a['pf']):>6} "
              f"{fmt_pf(s['dev']['pf']):>6} {fmt_pf(s['val']['pf']):>6} "
              f"{fmt_pf(s['oos']['pf']):>6} {a['ret_pct']:>+8.1f} "
              f"{s['oos']['max_dd_pct']:>7.2f} {a['max_stop_streak']:>5} "
              f"{r['fees']:>7.0f} {fmt_pf(r['stress']['oos']['pf']):>7}")
    print(f"\n[done] {tf} matrix report: {rp}")


def run_15m_factor_atr_matrix() -> None:
    run_factor_atr_matrix("15m", "val15_matrix_report.json")


def run_30m_factor_atr_matrix() -> None:
    run_factor_atr_matrix("30m", "val30_matrix_report.json")


def run_delay_diag() -> None:
    """比较最终候选的入场后20根MFE、实际收益和趋势捕获率。"""
    scenarios = [
        ("1h F3.5+ATR1.05", "1h", 3.5, {"atr_min_ratio": 1.05}),
        ("1h F6", "1h", 6.0, {}),
        ("30m F7+ATR1.05", "30m", 7.0, {"atr_min_ratio": 1.05}),
    ]
    results = []
    for label, tf, factor, kwargs in scenarios:
        candles = load_tf(tf)
        o = [c["o"] for c in candles]
        h = [c["h"] for c in candles]
        l = [c["l"] for c in candles]
        c = [c["c"] for c in candles]
        st = super_trend(o, h, l, c, periods=ST_P, multiplier=factor,
                         src="hl2", change_atr=True)
        atr = ta_atr(h, l, c, ST_P)
        adx = ta_adx(h, l, c, ADX_LEN)
        er = er_series(c, ER_LEN)
        candidates = build_candidates(candles, st, atr, adx, er, "A", 0,
                                      stop_atr=3.0, **kwargs)
        sim = simulate_st_trail(candles, candidates, st, BASE_FEE_BPS, BASE_SLIP_BPS)
        trades = [t for t in sim["trades"] if t["entry_ts"] >= _ms(2021)]
        side_num = {"long": 1, "short": -1}
        rows = []
        for t in trades:
            i, side = t["entry_i"], side_num[t["side"]]
            a = atr[i] or 0.0
            if a <= 0:
                continue
            end = min(len(candles), i + 21)
            mfe = mae = 0.0
            bars_to_mfe = 0
            for j in range(i + 1, end):
                fav = (h[j] - t["entry"]) if side == 1 else (t["entry"] - l[j])
                adv = (t["entry"] - l[j]) if side == 1 else (h[j] - t["entry"])
                if fav > mfe:
                    mfe = fav
                    bars_to_mfe = j - i
                mae = max(mae, adv)
            ret_atr = (t["exit"] - t["entry"]) * side / a
            mfe_atr, mae_atr = mfe / a, mae / a
            capture = max(0.0, min(1.0, ret_atr / mfe_atr)) if mfe_atr > 0 else 0.0
            missed = max(0.0, mfe_atr - ret_atr)
            rows.append({
                "mfe20_atr": mfe_atr, "mae20_atr": mae_atr,
                "ret_atr": ret_atr, "capture": capture, "missed_atr": missed,
                "bars_to_mfe": bars_to_mfe, "hold": t["exit_i"] - t["entry_i"],
                "win": t["pnl"] > 0,
            })
        n = max(1, len(rows))
        wins = [r for r in rows if r["win"]]
        results.append({
            "label": label, "tf": tf, "factor": factor, "filter": kwargs,
            "n": len(rows),
            "avg_mfe20_atr": sum(r["mfe20_atr"] for r in rows) / n,
            "avg_mae20_atr": sum(r["mae20_atr"] for r in rows) / n,
            "avg_ret_atr": sum(r["ret_atr"] for r in rows) / n,
            "avg_capture": sum(r["capture"] for r in rows) / n,
            "avg_capture_win": sum(r["capture"] for r in wins) / max(1, len(wins)),
            "avg_missed_atr": sum(r["missed_atr"] for r in rows) / n,
            "avg_bars_to_mfe": sum(r["bars_to_mfe"] for r in rows) / n,
            "avg_hold": sum(r["hold"] for r in rows) / n,
        })

    os.makedirs(CACHE_DIR, exist_ok=True)
    rp = os.path.join(CACHE_DIR, "delay_report.json")
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump({"source": "data-api.binance.vision BTCUSDT spot",
                   "study": "entry delay / trend capture diagnostics",
                   "horizon": 20, "results": results}, fh,
                  ensure_ascii=False, indent=2)
    print("\n=== 入场延迟 / 趋势捕获率诊断（入场后20根） ===")
    print(f"{'组合':<18} {'笔数':>6} {'MFE20ATR':>9} {'MAE20ATR':>9} "
          f"{'实际收益ATR':>11} {'捕获率':>7} {'盈利单捕获':>9} {'错失ATR':>8} "
          f"{'到达MFE根数':>10} {'持仓根数':>8}")
    for r in results:
        print(f"{r['label']:<18} {r['n']:>6} {r['avg_mfe20_atr']:>9.2f} "
              f"{r['avg_mae20_atr']:>9.2f} {r['avg_ret_atr']:>11.2f} "
              f"{r['avg_capture']:>7.3f} {r['avg_capture_win']:>9.3f} "
              f"{r['avg_missed_atr']:>8.2f} {r['avg_bars_to_mfe']:>10.1f} "
              f"{r['avg_hold']:>8.1f}")
    print(f"\n[done] delay report: {rp}")


def run_1h_full_matrix() -> None:
    """1h 全矩阵：ST周期 × Factor × ATR ratio，固定A0/3ATR止损/ST跟随。"""
    tf, stop_atr = "1h", 3.0
    periods = [7, 8, 9, 10, 11, 12, 14, 15]
    multipliers = [2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0, 7.0]
    ratios = [0.0, 1.0, 1.05, 1.1, 1.2]
    candles = load_tf(tf)
    o = [c["o"] for c in candles]
    h = [c["h"] for c in candles]
    l = [c["l"] for c in candles]
    c = [c["c"] for c in candles]
    adx = ta_adx(h, l, c, ADX_LEN)
    er = er_series(c, ER_LEN)
    results = []
    for period in periods:
        atr = ta_atr(h, l, c, period)
        for mult in multipliers:
            st = super_trend(o, h, l, c, periods=period, multiplier=mult,
                             src="hl2", change_atr=True)
            all_candidates = build_candidates(candles, st, atr, adx, er, "A", 0,
                                              stop_atr=stop_atr)
            for ratio in ratios:
                candidates = all_candidates if ratio == 0 else build_candidates(
                    candles, st, atr, adx, er, "A", 0, stop_atr=stop_atr,
                    atr_min_ratio=ratio)
                base = simulate_st_trail(candles, candidates, st, BASE_FEE_BPS,
                                         BASE_SLIP_BPS)
                stress = simulate_st_trail(candles, candidates, st, STRESS_FEE_BPS,
                                           STRESS_SLIP_BPS)
                label = f"ST{period}x{mult:g}/R{ratio:g}"
                results.append({
                    "period": period, "multiplier": mult, "ratio": ratio,
                    "label": label, "flips": len(st["flips"]),
                    "candidates": len(candidates),
                    "segments": {seg[0]: stat_block(base["trades"], st["flips"],
                                                    candles, base["equity"], seg)
                                 for seg in SEGMENTS},
                    "all": stat_block(base["trades"], st["flips"], candles,
                                      base["equity"], ("all", 2021, 2100)),
                    "stress": {seg[0]: stat_block(stress["trades"], st["flips"],
                                                  candles, stress["equity"], seg)
                               for seg in SEGMENTS},
                    "gross_pf": _gross_pf(base["trades"]),
                    "fees": sum(t["fees"] for t in base["trades"]),
                    "max_dd_pct": base["max_dd_pct"],
                })

    os.makedirs(CACHE_DIR, exist_ok=True)
    rp = os.path.join(CACHE_DIR, "full_matrix_1h_report.json")
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump({
            "source": "data-api.binance.vision BTCUSDT spot",
            "study": "1h ST period x factor x ATR ratio matrix; A0 + 3.0ATR stop + ST trail",
            "params": {"stop_atr": stop_atr, "risk_pct": RISK_PCT,
                       "leverage_cap": LEVERAGE_CAP, "fee_bps": BASE_FEE_BPS,
                       "slip_bps": BASE_SLIP_BPS},
            "grid": {"periods": periods, "multipliers": multipliers,
                     "atr_ratios": ratios},
            "results": results,
        }, fh, ensure_ascii=False, indent=2)

    def print_top(title: str, key, limit: int = 15) -> None:
        print(f"\n-- {title} --")
        print(f"{'组合':<22} {'笔数':>6} {'净PF':>6} {'DEV':>6} {'VAL':>6} "
              f"{'OOS':>6} {'收益%':>8} {'OOSDD%':>7} {'连损':>5} "
              f"{'费用':>7} {'压力OOS':>7}")
        for r in sorted(results, key=key, reverse=True)[:limit]:
            a, s = r["all"], r["segments"]
            print(f"{r['label']:<22} {a['n']:>6} {fmt_pf(a['pf']):>6} "
                  f"{fmt_pf(s['dev']['pf']):>6} {fmt_pf(s['val']['pf']):>6} "
                  f"{fmt_pf(s['oos']['pf']):>6} {a['ret_pct']:>+8.1f} "
                  f"{s['oos']['max_dd_pct']:>7.2f} {a['max_stop_streak']:>5} "
                  f"{r['fees']:>7.0f} {fmt_pf(r['stress']['oos']['pf']):>7}")

    print(f"\n=== 1h 全参数矩阵（{len(results)} 组） ===")
    print_top("按净PF排序", lambda r: r["all"]["pf"])
    print_top("按OOS PF排序", lambda r: r["segments"]["oos"]["pf"])
    print_top("按压力OOS排序", lambda r: r["stress"]["oos"]["pf"])
    print(f"\n[done] full matrix report: {rp}")


def run_symbol_matrix(symbol: str, tf: str, periods, multipliers, ratios,
                      report_name: str) -> list[dict]:
    """单周期矩阵：ST周期 × Factor × ATR ratio，固定A0/3ATR止损/ST跟随。"""
    stop_atr = 3.0
    candles = load_tf(tf, symbol)
    o = [c["o"] for c in candles]
    h = [c["h"] for c in candles]
    l = [c["l"] for c in candles]
    c = [c["c"] for c in candles]
    adx = ta_adx(h, l, c, ADX_LEN)
    er = er_series(c, ER_LEN)
    results = []
    for period in periods:
        atr = ta_atr(h, l, c, period)
        for mult in multipliers:
            st = super_trend(o, h, l, c, periods=period, multiplier=mult,
                             src="hl2", change_atr=True)
            all_candidates = build_candidates(candles, st, atr, adx, er, "A", 0,
                                              stop_atr=stop_atr)
            for ratio in ratios:
                candidates = all_candidates if ratio == 0 else build_candidates(
                    candles, st, atr, adx, er, "A", 0, stop_atr=stop_atr,
                    atr_min_ratio=ratio)
                base = simulate_st_trail(candles, candidates, st, BASE_FEE_BPS,
                                         BASE_SLIP_BPS)
                stress = simulate_st_trail(candles, candidates, st, STRESS_FEE_BPS,
                                           STRESS_SLIP_BPS)
                results.append({
                    "symbol": symbol, "tf": tf,
                    "period": period, "multiplier": mult, "ratio": ratio,
                    "label": f"ST{period}x{mult:g}/R{ratio:g}",
                    "flips": len(st["flips"]), "candidates": len(candidates),
                    "segments": {seg[0]: stat_block(base["trades"], st["flips"],
                                                    candles, base["equity"], seg)
                                 for seg in SEGMENTS},
                    "all": stat_block(base["trades"], st["flips"], candles,
                                      base["equity"], ("all", 2021, 2100)),
                    "stress": {seg[0]: stat_block(stress["trades"], st["flips"],
                                                  candles, stress["equity"], seg)
                               for seg in SEGMENTS},
                    "gross_pf": _gross_pf(base["trades"]),
                    "fees": sum(t["fees"] for t in base["trades"]),
                    "max_dd_pct": base["max_dd_pct"],
                })

    os.makedirs(CACHE_DIR, exist_ok=True)
    rp = os.path.join(CACHE_DIR, report_name)
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump({
            "source": f"data-api.binance.vision {symbol} spot",
            "study": f"{symbol} {tf} ST period x factor x ATR ratio matrix; "
                     "A0 + 3.0ATR stop + ST trail",
            "params": {"stop_atr": stop_atr, "risk_pct": RISK_PCT,
                       "leverage_cap": LEVERAGE_CAP, "fee_bps": BASE_FEE_BPS,
                       "slip_bps": BASE_SLIP_BPS},
            "grid": {"periods": periods, "multipliers": multipliers,
                     "atr_ratios": ratios},
            "results": results,
        }, fh, ensure_ascii=False, indent=2)
    return results


def run_eth_matrix() -> None:
    """ETH 15m/30m/1h 同网格矩阵；网格聚焦 BTC 已证明有效的 ST 优势区。"""
    symbol = "ETHUSDT"
    periods = [10, 11, 12, 14, 15]
    multipliers = [3.0, 4.0, 5.0, 6.0, 7.0]
    ratios = [0.0, 1.0, 1.05, 1.1, 1.2]
    all_results = []
    for tf in ("15m", "30m", "1h"):
        res = run_symbol_matrix(symbol, tf, periods, multipliers, ratios,
                                f"eth_matrix_{tf}_report.json")
        all_results.extend(res)
        print(f"\n=== {symbol} {tf} 矩阵 TOP10 按净PF（共 {len(res)} 组） ===")
        print(f"{'组合':<22} {'笔数':>6} {'毛PF':>6} {'净PF':>6} {'DEV':>6} "
              f"{'VAL':>6} {'OOS':>6} {'收益%':>8} {'OOSDD%':>7} {'连损':>5} "
              f"{'费用':>7} {'压力OOS':>7}")
        for r in sorted(res, key=lambda x: -x["all"]["pf"])[:10]:
            a, s = r["all"], r["segments"]
            print(f"{r['label']:<22} {a['n']:>6} {fmt_pf(r['gross_pf']):>6} "
                  f"{fmt_pf(a['pf']):>6} {fmt_pf(s['dev']['pf']):>6} "
                  f"{fmt_pf(s['val']['pf']):>6} {fmt_pf(s['oos']['pf']):>6} "
                  f"{a['ret_pct']:>+8.1f} {s['oos']['max_dd_pct']:>7.2f} "
                  f"{a['max_stop_streak']:>5} {r['fees']:>7.0f} "
                  f"{fmt_pf(r['stress']['oos']['pf']):>7}")

    rp = os.path.join(CACHE_DIR, "eth_matrix_summary.json")
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump({
            "source": f"data-api.binance.vision {symbol} spot",
            "study": "ETH 15m/30m/1h ST period x factor x ATR ratio matrix",
            "grid": {"periods": periods, "multipliers": multipliers,
                     "atr_ratios": ratios},
            "results": all_results,
        }, fh, ensure_ascii=False, indent=2)
    print(f"\n[done] ETH matrix summary: {rp}")


def simulate_tp_sl(candles, candidates, st, fee_bps: float, slip_bps: float,
                   tp1_r: float | None = None, tp1_ratio: float = 0.0,
                   tp_full_r: float | None = None,
                   use_trail: bool = True) -> dict:
    """A0 入场 + 初始硬止损 + 可选分批/固定止盈 + 剩余仓位 ST 跟随。

    - tp1_r=None 且 tp_full_r=None 且 use_trail=True 时与 simulate_st_trail 等价；
    - tp1_r：价格到 tp1_r×止损距离时平掉 tp1_ratio% 仓位，止损移到开仓价（保本）；
    - tp_full_r：价格到 tp_full_r×止损距离时剩余全平；
    - use_trail：轨道越过当前止损后改跟轨道；False 则剩余只等 ST 翻转/硬止损。
    同根先判止损再判止盈；翻转平仓后同根允许反向开新仓（与基线一致）。
    """
    fee = fee_bps / 10_000.0
    slip = slip_bps / 10_000.0
    by_entry: dict[int, list[dict]] = {}
    for c in candidates:
        by_entry.setdefault(c["entry_i"], []).append(c)

    pos = None
    realized = 0.0
    equity = [INIT]
    trades = []

    def leg(i: int, reason: str, raw_px: float, qty: float) -> None:
        nonlocal realized, pos
        if pos is None or qty <= 0:
            return
        side = pos["side"]
        fill = raw_px * (1 - slip) if side == 1 else raw_px * (1 + slip)
        gross = (fill - pos["entry_fill"]) * qty * side
        out_fee = fill * qty * fee
        in_fee = pos["entry_fee"] * (qty / pos["qty0"])
        net = gross - out_fee - in_fee
        realized += net
        pos["qty"] -= qty
        pos["pnl"] += net
        pos["gross_sum"] += gross
        pos["fee_sum"] += out_fee + in_fee
        pos["legs"].append(reason)
        if pos["qty"] <= pos["qty0"] * 1e-9:
            trades.append({
                "flip_i": pos["flip_i"], "entry_i": pos["entry_i"], "exit_i": i,
                "flip_ts": candles[pos["flip_i"]]["ts"],
                "entry_ts": candles[pos["entry_i"]]["ts"],
                "exit_ts": candles[i]["ts"],
                "side": "long" if side == 1 else "short",
                "reason": "+".join(pos["legs"]), "pnl": pos["pnl"],
                "gross": pos["gross_sum"], "fees": pos["fee_sum"],
                "entry": pos["entry"], "exit": raw_px,
                "bars_since_flip": i - pos["flip_i"],
            })
            pos = None

    for i, bar in enumerate(candles):
        if pos is not None and i > pos["entry_i"]:
            side = pos["side"]
            line = st["up"][i] if side == 1 else st["dn"][i]
            if use_trail and line is not None and (
                (side == 1 and line > pos["stop"]) or
                (side == -1 and line < pos["stop"])
            ):
                pos["stop"] = line
                pos["trailing"] = True
            stop_hit = (bar["l"] <= pos["stop"]) if side == 1 else (bar["h"] >= pos["stop"])
            if stop_hit:
                label = "st_trail" if pos["trailing"] else ("be" if pos["be"] else "stop")
                leg(i, label, pos["stop"], pos["qty"])

        if pos is not None and i > pos["entry_i"]:
            side = pos["side"]
            if tp1_r is not None and not pos["tp1_done"]:
                t1 = pos["entry"] + side * tp1_r * pos["dist"]
                reached = (bar["h"] >= t1) if side == 1 else (bar["l"] <= t1)
                if reached:
                    part = pos["qty0"] * tp1_ratio / 100.0
                    leg(i, "tp1", t1, part)
                    if pos is not None:
                        pos["tp1_done"] = True
                        pos["stop"] = (max(pos["stop"], pos["entry_fill"]) if side == 1
                                       else min(pos["stop"], pos["entry_fill"]))
                        pos["be"] = True
            if pos is not None and tp_full_r is not None:
                t2 = pos["entry"] + side * tp_full_r * pos["dist"]
                reached = (bar["h"] >= t2) if side == 1 else (bar["l"] <= t2)
                if reached:
                    leg(i, "tp", t2, pos["qty"])
            if pos is not None and st["trend"][i] not in (None, side):
                leg(i, "st_flip", bar["c"], pos["qty"])

        for cand in by_entry.get(i, []):
            if pos is not None:
                break
            eq = INIT + realized
            risk = eq * RISK_PCT
            dist = abs(cand["entry"] - cand["stop"])
            qty = min(risk / dist, eq * LEVERAGE_CAP / cand["entry"])
            if qty <= 0:
                continue
            side = cand["side"]
            entry_fill = cand["entry"] * (1 + slip if side == 1 else 1 - slip)
            pos = {
                "flip_i": cand["flip_i"], "entry_i": i, "side": side,
                "entry": cand["entry"], "entry_fill": entry_fill,
                "qty": qty, "qty0": qty, "dist": dist,
                "stop": cand["stop"], "entry_fee": qty * cand["entry"] * fee,
                "tp1_done": False, "be": False, "trailing": False,
                "legs": [], "pnl": 0.0, "gross_sum": 0.0, "fee_sum": 0.0,
            }

        float_pnl = 0.0
        if pos is not None:
            float_pnl = (bar["c"] - pos["entry_fill"]) * pos["qty"] * pos["side"]
        equity.append(INIT + realized + float_pnl)

    if pos is not None:
        leg(len(candles) - 1, "end", candles[-1]["c"], pos["qty"])
        equity[-1] = INIT + realized

    peak = INIT
    max_dd = 0.0
    for eq in equity:
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak if peak > 0 else 0.0)
    return {"trades": trades, "equity": equity, "max_dd_pct": max_dd * 100}


def _avg_hold(trades: list[dict]) -> float:
    if not trades:
        return 0.0
    return sum(t["exit_i"] - t["entry_i"] for t in trades) / len(trades)


def run_tp_sl_study() -> None:
    """出场研究：3ATR 硬止损 × 固定止盈/分批止盈/保本 × ST 跟随，BTC/ETH 1h。"""
    configs = [
        ("BTCUSDT", "BTC低滞后", 10, 3.5, 1.05),
        ("BTCUSDT", "BTC收益向", 11, 6.0, 0.0),
        ("BTCUSDT", "BTC无过滤", 10, 3.5, 0.0),
        ("ETHUSDT", "ETH质量向", 15, 6.0, 1.2),
    ]
    stops = [2.5, 3.0, 3.5]
    variants = [
        ("基线 无TP+跟随", {}),
        ("纯反向平(不跟轨)", dict(use_trail=False)),
        ("全平1R", dict(tp_full_r=1.0)),
        ("全平1.5R", dict(tp_full_r=1.5)),
        ("全平2R", dict(tp_full_r=2.0)),
        ("全平2.5R", dict(tp_full_r=2.5)),
        ("全平3R", dict(tp_full_r=3.0)),
        ("全平4R", dict(tp_full_r=4.0)),
        ("TP1 1R/30%+保本", dict(tp1_r=1.0, tp1_ratio=30.0)),
        ("TP1 1.5R/30%+保本", dict(tp1_r=1.5, tp1_ratio=30.0)),
        ("TP1 2R/30%+保本", dict(tp1_r=2.0, tp1_ratio=30.0)),
        ("TP1 1.5R/50%+保本", dict(tp1_r=1.5, tp1_ratio=50.0)),
        ("TP1 2R/50%+保本", dict(tp1_r=2.0, tp1_ratio=50.0)),
        ("TP1 1.5R/30%+不跟轨", dict(tp1_r=1.5, tp1_ratio=30.0, use_trail=False)),
        ("TP1 2R/30%+不跟轨", dict(tp1_r=2.0, tp1_ratio=30.0, use_trail=False)),
    ]

    rows: list[dict] = []
    for symbol, tag, period, mult, ratio in configs:
        candles = load_tf("1h", symbol)
        o = [c["o"] for c in candles]
        h = [c["h"] for c in candles]
        lo_ = [c["l"] for c in candles]
        cl = [c["c"] for c in candles]
        st = super_trend(o, h, lo_, cl, periods=period, multiplier=mult,
                         src="hl2", change_atr=True)
        atr = ta_atr(h, lo_, cl, period)
        adx = ta_adx(h, lo_, cl, ADX_LEN)
        er = er_series(cl, ER_LEN)

        for stop_atr in stops:
            cands = build_candidates(candles, st, atr, adx, er, "A", 0,
                                     stop_atr=stop_atr, atr_min_ratio=ratio)
            for vname, kw in variants:
                base = simulate_tp_sl(candles, cands, st, BASE_FEE_BPS,
                                      BASE_SLIP_BPS, **kw)
                stress = simulate_tp_sl(candles, cands, st, STRESS_FEE_BPS,
                                        STRESS_SLIP_BPS, **kw)
                if (stop_atr == 3.0 and not kw):
                    ref = simulate_st_trail(candles, cands, st, BASE_FEE_BPS,
                                            BASE_SLIP_BPS)
                    d = abs(sum(t["pnl"] for t in base["trades"])
                            - sum(t["pnl"] for t in ref["trades"]))
                    if d > 1e-6 or len(base["trades"]) != len(ref["trades"]):
                        raise RuntimeError(f"TP/SL 引擎与基线不一致: {tag} diff={d}")
                    print(f"[自检] {tag} 基线复算一致 "
                          f"({len(ref['trades'])} 笔, diff={d:.2e})")
                segs = {s[0]: stat_block(base["trades"], st["flips"], candles,
                                        base["equity"], s) for s in SEGMENTS}
                allb = stat_block(base["trades"], st["flips"], candles,
                                  base["equity"], ("all", 2021, 2100))
                rows.append({
                    "symbol": symbol, "tag": tag, "st": f"{period}x{mult:g}",
                    "ratio": ratio, "stop_atr": stop_atr, "variant": vname,
                    "candidates": len(cands), "n": allb["n"],
                    "ret_pct": allb["ret_pct"], "pf": allb["pf"],
                    "dev": segs["dev"]["pf"], "val": segs["val"]["pf"],
                    "oos": segs["oos"]["pf"], "oos_n": segs["oos"]["n"],
                    "oos_dd": segs["oos"]["max_dd_pct"],
                    "winrate": allb["winrate"],
                    "avg_hold": _avg_hold(base["trades"]),
                    "max_stop_streak": allb["max_stop_streak"],
                    "stress_oos": stat_block(stress["trades"], st["flips"], candles,
                                             stress["equity"], SEGMENTS[2])["pf"],
                })

    for symbol, tag, period, mult, ratio in configs:
        sub = [r for r in rows if r["tag"] == tag]
        print(f"\n=== {symbol} 1h ST{period}x{mult:g}/R{ratio:g} 出场网格"
              f"（{len(sub)} 组，按 OOS PF 排序） ===")
        print(f"{'变体':<22} {'止损':>5} {'笔数':>6} {'收益%':>8} {'净PF':>6} "
              f"{'DEV':>6} {'VAL':>6} {'OOS':>6} {'OOSDD%':>7} {'胜率%':>6} "
              f"{'持仓根':>6} {'连损':>5} {'压力OOS':>7}")
        for r in sorted(sub, key=lambda x: -x["oos"])[:15]:
            print(f"{r['variant']:<22} {r['stop_atr']:>5.1f} {r['n']:>6} "
                  f"{r['ret_pct']:>+8.1f} {fmt_pf(r['pf']):>6} {fmt_pf(r['dev']):>6} "
                  f"{fmt_pf(r['val']):>6} {fmt_pf(r['oos']):>6} {r['oos_dd']:>7.2f} "
                  f"{r['winrate']:>6.1f} {r['avg_hold']:>6.0f} "
                  f"{r['max_stop_streak']:>5} {fmt_pf(r['stress_oos']):>7}")

    os.makedirs(CACHE_DIR, exist_ok=True)
    rp = os.path.join(CACHE_DIR, "tp_sl_report.json")
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump({
            "source": "data-api.binance.vision spot 1h",
            "study": "A0 entry; stop_atr x TP structure (none/full/partial+BE) x ST trail",
            "params": {"risk_pct": RISK_PCT, "leverage_cap": LEVERAGE_CAP,
                       "fee_bps": BASE_FEE_BPS, "slip_bps": BASE_SLIP_BPS,
                       "stress_fee_bps": STRESS_FEE_BPS,
                       "stress_slip_bps": STRESS_SLIP_BPS},
            "grid": {"configs": [c[1] for c in configs], "stops": stops,
                     "variants": [v[0] for v in variants]},
            "results": rows,
        }, fh, ensure_ascii=False, indent=2)
    print(f"\n[done] TP/SL report: {rp}")


def simulate_pct_exits(candles, candidates, st, fee_bps: float, slip_bps: float,
                       stop_pct: float | None = None,
                       legs: list[tuple[float, float]] | None = None,
                       move_be: bool = True,
                       use_trail: bool = True,
                       stop_atr: float | None = None,
                       no_stop: bool = False) -> dict:
    """固定百分比（或 ATR 倍数）硬止损 + 多档 % 止盈 + 剩余出场方式。

    stop_pct：硬止损按入场价百分比；stop_atr：硬止损按 stop_atr × ATR（二选一）。
    legs=[(浮盈%, 平仓比例%), ...] 依序触发，触发后 remaining 继续跑；
    move_be=True 时首档止盈后止损移到开仓价；use_trail=False 则剩余只等反向翻转。
    no_stop=True 时取消初始硬止损（仓位仍按 stop_atr/stop_pct 的名义风险折算），
    只剩「首档止盈后保本」和「反向翻转平仓」两道出场。
    同根先判止损、再按顺序判各档止盈。
    """
    fee = fee_bps / 10_000.0
    slip = slip_bps / 10_000.0
    plans = list(legs or [])
    by_entry: dict[int, list[dict]] = {}
    for c in candidates:
        by_entry.setdefault(c["entry_i"], []).append(c)

    pos = None
    realized = 0.0
    equity = [INIT]
    trades = []

    def leg(i: int, reason: str, raw_px: float, qty: float) -> None:
        nonlocal realized, pos
        if pos is None or qty <= 0:
            return
        qty = min(qty, pos["qty"])
        side = pos["side"]
        fill = raw_px * (1 - slip) if side == 1 else raw_px * (1 + slip)
        gross = (fill - pos["entry_fill"]) * qty * side
        out_fee = fill * qty * fee
        in_fee = pos["entry_fee"] * (qty / pos["qty0"])
        net = gross - out_fee - in_fee
        realized += net
        pos["qty"] -= qty
        pos["pnl"] += net
        pos["gross_sum"] += gross
        pos["fee_sum"] += out_fee + in_fee
        pos["legs"].append(reason)
        if pos["qty"] <= pos["qty0"] * 1e-9:
            trades.append({
                "flip_i": pos["flip_i"], "entry_i": pos["entry_i"], "exit_i": i,
                "flip_ts": candles[pos["flip_i"]]["ts"],
                "entry_ts": candles[pos["entry_i"]]["ts"],
                "exit_ts": candles[i]["ts"],
                "side": "long" if side == 1 else "short",
                "reason": "+".join(pos["legs"]), "pnl": pos["pnl"],
                "gross": pos["gross_sum"], "fees": pos["fee_sum"],
                "entry": pos["entry"], "exit": raw_px,
                "bars_since_flip": i - pos["flip_i"],
            })
            pos = None

    for i, bar in enumerate(candles):
        if pos is not None and i > pos["entry_i"] and pos["stop"] is not None:
            side = pos["side"]
            line = st["up"][i] if side == 1 else st["dn"][i]
            if use_trail and line is not None and (
                (side == 1 and line > pos["stop"]) or
                (side == -1 and line < pos["stop"])
            ):
                pos["stop"] = line
                pos["trailing"] = True
            stop_hit = (bar["l"] <= pos["stop"]) if side == 1 else (bar["h"] >= pos["stop"])
            if stop_hit:
                label = "st_trail" if pos["trailing"] else ("be" if pos["be"] else "stop")
                leg(i, label, pos["stop"], pos["qty"])

        if pos is not None and i > pos["entry_i"]:
            side = pos["side"]
            while pos["leg_i"] < len(plans):
                trig, ratio = plans[pos["leg_i"]]
                target = pos["entry"] * (1 + side * trig / 100.0)
                reached = (bar["h"] >= target) if side == 1 else (bar["l"] <= target)
                if not reached:
                    break
                pos["leg_i"] += 1
                leg(i, f"tp{pos['leg_i']}", target, pos["qty0"] * ratio / 100.0)
                if pos is None:
                    break
                if move_be and pos["leg_i"] == 1:
                    base_stop = pos["stop"] if pos["stop"] is not None else pos["entry_fill"]
                    pos["stop"] = (max(base_stop, pos["entry_fill"]) if side == 1
                                   else min(base_stop, pos["entry_fill"]))
                    pos["be"] = True
            if pos is not None and st["trend"][i] not in (None, side):
                leg(i, "st_flip", bar["c"], pos["qty"])

        for cand in by_entry.get(i, []):
            if pos is not None:
                break
            eq = INIT + realized
            if stop_atr is not None:
                if cand.get("atr") is None:
                    continue
                dist = stop_atr * cand["atr"]
            else:
                dist = cand["entry"] * (stop_pct or 0.0) / 100.0
            if dist <= 0:
                continue
            qty = min(eq * RISK_PCT / dist, eq * LEVERAGE_CAP / cand["entry"])
            if qty <= 0:
                continue
            side = cand["side"]
            entry_fill = cand["entry"] * (1 + slip if side == 1 else 1 - slip)
            pos = {
                "flip_i": cand["flip_i"], "entry_i": i, "side": side,
                "entry": cand["entry"], "entry_fill": entry_fill,
                "qty": qty, "qty0": qty,
                "stop": (None if no_stop else
                         (cand["entry"] - dist if side == 1 else cand["entry"] + dist)),
                "entry_fee": qty * cand["entry"] * fee,
                "leg_i": 0, "be": False, "trailing": False,
                "legs": [], "pnl": 0.0, "gross_sum": 0.0, "fee_sum": 0.0,
            }

        float_pnl = 0.0
        if pos is not None:
            float_pnl = (bar["c"] - pos["entry_fill"]) * pos["qty"] * pos["side"]
        equity.append(INIT + realized + float_pnl)

    if pos is not None:
        leg(len(candles) - 1, "end", candles[-1]["c"], pos["qty"])
        equity[-1] = INIT + realized

    peak = INIT
    max_dd = 0.0
    for eq in equity:
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak if peak > 0 else 0.0)
    return {"trades": trades, "equity": equity, "max_dd_pct": max_dd * 100}


def run_pct_exit_study() -> None:
    """实盘百分比口径：固定 % 止损 × 多档 % 止盈（含 tp1 30%）× ST 跟随。"""
    configs = [
        ("BTCUSDT", "BTC低滞后", 10, 3.5, 1.05),
        ("BTCUSDT", "BTC收益向", 11, 6.0, 0.0),
        ("ETHUSDT", "ETH质量向", 15, 6.0, 1.2),
    ]
    stops = [2.0, 3.0, 4.0]
    plans = [
        ("无TP+跟随", []),
        ("无TP+纯反向", []),
        ("TP1 1%/30%", [(1.0, 30.0)]),
        ("TP1 1%/50%", [(1.0, 50.0)]),
        ("TP1 1%/100%", [(1.0, 100.0)]),
        ("TP1 1.5%/30%", [(1.5, 30.0)]),
        ("TP1 2%/30%", [(2.0, 30.0)]),
        ("TP1 2%/50%", [(2.0, 50.0)]),
        ("TP1 3%/30%", [(3.0, 30.0)]),
        ("TP1 1%+TP2 2% (30/40)", [(1.0, 30.0), (2.0, 40.0)]),
        ("TP1 1%+TP2 3% (30/40)", [(1.0, 30.0), (3.0, 40.0)]),
        ("TP1 1.5%+TP2 3% (30/40)", [(1.5, 30.0), (3.0, 40.0)]),
    ]

    rows: list[dict] = []
    for symbol, tag, period, mult, ratio in configs:
        candles = load_tf("1h", symbol)
        o = [c["o"] for c in candles]
        h = [c["h"] for c in candles]
        lo_ = [c["l"] for c in candles]
        cl = [c["c"] for c in candles]
        st = super_trend(o, h, lo_, cl, periods=period, multiplier=mult,
                         src="hl2", change_atr=True)
        atr = ta_atr(h, lo_, cl, period)
        adx = ta_adx(h, lo_, cl, ADX_LEN)
        er = er_series(cl, ER_LEN)
        cands = build_candidates(candles, st, atr, adx, er, "A", 0,
                                 stop_atr=3.0, atr_min_ratio=ratio)
        med_r = sorted(abs(c["entry"] - c["stop"]) / c["entry"] * 100
                       for c in cands)
        med_r = med_r[len(med_r) // 2] if med_r else 0.0

        for stop_pct in stops:
            for pname, legs in plans:
                # 「纯反向」档不跟轨，其余均开启 ST 跟随
                use_trail = pname != "无TP+纯反向"
                base = simulate_pct_exits(candles, cands, st, BASE_FEE_BPS,
                                          BASE_SLIP_BPS, stop_pct=stop_pct,
                                          legs=legs, use_trail=use_trail)
                stress = simulate_pct_exits(candles, cands, st, STRESS_FEE_BPS,
                                            STRESS_SLIP_BPS, stop_pct=stop_pct,
                                            legs=legs, use_trail=use_trail)
                segs = {s[0]: stat_block(base["trades"], st["flips"], candles,
                                        base["equity"], s) for s in SEGMENTS}
                allb = stat_block(base["trades"], st["flips"], candles,
                                  base["equity"], ("all", 2021, 2100))
                rows.append({
                    "symbol": symbol, "tag": tag, "st": f"{period}x{mult:g}",
                    "ratio": ratio, "median_1r_pct": med_r,
                    "stop_pct": stop_pct, "variant": pname,
                    "n": allb["n"], "ret_pct": allb["ret_pct"], "pf": allb["pf"],
                    "dev": segs["dev"]["pf"], "val": segs["val"]["pf"],
                    "oos": segs["oos"]["pf"], "oos_dd": segs["oos"]["max_dd_pct"],
                    "winrate": allb["winrate"],
                    "avg_hold": _avg_hold(base["trades"]),
                    "max_stop_streak": allb["max_stop_streak"],
                    "stress_oos": stat_block(stress["trades"], st["flips"], candles,
                                             stress["equity"], SEGMENTS[2])["pf"],
                })

    for symbol, tag, period, mult, ratio in configs:
        sub = [r for r in rows if r["tag"] == tag]
        print(f"\n=== {symbol} 1h ST{period}x{mult:g}/R{ratio:g} 百分比出场网格"
              f"（3ATR止损中位≈{sub[0]['median_1r_pct']:.2f}%，按 OOS PF 排序） ===")
        print(f"{'变体':<22} {'硬止损%':>7} {'笔数':>5} {'收益%':>8} {'净PF':>6} "
              f"{'DEV':>6} {'VAL':>6} {'OOS':>6} {'OOSDD%':>7} {'胜率%':>6} "
              f"{'持仓根':>6} {'连损':>5} {'压力OOS':>7}")
        for r in sorted(sub, key=lambda x: -x["oos"])[:14]:
            print(f"{r['variant']:<22} {r['stop_pct']:>7.1f} {r['n']:>5} "
                  f"{r['ret_pct']:>+8.1f} {fmt_pf(r['pf']):>6} {fmt_pf(r['dev']):>6} "
                  f"{fmt_pf(r['val']):>6} {fmt_pf(r['oos']):>6} {r['oos_dd']:>7.2f} "
                  f"{r['winrate']:>6.1f} {r['avg_hold']:>6.0f} "
                  f"{r['max_stop_streak']:>5} {fmt_pf(r['stress_oos']):>7}")

    os.makedirs(CACHE_DIR, exist_ok=True)
    rp = os.path.join(CACHE_DIR, "pct_exit_report.json")
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump({
            "source": "data-api.binance.vision spot 1h",
            "study": "A0 entry; fixed % hard stop x % TP legs (live-style) x ST trail",
            "params": {"risk_pct": RISK_PCT, "leverage_cap": LEVERAGE_CAP,
                       "fee_bps": BASE_FEE_BPS, "slip_bps": BASE_SLIP_BPS,
                       "stress_fee_bps": STRESS_FEE_BPS,
                       "stress_slip_bps": STRESS_SLIP_BPS},
            "grid": {"configs": [c[1] for c in configs], "stops_pct": stops,
                     "plans": [p[0] for p in plans]},
            "results": rows,
        }, fh, ensure_ascii=False, indent=2)
    print(f"\n[done] pct exit report: {rp}")


def run_pct_reverse_study() -> None:
    """实盘口径：固定%止盈分批 + 保本 + 剩余只等反向平仓（不跟轨）。"""
    configs = [
        ("BTCUSDT", "BTC低滞后", 10, 3.5, 1.05),
        ("BTCUSDT", "BTC收益向", 11, 6.0, 0.0),
        ("ETHUSDT", "ETH质量向", 15, 6.0, 1.2),
    ]
    # (标签, 止损%（None 表示用 ATR）, ATR 倍数)
    stops = [
        ("3.0ATR", None, 3.0),
        ("3.5ATR", None, 3.5),
        ("2%", 2.0, None),
        ("3%", 3.0, None),
    ]
    # (标签, legs, 保本, 剩余是否跟轨)
    plans = [
        ("无TP 纯反向", [], False, False),
        ("TP1 1%/30%", [(1.0, 30.0)], True, False),
        ("TP1 1.5%/30%", [(1.5, 30.0)], True, False),
        ("TP1 2%/30%", [(2.0, 30.0)], True, False),
        ("TP1 3%/30%", [(3.0, 30.0)], True, False),
        ("TP1 2%/50%", [(2.0, 50.0)], True, False),
        ("TP1 2%/30% 不保本", [(2.0, 30.0)], False, False),
        ("TP1 2%/30%+TP2 4%/40%", [(2.0, 30.0), (4.0, 40.0)], True, False),
        ("TP1 1%/30%+TP2 2%/40%", [(1.0, 30.0), (2.0, 40.0)], True, False),
        ("对照 TP1 2%/30% 跟轨", [(2.0, 30.0)], True, True),
    ]

    rows: list[dict] = []
    for symbol, tag, period, mult, ratio in configs:
        candles = load_tf("1h", symbol)
        o = [c["o"] for c in candles]
        h = [c["h"] for c in candles]
        lo_ = [c["l"] for c in candles]
        cl = [c["c"] for c in candles]
        st = super_trend(o, h, lo_, cl, periods=period, multiplier=mult,
                         src="hl2", change_atr=True)
        atr = ta_atr(h, lo_, cl, period)
        adx = ta_adx(h, lo_, cl, ADX_LEN)
        er = er_series(cl, ER_LEN)
        cands = build_candidates(candles, st, atr, adx, er, "A", 0,
                                 stop_atr=3.0, atr_min_ratio=ratio)
        med_r = sorted(abs(c["entry"] - c["stop"]) / c["entry"] * 100
                       for c in cands)
        med_r = med_r[len(med_r) // 2] if med_r else 0.0

        for slabel, stop_pct, stop_atr in stops:
            for pname, legs, move_be, use_trail in plans:
                kw = dict(stop_pct=stop_pct, stop_atr=stop_atr, legs=legs,
                          move_be=move_be, use_trail=use_trail)
                base = simulate_pct_exits(candles, cands, st, BASE_FEE_BPS,
                                          BASE_SLIP_BPS, **kw)
                stress = simulate_pct_exits(candles, cands, st, STRESS_FEE_BPS,
                                            STRESS_SLIP_BPS, **kw)
                segs = {s[0]: stat_block(base["trades"], st["flips"], candles,
                                        base["equity"], s) for s in SEGMENTS}
                allb = stat_block(base["trades"], st["flips"], candles,
                                  base["equity"], ("all", 2021, 2100))
                rows.append({
                    "symbol": symbol, "tag": tag, "st": f"{period}x{mult:g}",
                    "ratio": ratio, "median_3atr_pct": med_r,
                    "stop": slabel, "variant": pname,
                    "n": allb["n"], "ret_pct": allb["ret_pct"], "pf": allb["pf"],
                    "dev": segs["dev"]["pf"], "val": segs["val"]["pf"],
                    "oos": segs["oos"]["pf"], "oos_dd": segs["oos"]["max_dd_pct"],
                    "winrate": allb["winrate"],
                    "avg_hold": _avg_hold(base["trades"]),
                    "max_stop_streak": allb["max_stop_streak"],
                    "stress_oos": stat_block(stress["trades"], st["flips"], candles,
                                             stress["equity"], SEGMENTS[2])["pf"],
                })

    for symbol, tag, period, mult, ratio in configs:
        sub = [r for r in rows if r["tag"] == tag]
        print(f"\n=== {symbol} 1h ST{period}x{mult:g}/R{ratio:g} | "
              f"分批%止盈+保本+剩余反向平（3ATR中位≈{sub[0]['median_3atr_pct']:.2f}%） ===")
        print(f"{'变体':<24} {'止损':>7} {'笔数':>5} {'收益%':>8} {'净PF':>6} "
              f"{'DEV':>6} {'VAL':>6} {'OOS':>6} {'OOSDD%':>7} {'胜率%':>6} "
              f"{'持仓根':>6} {'连损':>5} {'压力OOS':>7}")
        for r in sorted(sub, key=lambda x: -x["oos"]):
            print(f"{r['variant']:<24} {r['stop']:>7} {r['n']:>5} "
                  f"{r['ret_pct']:>+8.1f} {fmt_pf(r['pf']):>6} {fmt_pf(r['dev']):>6} "
                  f"{fmt_pf(r['val']):>6} {fmt_pf(r['oos']):>6} {r['oos_dd']:>7.2f} "
                  f"{r['winrate']:>6.1f} {r['avg_hold']:>6.0f} "
                  f"{r['max_stop_streak']:>5} {fmt_pf(r['stress_oos']):>7}")

    os.makedirs(CACHE_DIR, exist_ok=True)
    rp = os.path.join(CACHE_DIR, "pct_reverse_report.json")
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump({
            "source": "data-api.binance.vision spot 1h",
            "study": "A0 entry; % hard stop (or ATR) x partial % TP + breakeven "
                     "+ reverse-close remainder",
            "params": {"risk_pct": RISK_PCT, "leverage_cap": LEVERAGE_CAP,
                       "fee_bps": BASE_FEE_BPS, "slip_bps": BASE_SLIP_BPS,
                       "stress_fee_bps": STRESS_FEE_BPS,
                       "stress_slip_bps": STRESS_SLIP_BPS},
            "grid": {"configs": [c[1] for c in configs],
                     "stops": [s[0] for s in stops],
                     "plans": [p[0] for p in plans]},
            "results": rows,
        }, fh, ensure_ascii=False, indent=2)
    print(f"\n[done] pct reverse report: {rp}")


def _seg_min(pf_map: dict) -> float:
    vals = [v for v in pf_map.values() if v is not None]
    return min(vals) if vals else 0.0


def run_eth_optimize() -> None:
    """ETH 1h 联合寻优：ST 周期×倍数×ATR ratio 与出场参数一起搜。

    筛选口径（不只看 OOS 单点，避免过拟合）：
      主排序 = min(DEV PF, VAL PF, OOS PF)   —— 三段里最差的那段
      约束   = 全样本笔数 >= 120，且 OOS 笔数 >= 40
      输出   = 同时给出净PF、OOS PF、压力费率 OOS PF、全周期回撤、2026 盈亏
    """
    symbol = "ETHUSDT"
    tf = "1h"
    periods = [10, 11, 12, 14, 15, 16, 18, 20]
    multipliers = [3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0]
    ratios = [0.0, 1.0, 1.05, 1.1, 1.2, 1.3]
    MIN_N, MIN_OOS_N = 120, 40

    candles = load_tf(tf, symbol)
    o = [c["o"] for c in candles]
    h = [c["h"] for c in candles]
    lo_ = [c["l"] for c in candles]
    cl = [c["c"] for c in candles]
    adx = ta_adx(h, lo_, cl, ADX_LEN)
    er = er_series(cl, ER_LEN)
    atr_of = {p: ta_atr(h, lo_, cl, p) for p in periods}

    # 第一阶段：固定"无止损 + TP1 1%/30% + 保本 + 反向"扫入场空间
    stage1_exit = dict(stop_atr=3.5, legs=[(1.0, 30.0)], move_be=True,
                       use_trail=False, no_stop=True)
    cache: dict[tuple, tuple] = {}
    rows = []
    for p in periods:
        atr = atr_of[p]
        for m in multipliers:
            st = super_trend(o, h, lo_, cl, periods=p, multiplier=m,
                             src="hl2", change_atr=True)
            for ratio in ratios:
                cands = build_candidates(candles, st, atr, adx, er, "A", 0,
                                         stop_atr=3.0, atr_min_ratio=ratio)
                if len(cands) < MIN_N:
                    continue
                cache[(p, m, ratio)] = (st, cands)
                b = simulate_pct_exits(candles, cands, st, BASE_FEE_BPS,
                                       BASE_SLIP_BPS, **stage1_exit)
                segs = {x[0]: stat_block(b["trades"], st["flips"], candles,
                                        b["equity"], x) for x in SEGMENTS}
                a = stat_block(b["trades"], st["flips"], candles, b["equity"],
                               ("all", 2021, 2100))
                if a["n"] < MIN_N or segs["oos"]["n"] < MIN_OOS_N:
                    continue
                pf = {k: segs[k]["pf"] for k in ("dev", "val", "oos")}
                rows.append({
                    "period": p, "multiplier": m, "ratio": ratio,
                    "label": f"ST{p}x{m:g}/R{ratio:g}",
                    "n": a["n"], "ret_pct": a["ret_pct"], "pf": a["pf"],
                    "dev": pf["dev"], "val": pf["val"], "oos": pf["oos"],
                    "min_pf": _seg_min(pf), "oos_dd": segs["oos"]["max_dd_pct"],
                    "max_dd": b["max_dd_pct"],
                    "winrate": a["winrate"], "streak": a["max_stop_streak"],
                })

    print(f"\n=== ETH {tf} 第一阶段：入场空间（{len(rows)} 组，"
          f"固定 无止损+TP1 1%/30%+保本+反向） ===")
    print(f"{'组合':<18} {'笔数':>5} {'收益%':>8} {'净PF':>6} {'DEV':>6} {'VAL':>6} "
          f"{'OOS':>6} {'最差段':>7} {'OOSDD%':>7} {'胜率%':>6} {'连损':>4}")
    rows.sort(key=lambda x: -x["min_pf"])
    for r in rows[:18]:
        print(f"{r['label']:<18} {r['n']:>5} {r['ret_pct']:>+8.1f} {fmt_pf(r['pf']):>6} "
              f"{fmt_pf(r['dev']):>6} {fmt_pf(r['val']):>6} {fmt_pf(r['oos']):>6} "
              f"{fmt_pf(r['min_pf']):>7} {r['oos_dd']:>7.2f} {r['winrate']:>6.1f} "
              f"{r['streak']:>4}")

    # 第二阶段：对入场 Top12 扫出场（TP1 幅度 × 平仓比例 × 有无初始止损）
    top = rows[:12]
    tp_grid = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]
    ratio_grid = [20.0, 30.0, 40.0, 50.0]
    stop_grid = [("无初始止损", None, True), ("3.0ATR止损", 3.0, False),
                 ("3.5ATR止损", 3.5, False)]
    best = []
    for e in top:
        st, cands = cache[(e["period"], e["multiplier"], e["ratio"])]
        for pct in tp_grid:
            for pr in ratio_grid:
                for slabel, sa, no_stop in stop_grid:
                    kw = dict(stop_atr=(sa or 3.5), legs=[(pct, pr)],
                              move_be=True, use_trail=False, no_stop=no_stop)
                    b = simulate_pct_exits(candles, cands, st, BASE_FEE_BPS,
                                           BASE_SLIP_BPS, **kw)
                    segs = {x[0]: stat_block(b["trades"], st["flips"], candles,
                                            b["equity"], x) for x in SEGMENTS}
                    a = stat_block(b["trades"], st["flips"], candles, b["equity"],
                                   ("all", 2021, 2100))
                    if a["n"] < MIN_N or segs["oos"]["n"] < MIN_OOS_N:
                        continue
                    pf = {k: segs[k]["pf"] for k in ("dev", "val", "oos")}
                    best.append({
                        "label": e["label"], "stop": slabel,
                        "tp1": pct, "tp1_ratio": pr,
                        "n": a["n"], "ret_pct": a["ret_pct"], "pf": a["pf"],
                        "dev": pf["dev"], "val": pf["val"], "oos": pf["oos"],
                        "min_pf": _seg_min(pf), "oos_dd": segs["oos"]["max_dd_pct"],
                        "max_dd": b["max_dd_pct"], "winrate": a["winrate"],
                        "streak": a["max_stop_streak"], "kw": kw,
                        "cands": cands, "st": st,
                    })

    best.sort(key=lambda x: -x["min_pf"])
    # 压力费率只对前 30 名算，省时间
    for r in best[:30]:
        s = simulate_pct_exits(candles, r["cands"], r["st"], STRESS_FEE_BPS,
                               STRESS_SLIP_BPS, **r["kw"])
        r["stress_oos"] = stat_block(s["trades"], r["st"]["flips"], candles,
                                     s["equity"], SEGMENTS[2])["pf"]
        y26 = [t for t in s["trades"] if t["entry_ts"] >= _ms(2026)]
        r["pnl_2026"] = sum(t["pnl"] for t in s["trades"]
                            if t["entry_ts"] >= _ms(2026)) if y26 else 0.0

    print(f"\n=== ETH {tf} 第二阶段：出场网格（{len(best)} 组，按最差段 PF 排序） ===")
    print(f"{'组合':<18} {'止损':<10} {'TP1':>5} {'比例':>4} {'笔数':>5} {'收益%':>8} "
          f"{'净PF':>6} {'DEV':>6} {'VAL':>6} {'OOS':>6} {'最差段':>7} {'OOSDD%':>7} "
          f"{'压力OOS':>7} {'2026':>7}")
    for r in best[:20]:
        print(f"{r['label']:<18} {r['stop']:<10} {r['tp1']:>5.2f} {r['tp1_ratio']:>4.0f} "
              f"{r['n']:>5} {r['ret_pct']:>+8.1f} {fmt_pf(r['pf']):>6} "
              f"{fmt_pf(r['dev']):>6} {fmt_pf(r['val']):>6} {fmt_pf(r['oos']):>6} "
              f"{fmt_pf(r['min_pf']):>7} {r['oos_dd']:>7.2f} "
              f"{fmt_pf(r.get('stress_oos', 0)):>7} {r.get('pnl_2026', 0):>+7.0f}")

    os.makedirs(CACHE_DIR, exist_ok=True)
    rp = os.path.join(CACHE_DIR, "eth_opt_report.json")
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump({
            "source": f"data-api.binance.vision {symbol} spot {tf}",
            "study": "ETH entry space (ST period x multiplier x ATR ratio) x exit "
                     "(% TP1 legs, optional hard stop, breakeven, reverse close)",
            "params": {"risk_pct": RISK_PCT, "leverage_cap": LEVERAGE_CAP,
                       "fee_bps": BASE_FEE_BPS, "slip_bps": BASE_SLIP_BPS,
                       "stress_fee_bps": STRESS_FEE_BPS,
                       "stress_slip_bps": STRESS_SLIP_BPS},
            "grid": {"periods": periods, "multipliers": multipliers,
                     "ratios": ratios, "tp1": tp_grid, "tp1_ratio": ratio_grid,
                     "stops": [s[0] for s in stop_grid]},
            "stage1": rows,
            "stage2": [{k: v for k, v in r.items() if k not in ("kw", "cands", "st")}
                       for r in best],
        }, fh, ensure_ascii=False, indent=2)
    print(f"\n[done] ETH opt report: {rp}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["fetch", "run", "all", "exit", "stop",
                                       "cooldown", "factor", "factor2", "factor15",
                                       "diag", "atrfilter", "atrth", "atrfactor",
                                       "f35atr", "atrz", "atrpct", "atrchg",
                                       "val15", "val15matrix", "val30matrix",
                                       "delay", "full1h", "ethmatrix", "tpsl",
                                       "pctexit", "pctrev", "ethopt"])
    args = ap.parse_args()

    if args.action == "ethopt":
        run_eth_optimize()
        return
    if args.action == "pctrev":
        run_pct_reverse_study()
        return
    if args.action == "pctexit":
        run_pct_exit_study()
        return
    if args.action == "tpsl":
        run_tp_sl_study()
        return
    if args.action == "exit":
        run_exit_study()
        return
    if args.action == "stop":
        run_stop_sweep()
        return
    if args.action == "cooldown":
        run_cooldown_sweep()
        return
    if args.action == "factor":
        run_factor_sweep()
        return
    if args.action == "factor2":
        run_factor_sweep([4.5, 5.0, 6.0], "factor_report_ext.json")
        return
    if args.action == "factor15":
        run_factor_sweep([3.0, 4.0, 5.0, 6.0, 7.0, 8.0],
                         "factor_report_15m.json", "15m")
        return
    if args.action == "diag":
        run_signal_diag()
        return
    if args.action == "atrfilter":
        run_atr_filter_study()
        return
    if args.action == "atrth":
        run_atr_threshold_sweep()
        return
    if args.action == "atrfactor":
        run_factor_sweep([3.0, 3.25, 3.5, 4.0], "atr_factor_report.json", "1h",
                         atr_min_ratio=1.1)
        return
    if args.action == "f35atr":
        run_atr_threshold_sweep([0.0, 1.0, 1.05, 1.1, 1.15],
                                "f35_atr_threshold_report.json", 3.5)
        return
    if args.action == "atrz":
        run_atr_z_study()
        return
    if args.action == "atrpct":
        run_atr_pct_study()
        return
    if args.action == "atrchg":
        run_atr_chg_study()
        return
    if args.action == "val15":
        run_val15()
        return
    if args.action == "val15matrix":
        run_15m_factor_atr_matrix()
        return
    if args.action == "val30matrix":
        run_30m_factor_atr_matrix()
        return
    if args.action == "delay":
        run_delay_diag()
        return
    if args.action == "full1h":
        run_1h_full_matrix()
        return
    if args.action == "ethmatrix":
        run_eth_matrix()
        return

    if args.action in ("fetch", "all"):
        for tf in TF_MS:
            fetch_tf(tf)
    if args.action in ("run", "all"):
        results = []
        for tf in TF_MS:
            candles = load_tf(tf)
            print(f"[data] {tf}: {len(candles)} bars, "
                  f"{datetime.fromtimestamp(candles[0]['ts']/1000, timezone.utc):%Y-%m-%d} -> "
                  f"{datetime.fromtimestamp(candles[-1]['ts']/1000, timezone.utc):%Y-%m-%d}")
            for mode in ("A", "B"):
                for fid in range(4):
                    results.append(run_variant(tf, candles, mode, fid))
        os.makedirs(CACHE_DIR, exist_ok=True)
        report = {
            "source": "data-api.binance.vision BTCUSDT spot (futures endpoint blocked by proxy)",
            "params": {"st": "10x3", "adx": "14>20", "er": "20>=0.25",
                       "risk_pct": RISK_PCT, "stop_atr": STOP_ATR, "tp_r": TP_R,
                       "leverage_cap": LEVERAGE_CAP, "pullback_atr": PULLBACK_ATR,
                       "fee_bps": BASE_FEE_BPS, "slip_bps": BASE_SLIP_BPS,
                       "stress_fee_bps": STRESS_FEE_BPS, "stress_slip_bps": STRESS_SLIP_BPS},
            "results": results,
        }
        rp = os.path.join(CACHE_DIR, "report.json")
        with open(rp, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        for tf in TF_MS:
            print_table([r for r in results if r["tf"] == tf], tf)
        print(f"\n[done] full report: {rp}")


if __name__ == "__main__":
    main()
