"""形态识别页 vs 策略学习页：两套「趋势环境识别」在 SNDK 1h 上的回撤/收益对比。

同一批 SuperTrend(10/3.0) 翻转信号，套不同「趋势环境识别」过滤器，跑同一套出场
（TP1 1.5% 平 70% + 保本 + 跟随 ST 轨道，与 bt_pattern_page 完全一致），
比较：笔数 / 胜率 / 收益率 / 盈亏比 / 最大回撤 / 最长连亏。

过滤器：
  ALL      全部翻转（无过滤基线）
  PAT_FORM 形态识别页·4h 形态方向（recognize_pattern，block_4h 口径：仅拦 4h 明确反向）
  PAT_REG  形态识别页·market_regime 可交易（classify_market_regime.tradeable）
  SL_DIS   策略学习页·剔除震荡无序期（regime6 is_disorder）
  SL_TRD   策略学习页·仅趋势类（trend_run/trend_init/trend_end）
  V3       页面 V3 过滤（filter_v3，参考列）

用法：
  python bt_regime_compare.py SNDK-USDT-SWAP --base_tf 1h --h4_tf 4h
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bt_pattern_page import load, backtest, metrics
from indicators import super_trend, ta_adx
from pattern_recog import recognize as recognize_pattern
import market_regime


# ── 策略学习页的 regime6 分类（从 strategy_learning.py 原样搬来，避免导入重 ML 依赖）──
def _sharp_features(candles, i, atr, flips_sorted, htf_ts, htf_cl, side):
    cl = [c["c"] for c in candles]
    hi = [c["h"] for c in candles]
    lo = [c["l"] for c in candles]
    vo = [c["vol"] for c in candles]
    avg = lambda xs: (lambda ys: (sum(ys) / len(ys)) if ys else 0.0)([x for x in xs if x is not None])
    out = {}
    if i >= 50 and atr:
        a50 = avg(atr[i - 50:i])
        out["atr_ratio50"] = round(atr[i] / a50, 4) if a50 else 0.0
    else:
        out["atr_ratio50"] = 0.0
    if i >= 20:
        rets = [abs(cl[t] - cl[t - 1]) for t in range(i - 19, i + 1)]
        rv = (sum(r * r for r in rets) / len(rets)) ** 0.5
        a_ref = avg(atr[i - 50:i]) if i >= 50 else (atr[i] if atr else 0.0)
        out["realized_vol20"] = round(rv / (a_ref + 1e-12), 4)
    else:
        out["realized_vol20"] = 0.0
    if i >= 20:
        hh = max(hi[i - 20:i + 1]); ll = min(lo[i - 20:i + 1])
        rng = (hh - ll) or 1e-12
        out["range_pos20"] = round((cl[i] - ll) / rng, 4)
        out["pullback20"] = round(((hh - cl[i]) / rng) if side > 0 else ((cl[i] - ll) / rng), 4)
        bodies = [abs(candles[t]["c"] - candles[t]["o"]) for t in range(i - 19, i + 1)]
        out["body_frac20"] = round(avg(bodies) / (avg(atr[i - 20:i + 1]) + 1e-12), 4)
    else:
        out["range_pos20"] = 0.0; out["pullback20"] = 0.0; out["body_frac20"] = 0.0
    if i >= 20:
        v20 = avg(vo[i - 20:i]); v50 = avg(vo[i - 50:i]) if i >= 50 else v20
        out["vol_ratio20"] = round(vo[i] / (v20 + 1e-12), 4)
        out["vol_ratio50"] = round(vo[i] / (v50 + 1e-12), 4)
    else:
        out["vol_ratio20"] = 0.0; out["vol_ratio50"] = 0.0
    fd = sum(1 for f in flips_sorted if i - 20 < f <= i) / 20.0
    out["flipDensity20"] = round(fd, 4)
    if htf_cl and i >= 10:
        k = bisect.bisect_right(htf_ts, candles[i]["ts"]) - 1
        if k >= 6:
            mom1h = cl[i] - cl[max(0, i - 10)]
            mom4h = htf_cl[k] - htf_cl[k - 6]
            out["htf_div"] = round(-1.0 if (mom1h * mom4h < 0) else 1.0, 4)
            out["mom1h_norm"] = round(mom1h / (atr[i] + 1e-12), 4)
        else:
            out["htf_div"] = 0.0; out["mom1h_norm"] = 0.0
    else:
        out["htf_div"] = 0.0; out["mom1h_norm"] = 0.0
    return out


def _classify_regime6(m, sharp, feats, side):
    adx = m.get("adx") or 0.0
    adx_slope = m.get("adx_slope") or 0.0
    er = m.get("er") or 0.0
    mom = m.get("momentum") or 0.0
    flip20 = sharp.get("flipDensity20") or 0.0
    atr_ratio = sharp.get("atr_ratio50") or 0.0
    htf_div = sharp.get("htf_div") or 0.0
    if flip20 >= 0.10 and adx < 23:
        return ("choppy_disorder", "震荡无序期", True)
    trending = (adx >= 23) and (er > 0.22)
    if trending:
        if adx_slope < -5 or (htf_div < 0 and adx >= 25):
            return ("trend_end", "趋势末期", False)
        if adx < 28 and adx_slope > 3:
            return ("trend_init", "趋势启动", False)
        return ("trend_run", "趋势", False)
    if adx_slope < -5 and flip20 < 0.10:
        return ("range_start", "震荡开启", False)
    if atr_ratio < 1.0 and adx < 18 and flip20 < 0.05:
        return ("range_end", "震荡末期", False)
    return ("range_mid", "震荡", False)


def fmt(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", nargs="?", default="SNDK-USDT-SWAP")
    ap.add_argument("--base_tf", default="1h")
    ap.add_argument("--h4_tf", default="4h")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()

    base, h4 = load(a.symbol, use_cache=not a.refresh, base_tf=a.base_tf, h4_tf=a.h4_tf)
    if not base:
        return
    tss = [c["ts"] for c in base]
    print(f"{a.base_tf} 数据：{fmt(tss[0])} ~ {fmt(tss[-1])}（{len(base)} 根）")

    o = [c["o"] for c in base]; h = [c["h"] for c in base]
    l = [c["l"] for c in base]; cl = [c["c"] for c in base]
    st = super_trend(o, h, l, cl, periods=10, multiplier=3.0, change_atr=True)
    atr = st["atr"]
    flips = sorted(f["i"] for f in st["flips"])
    up_plot, dn_plot = st["up_plot"], st["dn_plot"]
    adx14 = ta_adx(h, l, cl, 14)

    # 4h 形态方向（形态识别页 block_4h 口径）
    pat = recognize_pattern([{"ts": c["ts"], "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"]}
                             for c in h4])["pattern"]
    pts = [p["ts"] for p in pat]
    pmap = {p["ts"]: p for p in pat}
    h4_ts = [c["ts"] for c in h4]
    h4_cl = [c["c"] for c in h4]

    sigs = []
    for i in flips:
        if i >= len(base) or i < 50:
            continue
        sd = 1 if st["trend"][i] == 1 else -1
        pdir = None
        j = bisect.bisect_right(pts, base[i]["ts"]) - 1
        if j >= 0:
            pdir = pmap[pts[j]].get("dir")

        # 形态识别页·market_regime
        mr = market_regime.classify_market_regime(
            cl[:i + 1], h[:i + 1], l[:i + 1], atr[:i + 1], adx14[:i + 1], flips, i, sd)
        m = mr["metrics"]

        # 策略学习页·regime6
        sharp = _sharp_features(base, i, atr, flips, h4_ts, h4_cl, sd)
        reg6 = _classify_regime6(m, sharp, {}, sd)

        sigs.append({
            "i": i, "ts": base[i]["ts"], "dir": sd, "entry": cl[i],
            "pass_4h": (pdir != -sd),                       # 形态识别页·4h 形态方向
            "pat_tradeable": mr["tradeable"],                # 形态识别页·market_regime 可交易
            "is_disorder": reg6[2],                          # 策略学习页·震荡无序期
            "reg6": reg6[0], "reg6_cn": reg6[1],
        })

    print(f"信号总数（i>=50）= {len(sigs)}")

    FILTERS = [
        ("ALL",     "全部翻转（无过滤）",              lambda s: True),
        ("PAT_FORM","形态识别页·4h形态方向",           lambda s: s["pass_4h"]),
        ("PAT_REG", "形态识别页·market_regime可交易",   lambda s: s["pat_tradeable"]),
        ("SL_DIS",  "策略学习页·剔除震荡无序",          lambda s: not s["is_disorder"]),
        ("SL_TRD",  "策略学习页·仅趋势类",              lambda s: s["reg6"] in ("trend_run", "trend_init", "trend_end")),
    ]

    res = {name: backtest([s for s in sigs if ok(s)], h, l, cl, up_plot, dn_plot, set(flips))
           for name, _, ok in FILTERS}
    mm = {name: metrics(v) for name, v in res.items()}

    hdr = f"{'过滤器':<28}{'笔数':>6}{'胜率':>8}{'收益率%':>9}{'盈亏比':>8}{'最大回撤%':>11}{'最长连亏':>9}"
    print("\n" + hdr)
    print("-" * len(hdr))
    for name, lab, _ in FILTERS:
        x = mm[name]
        po = "   inf" if x["payoff"] == float("inf") else f"{x['payoff']:>8.2f}"
        print(f"{lab:<28}{x['n']:>6}{x['wr']:>7.1f}%{x['ret']:>9.2f}{po}{x['max_dd']:>11.2f}{x['streak']:>9}")

    # 策略学习页 regime6 分布 + 各 regime 的回撤
    from collections import Counter, defaultdict
    print("\n── 策略学习页 regime6 分布（含各档回撤）──")
    by_reg = defaultdict(list)
    for s in sigs:
        by_reg[s["reg6"]].append(s)
    for reg, items in sorted(by_reg.items(), key=lambda kv: -len(kv[1])):
        tr = backtest(items, h, l, cl, up_plot, dn_plot, set(flips))
        mreg = metrics(tr)
        po = "   inf" if mreg["payoff"] == float("inf") else f"{mreg['payoff']:.2f}"
        print(f"  {reg:<16} n={len(items):>3}  胜率{mreg['wr']:>6.1f}%  收益{mreg['ret']:>8.2f}%  "
              f"盈亏比{po}  回撤{mreg['max_dd']:>7.2f}%")

    # 形态识别页 market_regime 分布
    print("\n── 形态识别页 market_regime 可交易/不可交易 对照 ──")
    tr_t = backtest([s for s in sigs if s["pat_tradeable"]], h, l, cl, up_plot, dn_plot, set(flips))
    tr_f = backtest([s for s in sigs if not s["pat_tradeable"]], h, l, cl, up_plot, dn_plot, set(flips))
    mt, mf = metrics(tr_t), metrics(tr_f)
    print(f"  可交易(tradeable=True) : n={mt['n']} 胜率{mt['wr']:.1f}% 收益{mt['ret']:+.2f}% 回撤{mt['max_dd']:.2f}%")
    print(f"  不可交易              : n={mf['n']} 胜率{mf['wr']:.1f}% 收益{mf['ret']:+.2f}% 回撤{mf['max_dd']:.2f}%")


if __name__ == "__main__":
    main()
