"""1h 联合寻优：闸门（ER / 等级 / ATR / 区间 / ADX）+ SuperTrend 周期×倍数。

两阶段（每品种独立）：
  1) 锁当前超趋，扫闸门网格
  2) 用最优闸门再扫超趋；若超趋变了，再扫一遍闸门

条件对齐实盘：live_gate（含动量/假突破/自适应 ER）+ 该品种线上 exit_rules
（三级止盈含 tp3_mode、保本、ST 跟踪、极端止损）+ 10U×10x，只做 1h。
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
    BARS, BIAS_TFS, LIVE_URL, exit_rules, fetch_candles, trade_cfg, ts_fmt, _get,
)

GATE_TF = "1h"
PERIODS = list(range(7, 22, 2))
MULTS = [float(x) for x in range(2, 11)]
MIN_TRADES = 8
SYMBOLS = ("BTC", "ETH", "SPCX", "MU", "SNDK")

ER_MINS = (0.12, 0.15, 0.18)
GRADE_CHOICES = (
    (["A", "B", "C"], 1, "ABC≥1"),
    (["A", "B"], 2, "AB≥2"),
)
ATR_CHOICES = (
    (False, 0.7, "关"),
    (True, 0.7, "0.70"),
)
RANGE_CHOICES = (
    (False, 3, "关"),
    (True, 2, "触边2"),
    (True, 3, "触边3"),
)
ADX_CHOICES = (
    (False, "关"),
    (True, "≥20"),
)


def score_row(pnl: float, dd: float, trades: int) -> float:
    if trades < MIN_TRADES or dd <= 0:
        return -999.0
    return pnl / dd


def gate_spec(er_min, grades, min_score, atr_on, atr_v, rng_on, touches, adx_on) -> dict:
    hide = max(0.0, round(er_min - 0.04, 2))
    return {
        "er_min": er_min,
        "er_hide_below": hide,
        "er_weak_min": hide,
        "er_trend": 0.30,
        "quick_enabled": False,
        "allow_grades": list(grades),
        "min_score": min_score,
        "atr_filter_enabled": atr_on,
        "atr_vol_min": atr_v,
        "range_filter_enabled": rng_on,
        "range_size_max": 0.15,
        "range_touches_min": touches,
        "adx_filter_enabled": adx_on,
        "adx_min": 20.0,
        "adx_period": 14,
        "mtf_filter_enabled": False,
    }


def gate_label(g: dict) -> str:
    grades = "".join(g["allow_grades"])
    atr = f"ATR{g['atr_vol_min']:.2f}" if g["atr_filter_enabled"] else "ATRoff"
    rng = f"R{g['range_touches_min']}" if g["range_filter_enabled"] else "Roff"
    adx = "ADX20" if g["adx_filter_enabled"] else "ADXoff"
    return f"ER{g['er_min']:.2f}_{grades}s{g['min_score']}_{atr}_{rng}_{adx}"


def live_gate_dict(sym: dict) -> dict:
    keys = [
        "er_min", "er_hide_below", "er_weak_min", "er_trend", "quick_enabled",
        "allow_grades", "min_score",
        "atr_filter_enabled", "atr_vol_min",
        "range_filter_enabled", "range_size_max", "range_touches_min",
        "adx_filter_enabled", "adx_min", "adx_period",
        "mtf_filter_enabled",
    ]
    return {k: copy.deepcopy(sym[k]) for k in keys}


def apply_on(sym: dict, gate: dict, periods: int | None = None, mult: float | None = None) -> dict:
    s = copy.deepcopy(sym)
    s.update(gate)
    s["allow_tfs"] = [GATE_TF]
    if periods is not None:
        s["params"] = {**s["params"], "periods": periods, "multiplier": float(mult)}
    return s


def all_gate_grid() -> list[dict]:
    out = []
    # 全关对照：只靠超趋翻转
    out.append(gate_spec(0.0, ["A", "B", "C"], 1, False, 0.7, False, 3, False))
    for er in ER_MINS:
        for grades, score, _ in GRADE_CHOICES:
            for atr_on, atr_v, _ in ATR_CHOICES:
                for rng_on, touches, _ in RANGE_CHOICES:
                    for adx_on, _ in ADX_CHOICES:
                        out.append(gate_spec(
                            er, grades, score, atr_on, atr_v, rng_on, touches, adx_on,
                        ))
    # 去重
    seen = set()
    uniq = []
    for g in out:
        k = gate_label(g)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(g)
    return uniq


def bt(sym: dict, candles: list, cbtf: dict, gate: dict, pe: int, m: float) -> dict | None:
    s = apply_on(sym, gate, pe, m)
    r = run_backtest(
        candles, s["params"],
        init_cash=100.0, fee_rate=0.0005, allow_short=True,
        exit_rules=exit_rules(s),
        sizing="fixed", margin_usdt=sym["margin_usdt"],
        leverage=sym["leverage"],
        live_gate=trade_cfg(s), gate_tf=GATE_TF, candles_by_tf=cbtf,
    )
    if "error" in r:
        return None
    pnl = round(r["final"] - 100, 2)
    return {
        "st": f"{pe}×{m:g}",
        "periods": pe,
        "multiplier": m,
        "gate": gate_label(gate),
        "er_min": gate["er_min"],
        "grades": "".join(gate["allow_grades"]),
        "min_score": gate["min_score"],
        "atr": (f"{gate['atr_vol_min']:.2f}" if gate["atr_filter_enabled"] else "off"),
        "range": (f"t{gate['range_touches_min']}" if gate["range_filter_enabled"] else "off"),
        "adx": ("20" if gate["adx_filter_enabled"] else "off"),
        "pnl_u": pnl,
        "max_dd_pct": r["max_dd_pct"],
        "trades": r["trades"],
        "win_rate": r["win_rate"],
        "profit_factor": r["profit_factor"],
        "blocked": r["er_blocked"],
        "score": round(score_row(pnl, r["max_dd_pct"], r["trades"]), 3),
    }


def pick_best(rows: list[dict]) -> dict | None:
    ok = [r for r in rows if r["score"] > -900]
    if not ok:
        ok = [r for r in rows if r["trades"] >= 5]
    if not ok:
        return None
    return max(ok, key=lambda r: (r["score"], r["pnl_u"]))


def run_symbol(name: str, sym: dict, candles: list, cbtf: dict) -> dict:
    cur_p = int(sym["params"]["periods"])
    cur_m = float(sym["params"]["multiplier"])
    live_g = live_gate_dict(sym)
    rules_g = all_gate_grid()
    t0 = time.time()

    print(f"\n=== {name} stage1 gates @{cur_p}x{cur_m:g}  n={len(rules_g)} ===", flush=True)
    gate_rows = []
    for i, g in enumerate(rules_g, 1):
        row = bt(sym, candles, cbtf, g, cur_p, cur_m)
        if row:
            row["is_live_st"] = True
            row["is_live_gate"] = gate_label(g) == gate_label(live_g)
            gate_rows.append(row)
        if i % 18 == 0:
            print(f"  {name} gate {i}/{len(rules_g)}", flush=True)
    gate_rows.sort(key=lambda r: r["score"], reverse=True)

    live_row = bt(sym, candles, cbtf, live_g, cur_p, cur_m)
    if live_row:
        live_row["is_live_st"] = True
        live_row["is_live_gate"] = True
        live_row["gate"] = "LIVE " + gate_label(live_g)

    best_g_row = pick_best(gate_rows)
    best_gate = next(
        (g for g in rules_g if best_g_row and gate_label(g) == best_g_row["gate"]),
        live_g,
    )

    print(f"  {name} stage2 ST @ {gate_label(best_gate)}", flush=True)
    st_rows = []
    total = len(PERIODS) * len(MULTS)
    n = 0
    for pe in PERIODS:
        for m in MULTS:
            n += 1
            row = bt(sym, candles, cbtf, best_gate, pe, m)
            if row:
                row["is_live_st"] = pe == cur_p and m == cur_m
                row["is_live_gate"] = False
                st_rows.append(row)
            if n % 18 == 0:
                print(f"  {name} st {n}/{total}", flush=True)
    st_rows.sort(key=lambda r: r["score"], reverse=True)
    best_st_row = pick_best(st_rows)

    # 超趋变了则用新超趋再扫闸门，捕捉交互
    if best_st_row and (
        best_st_row["periods"] != cur_p or float(best_st_row["multiplier"]) != cur_m
    ):
        pe, m = best_st_row["periods"], float(best_st_row["multiplier"])
        print(f"  {name} stage3 gates @{pe}x{m:g}", flush=True)
        g2 = []
        for i, g in enumerate(rules_g, 1):
            row = bt(sym, candles, cbtf, g, pe, m)
            if row:
                g2.append(row)
            if i % 24 == 0:
                print(f"  {name} gate2 {i}/{len(rules_g)}", flush=True)
        g2.sort(key=lambda r: r["score"], reverse=True)
        best2 = pick_best(g2)
        if best2 and (best2["score"], best2["pnl_u"]) > (best_st_row["score"], best_st_row["pnl_u"]):
            best_st_row = best2
            gate_rows = g2  # 展示用：最终超趋下的闸门榜

    def slim(r, n=8):
        return [{k: v for k, v in x.items() if k not in ("is_live_st",)} for x in r[:n]]

    elapsed = round(time.time() - t0, 1)
    print(
        f"  {name} done {elapsed}s  live={live_row['pnl_u'] if live_row else '?'}U  "
        f"best={best_st_row['st'] if best_st_row else '-'} "
        f"{best_st_row['gate'] if best_st_row else ''} "
        f"{best_st_row['pnl_u'] if best_st_row else '?'}U",
        flush=True,
    )
    return {
        "start": ts_fmt(candles[0]["ts"]),
        "end": ts_fmt(candles[-1]["ts"]),
        "bars": len(candles),
        "live_st": f"{cur_p}×{cur_m:g}",
        "live_gate": gate_label(live_g),
        "live": live_row,
        "best": best_st_row,
        "best_gate_at_live_st": best_g_row,
        "top_gates_live_st": slim(gate_rows, 8),
        "top_st_best_gate": slim(st_rows, 8),
        "elapsed_s": elapsed,
    }


def main():
    live = _get(LIVE_URL)
    by_name = {}
    for s in live["symbols"]:
        n = s["symbol"].replace("-USDT-SWAP", "").replace("-USDT", "")
        if n in SYMBOLS:
            by_name[n] = s

    out = {"tf": GATE_TF, "min_trades": MIN_TRADES, "symbols": {}}
    for name in SYMBOLS:
        if name not in by_name:
            print(f"skip {name}: not on live list", flush=True)
            continue
        sym = by_name[name]
        print(f"\n### fetch {name} {GATE_TF} ###", flush=True)
        candles = fetch_candles(sym["symbol"], GATE_TF, BARS[GATE_TF])
        cbtf = {GATE_TF: candles}
        for tf in BIAS_TFS:
            if tf == GATE_TF:
                continue
            extra = fetch_candles(sym["symbol"], tf, min(BARS[GATE_TF], BARS.get(tf, 4500)))
            if extra:
                cbtf[tf] = extra
        out["symbols"][name] = run_symbol(name, sym, candles, cbtf)

    path = os.path.join(os.path.dirname(__file__), "_joint_opt_1h.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {path}", flush=True)
    summary = {
        n: {
            "live": v.get("live"),
            "best": v.get("best"),
        }
        for n, v in out["symbols"].items()
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
