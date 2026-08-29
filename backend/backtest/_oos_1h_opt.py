"""BTC / ETH 1h 在 2025-09-03 ~ 2026-03-03 样本外窗口的完整两阶段寻优。

目的：验证早期窗口是否存在与样本内（11×4.0/≥50、7×4.0/≥35）同族的参数，
判断 BTC/ETH 的参数是否跨窗口稳健，还是随行情漂移。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _live_cfg_backtest import (
    BIAS_TFS, LIVE_URL, fetch_candles, ts_fmt, _get,
)
from _eth_score_opt import run_one, coarse, fine_around, finalize, MIN_TRADES
from _btc_score_opt import btc_sym
from _eth_score_opt import eth_sym

GATE_TF = "1h"
W_START = datetime(2025, 9, 3, tzinfo=timezone.utc)
W_END = datetime(2026, 3, 4, tzinfo=timezone.utc)  # 半开区间，含 03-03 全天
BEST_IN_SAMPLE = {
    "BTC": (11, 4.0, 50),
    "ETH": (7, 4.0, 35),
}


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
    a_ms, b_ms = _ms(W_START), _ms(W_END)
    out = {"note": "BTC/ETH 1h 样本外窗口 2025-09-03~2026-03-03 完整寻优", "by_symbol": {}}

    for name, sym_fn in (("BTC", btc_sym), ("ETH", eth_sym)):
        sym = sym_fn(live)
        print(f"\n===== {name} 1h OOS 寻优 =====", flush=True)
        entry = {"symbol": sym["symbol"], "in_sample_best": BEST_IN_SAMPLE[name]}

        candles = fetch_candles(sym["symbol"], GATE_TF, 9000)
        w = slice_window(candles, a_ms, b_ms)
        print(f"  窗口 {len(w)} 根 {ts_fmt(w[0]['ts'])}~{ts_fmt(w[-1]['ts'])}", flush=True)

        cbtf: dict[str, list] = {GATE_TF: w}
        for tf in BIAS_TFS:
            if tf == GATE_TF:
                continue
            bars_needed = {"15m": 18000, "4h": 1500, "1d": 300}.get(tf, 1500)
            extra_raw = fetch_candles(sym["symbol"], tf, bars_needed)
            extra = slice_window(extra_raw, a_ms, b_ms)
            if extra:
                cbtf[tf] = extra
                print(f"  辅助 {tf}: {len(extra)} 根", flush=True)

        rows = coarse(sym, GATE_TF, w, cbtf)
        ok = [r for r in rows if r["trades"] >= MIN_TRADES]
        centers = []
        if ok:
            centers.append(max(ok, key=lambda r: r["pnl_u"]))
            centers.append(max(ok, key=lambda r: r["score"]))
        fine_rows = []
        seen = set()
        for c in centers:
            for r in fine_around(sym, GATE_TF, w, cbtf, c):
                k = (r["periods"], r["multiplier"], r["min_score_100"])
                if k not in seen:
                    seen.add(k)
                    fine_rows.append(r)
        res = finalize(rows + fine_rows)
        entry.update({
            "start": ts_fmt(w[0]["ts"]),
            "end": ts_fmt(w[-1]["ts"]),
            "bars": len(w),
            "coarse_combos": len(rows),
            "fine_combos": len(fine_rows),
            "best_score": res["best_score"],
            "best_pnl": res["best_pnl"],
            "top8": res["top8"],
        })
        print("\n[样本外 Top 结果]", flush=True)
        for label in ("best_score", "best_pnl"):
            report(label, res[label])
        # 交叉：样本内最优参数在样本外（已在上一脚本测过，这里再确认一次）
        pe, m, thr = BEST_IN_SAMPLE[name]
        r_cross = run_one(sym, GATE_TF, w, cbtf, pe, m, thr)
        report(f"样本内最优 {pe}×{m}/≥{thr} 在样本外", r_cross)
        entry["cross_in_sample_on_oos"] = r_cross

        out["by_symbol"][name] = entry

    path = os.path.join(os.path.dirname(__file__), "_oos_1h_opt.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {path}", flush=True)


if __name__ == "__main__":
    main()
