# -*- coding: utf-8 -*-
"""NVDA / MU / SNDK 三只股票行情特性对照（阶段1配套诊断）。

指标口径（全部基于各品种线上 SuperTrend 参数 + 1h K 线缓存）：
  ST flip/100根   ：翻转次数 / 有效K数 × 100
  平均趋势持续    ：单段趋势平均延续 bar 数（含中位数，抗单根噪声）
  ER均值          ：Kaufman ER(window=60) 逐根滑动的均值
                    附：趋势根占比(ER≥0.30) / 震荡根占比(ER<0.15)
  信号数量        ：ST 翻转信号总数（另给每100根密度与线上 v1 成交笔数）
  ATR ratio波动   ：ATR14/过去50根均值 比值的 std（系统 _atr_soft_part 同口径）
                    附 mean 与绝对波动 ATR(periods)/价格均值
"""
from __future__ import annotations

import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from indicators import super_trend, ta_atr  # noqa: E402
from regime import efficiency_ratio  # noqa: E402
from _live_cfg_backtest import _get, ts_fmt  # noqa: E402

LIVE_URLS = ["http://43.108.10.84:5174/api/trade/symbols",
             "http://47.84.106.154:5174/api/trade/symbols"]
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data")
CASES = [("NVDA", "NVDA.json"), ("MU", "MU.json"), ("SNDK", "SNDK.json")]
TF = "1h"


def stats_rows():
    rows = {}
    for name, fname in CASES:
        path = os.path.join(DATA, fname)
        cached = json.load(open(path, encoding="utf-8"))
        cbtf = {k: v for k, v in cached.items()
                if isinstance(v, list) and v and "ts" in (v[0] or {})}
        candles = cbtf.get(TF) or cbtf.get("1H")
        sym = None
        for url in LIVE_URLS:
            try:
                live = _get(url)
            except Exception:
                continue
            sym = next((s for s in live["symbols"]
                        if s["symbol"].startswith(name + "-")), None)
            if sym:
                break
        p = sym["params"]
        rows[name] = dict(per=p.get("periods", 15), mul=p.get("multiplier", 9.1),
                          sym_key=sym["symbol"], n=len(candles),
                          start=ts_fmt(candles[0]["ts"]), end=ts_fmt(candles[-1]["ts"]))
        o = [c["o"] for c in candles]
        h = [c["h"] for c in candles]
        l = [c["l"] for c in candles]
        cl = [c["c"] for c in candles]
        st = super_trend(o, h, l, cl, periods=p.get("periods", 15),
                         multiplier=p.get("multiplier", 9.1),
                         src=p.get("src", "hl2"),
                         change_atr=p.get("change_atr", True))
        trend, flips = st["trend"], st["flips"]
        start = next((i for i in range(len(trend)) if trend[i] is not None), 0)
        eff = len(trend) - start
        rows[name]["eff_bars"] = eff
        rows[name]["flips"] = len(flips)
        rows[name]["flip_per_100"] = round(len(flips) / eff * 100, 2)
        # 平均趋势持续
        bounds = [start] + [f["i"] for f in flips] + [len(trend)]
        segs = [bounds[i + 1] - bounds[i] for i in range(len(bounds) - 1)]
        rows[name]["seg_mean"] = round(statistics.mean(segs), 1)
        rows[name]["seg_median"] = statistics.median(segs)
        rows[name]["segs"] = len(segs)
        # ER 滑动均值 + 状态占比
        ers = [efficiency_ratio(candles[:i + 1]) for i in range(len(candles))]
        valid = [e for e in ers if e is not None]
        rows[name]["er_mean"] = round(statistics.mean(valid), 4) if valid else None
        rows[name]["er_trend_pct"] = round(
            sum(1 for e in valid if e >= 0.30) / len(valid) * 100, 1) if valid else None
        rows[name]["er_weak_pct"] = round(
            sum(1 for e in valid if 0.15 <= e < 0.30) / len(valid) * 100, 1) if valid else None
        rows[name]["er_range_pct"] = round(
            sum(1 for e in valid if e < 0.15) / len(valid) * 100, 1) if valid else None
        # ATR ratio 序列（ATR14 / 过去50均值），同 regime_scoring._atr_soft_part
        atr14 = ta_atr(h, l, cl, 14)
        ratios = []
        for i in range(len(atr14)):
            if atr14[i] is None:
                continue
            win = [a for a in atr14[max(0, i - 49):i + 1] if a is not None]
            if len(win) >= 15 and win[-1] > 0:
                ratios.append(atr14[i] / (sum(win) / len(win)))
        rows[name]["atr_ratio_mean"] = round(statistics.mean(ratios), 3) if ratios else None
        rows[name]["atr_ratio_std"] = round(
            statistics.pstdev(ratios), 3) if ratios else None
        # 绝对波动：ATR(periods)/close 均值(%)
        atr_p = st["atr"]
        pct = [atr_p[i] / cl[i] * 100 for i in range(len(atr_p))
               if atr_p[i] is not None and cl[i]]
        rows[name]["atr_pct_mean"] = round(statistics.mean(pct), 2) if pct else None
        rows[name]["atr_pct_std"] = round(statistics.pstdev(pct), 2) if pct else None
    return rows


def _cell(r, nm, fmt):
    if fmt is None:
        return "-"
    v = fmt(r, nm)
    return f"{v:>18}"


def main():
    rows = stats_rows()
    jp = os.path.join(DATA, "_engine_classify.json")
    live_n = {}
    if os.path.exists(jp):
        for r in json.load(open(jp, encoding="utf-8")):
            if r.get("v1"):
                live_n[r["name"]] = r["v1"].get("n")
    W = 18

    def out(label, getter):
        cells = [f"{label:<20}"]
        for nm, _ in CASES:
            v = getter(rows[nm], nm)
            cells.append(f"{str(v):>{W}}")
        print("".join(cells))

    out("窗口", lambda r, nm: f"{r['start']} ~ {r['end'][-5:]}")
    out("K线数", lambda r, nm: f"{r['n']:,}")
    out("ST 参数", lambda r, nm: f"p={r['per']} x {r['mul']:g}")
    out("ST flip / 100根", lambda r, nm: f"{r['flip_per_100']:.2f}")
    out("平均趋势持续(bar)", lambda r, nm: f"{r['seg_mean']:.1f}")
    out("趋势持续中位数", lambda r, nm: f"{r['seg_median']:.0f}")
    out("ER 均值", lambda r, nm: f"{r['er_mean']:.4f}")
    out("信号数量(翻转)", lambda r, nm: f"{r['flips']}")
    out("信号密度 / 100根", lambda r, nm: f"{r['flips'] / r['eff_bars'] * 100:.1f}")
    out("线上成交笔数(V1)", lambda r, nm: f"{live_n.get(nm, '-')}")
    out("ATR ratio 波动(std)", lambda r, nm: f"{r['atr_ratio_std']:.3f}")
    out("ATR ratio 均值", lambda r, nm: f"{r['atr_ratio_mean']:.3f}")
    out("ATR% 均值", lambda r, nm: f"{r['atr_pct_mean']:.2f}%")
    print(f"\n{'状态分布(根占比)':<20}{'NVDA':>{W}}{'MU':>{W}}{'SNDK':>{W}}")
    for k, label in [("er_trend_pct", "趋势 ER>=0.30"),
                     ("er_weak_pct", "弱趋势 0.15-0.30"),
                     ("er_range_pct", "震荡 ER<0.15")]:
        out(label, lambda r, nm, k=k: f"{r[k]}%")
    with open(os.path.join(DATA, "_stock_compare.json"), "w",
              encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1, default=str)
    print("\nWrote _stock_compare.json")


if __name__ == "__main__":
    main()
