"""ETH 15m 早期窗口（2026-03-04 ~ 05-26）寻优 + 与后期窗口交叉验证。

背景：1h 回测窗口是 02-22 ~ 08-29（4500 根），15m 是 05-27 ~ 08-29（9000 根）。
本脚本把 15m 的早期窗口 03-04 ~ 05-26 单独拉出来跑同一套两阶段寻优，
并把「后期最优 6×4.5/≥65」放到早期窗口、把「早期最优」放到后期窗口做交叉验证。
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _live_cfg_backtest import (
    BARS, BIAS_TFS, LIVE_URL, exit_rules, fetch_candles, ts_fmt, _get,
)
from _eth_score_opt import eth_sym, run_one, coarse, fine_around, finalize, MIN_TRADES

GATE_TF = "15m"
# 早期窗口（UTC，半开区间）：03-04 00:00 ~ 05-27 00:00
W_EARLY_START = datetime(2026, 3, 4, tzinfo=timezone.utc)
W_EARLY_END = datetime(2026, 5, 27, tzinfo=timezone.utc)
# 后期窗口（UTC）：05-27 00:00 ~ 08-30 00:00
W_LATE_START = datetime(2026, 5, 27, tzinfo=timezone.utc)
W_LATE_END = datetime(2026, 8, 30, tzinfo=timezone.utc)
LATE_BEST = (6, 4.5, 65)  # 之前寻优得到的后期 15m 最优


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def slice_window(candles: list, a_ms: int, b_ms: int) -> list:
    return [c for c in candles if a_ms <= c["ts"] < b_ms]


def report(label: str, r: dict | None) -> None:
    if not r:
        print(f"  {label}: 无结果", flush=True)
        return
    print(
        f"  {label}: ST {r['periods']}×{r['multiplier']} score≥{r['min_score_100']} "
        f"→ {r['pnl_u']}U / dd {r['max_dd_pct']}% / {r['trades']}笔 / "
        f"wr {r['win_rate']}% / PF {r['profit_factor']} / blocked {r['blocked']}",
        flush=True,
    )


def main():
    live = _get(LIVE_URL)
    sym = eth_sym(live)
    out = {
        "symbol": sym["symbol"],
        "note": "ETH 15m 早期窗口寻优 + 双向交叉验证",
        "early": {"start": "2026-03-04", "end": "2026-05-26"},
        "late": {"start": "2026-05-27", "end": "2026-08-29"},
    }

    # ---- 拉数据：15m 拉 18000 根（覆盖 02-22 起），其余 tf 复用原窗口逻辑 ----
    raw15 = fetch_candles(sym["symbol"], GATE_TF, 18000)
    e_ms, l_ms = _ms(W_EARLY_START), _ms(W_LATE_END)
    early_c = slice_window(raw15, _ms(W_EARLY_START), _ms(W_EARLY_END))
    late_c = raw15[-9000:]
    print(f"\n[15m] 早期窗口 {len(early_c)} 根 {ts_fmt(early_c[0]['ts'])}~{ts_fmt(early_c[-1]['ts'])}", flush=True)
    print(f"[15m] 后期窗口 {len(late_c)} 根 {ts_fmt(late_c[0]['ts'])}~{ts_fmt(late_c[-1]['ts'])}", flush=True)

    cbtf_e: dict[str, list] = {GATE_TF: early_c}
    cbtf_l: dict[str, list] = {GATE_TF: late_c}
    for tf in BIAS_TFS:
        if tf == GATE_TF:
            continue
        extra = fetch_candles(sym["symbol"], tf, 9000)
        e_extra = slice_window(extra, _ms(W_EARLY_START), _ms(W_EARLY_END))
        l_extra = slice_window(extra, _ms(W_LATE_START), _ms(W_LATE_END))
        if e_extra:
            cbtf_e[tf] = e_extra
        if l_extra:
            cbtf_l[tf] = l_extra

    # ---- 早期窗口：粗扫 + 细扫 ----
    print(f"\n=== 早期窗口 粗扫 ===", flush=True)
    rows_e = coarse(sym, GATE_TF, early_c, cbtf_e)
    ok = [r for r in rows_e if r["trades"] >= MIN_TRADES]
    centers = []
    if ok:
        centers.append(max(ok, key=lambda r: r["pnl_u"]))
        centers.append(max(ok, key=lambda r: r["score"]))
    fine_e = []
    seen = set()
    for c in centers:
        for r in fine_around(sym, GATE_TF, early_c, cbtf_e, c):
            k = (r["periods"], r["multiplier"], r["min_score_100"])
            if k not in seen:
                seen.add(k)
                fine_e.append(r)
    res_e = finalize(rows_e + fine_e)
    out["early_opt"] = {
        "bars": len(early_c),
        "coarse": len(rows_e),
        "fine": len(fine_e),
        "best_score": res_e["best_score"],
        "best_pnl": res_e["best_pnl"],
        "top8": res_e["top8"],
    }
    print("\n[早期窗口 Top 结果]", flush=True)
    for label in ("best_score", "best_pnl"):
        report(label, res_e[label])

    # ---- 交叉验证 ----
    print("\n=== 交叉验证 ===", flush=True)
    # 1) 后期最优参数放到早期窗口跑
    pe, m, thr = LATE_BEST
    r_late_best_on_early = run_one(sym, GATE_TF, early_c, cbtf_e, pe, m, thr)
    report(f"后期最优 {pe}×{m}/≥{thr} 在早期窗口", r_late_best_on_early)
    out["cross_late_best_on_early"] = r_late_best_on_early

    # 2) 早期窗口最优参数放到后期窗口跑
    e_best = None
    if res_e["best_score"]:
        e_best = res_e["best_score"]
    elif res_e["best_pnl"]:
        e_best = res_e["best_pnl"]
    if e_best:
        pe2, m2, th2 = e_best["periods"], e_best["multiplier"], e_best["min_score_100"]
        r_early_best_on_late = run_one(sym, GATE_TF, late_c, cbtf_l, pe2, m2, th2)
        report(f"早期最优 {pe2}×{m2}/≥{th2} 在后期窗口", r_early_best_on_late)
        out["cross_early_best_on_late"] = r_early_best_on_late

    path = os.path.join(os.path.dirname(__file__), "_eth_early_window.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {path}", flush=True)


if __name__ == "__main__":
    main()
