"""SPCX-USDT-SWAP 15m / 1h：关掉组合过滤器，只按 0-100 置信度打分开仓。

两阶段寻优：
  1) 粗网格：periods(7..21 奇) × mult(2..10 整) × 评分阈值(35..80 步5)
  2) 细网格：自动围绕粗网格最优区（±2 period / ±2.0 mult / ±15 分）步长细化
"""
from __future__ import annotations

import copy
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest
from _live_cfg_backtest import (
    BARS, BIAS_TFS, LIVE_URL, exit_rules, fetch_candles, ts_fmt, _get,
)
from _btc_score_opt import score_only_cfg, score_row

GATE_TFS = ("15m", "1h")
COARSE_PERIODS = list(range(7, 22, 2))
COARSE_MULTS = list(range(2, 11))
COARSE_SCORES = list(range(35, 85, 5))
MIN_TRADES = 8


def spcx_sym(live: dict) -> dict:
    for s in live["symbols"]:
        if "SPCX" in s["symbol"]:
            return copy.deepcopy(s)
    raise RuntimeError("live symbols 里没有 SPCX")


def run_one(sym: dict, gate_tf: str, candles: list, cbtf: dict,
            pe: int, m: float, thr: int) -> dict | None:
    base_p = sym["params"]
    p = {**base_p, "periods": pe, "multiplier": float(m)}
    r = run_backtest(
        candles, p,
        init_cash=100.0, fee_rate=0.0005, allow_short=True,
        exit_rules=exit_rules(sym),
        sizing="fixed", margin_usdt=sym["margin_usdt"], leverage=sym["leverage"],
        live_gate=score_only_cfg(gate_tf), gate_tf=gate_tf, candles_by_tf=cbtf,
        score_only_gate=True, min_total_score=float(thr),
    )
    if "error" in r:
        return None
    pnl = round(r["final"] - 100, 2)
    cur_p, cur_m = base_p["periods"], base_p["multiplier"]
    return {
        "periods": pe,
        "multiplier": float(m),
        "min_score_100": thr,
        "pnl_u": pnl,
        "margin_roi_pct": round(pnl / sym["margin_usdt"] * 100, 1),
        "max_dd_pct": r["max_dd_pct"],
        "trades": r["trades"],
        "win_rate": r["win_rate"],
        "profit_factor": r["profit_factor"],
        "blocked": r["er_blocked"],
        "score": round(score_row(pnl, r["max_dd_pct"], r["trades"]), 3),
        "is_current_st": pe == cur_p and float(m) == float(cur_m),
    }


def coarse(sym: dict, gate_tf: str, candles: list, cbtf: dict) -> list[dict]:
    rows = []
    total = len(COARSE_PERIODS) * len(COARSE_MULTS) * len(COARSE_SCORES)
    n = 0
    t0 = time.time()
    for pe in COARSE_PERIODS:
        for m in COARSE_MULTS:
            for thr in COARSE_SCORES:
                n += 1
                r = run_one(sym, gate_tf, candles, cbtf, pe, m, thr)
                if r:
                    rows.append(r)
                if n % 100 == 0:
                    print(f"  [coarse] {gate_tf} {n}/{total} …", flush=True)
    print(f"  [coarse] {gate_tf} done {len(rows)} combos in {time.time()-t0:.0f}s", flush=True)
    return rows


def fine_around(sym: dict, gate_tf: str, candles: list, cbtf: dict,
                center: dict, p_rad: int = 2, m_rad: float = 2.0, s_rad: int = 15) -> list[dict]:
    pe0, m0, s0 = center["periods"], center["multiplier"], center["min_score_100"]
    pes = sorted({x for x in range(pe0 - p_rad, pe0 + p_rad + 1) if x >= 2})
    ms = [round(x / 10, 1) for x in
          range(int(round((m0 - m_rad) * 10)), int(round((m0 + m_rad) * 10)) + 1, 5)]
    ss = sorted({x for x in range(s0 - s_rad, s0 + s_rad + 1, 5) if 0 <= x <= 100})
    rows = []
    total = len(pes) * len(ms) * len(ss)
    n = 0
    t0 = time.time()
    for pe in pes:
        for m in ms:
            for s in ss:
                n += 1
                r = run_one(sym, gate_tf, candles, cbtf, pe, m, s)
                if r:
                    rows.append(r)
                if n % 80 == 0:
                    print(f"  [fine] {gate_tf} {n}/{total} …", flush=True)
    print(f"  [fine] {gate_tf} done {len(rows)} combos in {time.time()-t0:.0f}s", flush=True)
    return rows


def finalize(rows: list[dict]) -> dict:
    ok = [r for r in rows if r["trades"] >= MIN_TRADES]
    ok.sort(key=lambda x: (x["score"], x["pnl_u"]), reverse=True)
    best_score = ok[0] if ok else None
    best_pnl = max(ok, key=lambda r: r["pnl_u"]) if ok else None
    return {"best_score": best_score, "best_pnl": best_pnl, "top8": ok[:8]}


def main():
    live = _get(LIVE_URL)
    sym = spcx_sym(live)
    out = {"symbol": sym["symbol"], "filters": "仅0-100打分，无ATR/区间/MTF/ADX/等级/强度硬闸",
           "exit_rules": "线上SPCX三级止盈", "by_tf": {}}

    for gate_tf in GATE_TFS:
        bars = BARS.get(gate_tf, 4500)
        print(f"\n=== SPCX {gate_tf} score-only ===", flush=True)
        candles = fetch_candles(sym["symbol"], gate_tf, bars)
        cbtf = {gate_tf: candles}
        for tf in BIAS_TFS:
            if tf == gate_tf:
                continue
            extra = fetch_candles(sym["symbol"], tf, min(bars, BARS.get(tf, 4500)))
            if extra:
                cbtf[tf] = extra

        rows = coarse(sym, gate_tf, candles, cbtf)
        ok = [r for r in rows if r["trades"] >= MIN_TRADES]
        centers = []
        if ok:
            centers.append(max(ok, key=lambda r: r["pnl_u"]))
            centers.append(max(ok, key=lambda r: r["score"]))
        fine_rows = []
        seen = set()
        for c in centers:
            for r in fine_around(sym, gate_tf, candles, cbtf, c):
                k = (r["periods"], r["multiplier"], r["min_score_100"])
                if k not in seen:
                    seen.add(k)
                    fine_rows.append(r)
        all_rows = rows + fine_rows
        res = finalize(all_rows)
        out["by_tf"][gate_tf] = {
            "start": ts_fmt(candles[0]["ts"]),
            "end": ts_fmt(candles[-1]["ts"]),
            "bars": len(candles),
            "coarse_combos": len(rows),
            "fine_combos": len(fine_rows),
            **res,
        }
        for label in ("best_score", "best_pnl"):
            b = res[label]
            if b:
                print(
                    f"  {label}: ST {b['periods']}×{b['multiplier']} score≥{b['min_score_100']} "
                    f"→ {b['pnl_u']}U dd={b['max_dd_pct']}% trades={b['trades']} "
                    f"wr={b['win_rate']}% PF={b['profit_factor']}",
                    flush=True,
                )

    path = os.path.join(os.path.dirname(__file__), "_spcx_score_opt.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {path}", flush=True)
    for tf, v in out["by_tf"].items():
        b = v.get("best_pnl")
        if b:
            print(
                f"[{tf}] 收益最高: ST {b['periods']}×{b['multiplier']}, 评分≥{b['min_score_100']} "
                f"→ {b['pnl_u']}U / dd {b['max_dd_pct']}% / {b['trades']}笔 / PF {b['profit_factor']}",
                flush=True,
            )


if __name__ == "__main__":
    main()
