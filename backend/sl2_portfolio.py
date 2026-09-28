# -*- coding: utf-8 -*-
"""
多资产组合评估 —— 回答「广度上去以后还有没有边际」
==============================================================================

为什么是这个脚本
----------------
BTC 单资产的结论已经封顶：最好的格子只有 45~70 笔交易、有效广度 1~5 笔、
95% 置信区间全部跨过 0，而且总净值打不过买入持有。
瓶颈不是模型，是**样本量**。这个脚本把「同一套规则」原样搬到多资产上，
看合并后的样本能不能给出一个**统计上可判定**的答案。

规则是**预先固定**的，不许在这里调参
------------------------------------
    SuperTrend(10, mult) 翻向开仓、下个反向翻向平仓；
    开仓时若 ATR% 在过去 W 笔的滚动分位 > filter-q，则放弃这笔（波动率过滤）。

默认 mult=4.0 / tf=4h / filter-q=0.70，这一组来自 BTC 上「持有周期 × 倍数」扫描里
方向一致的区域（宽止损、长持有），**不是**在验证集上挑出来的最优值。
在本脚本里改参数就是在调参，会重新引入选择偏差 —— 所以脚本会把你用的参数打印出来留档。

⚠️ 关于「独立性」的诚实处理
加密资产之间高度相关，名义上 n 笔交易的真实信息量远小于 n。
所以置信区间用**按月分块的 bootstrap**：同一个月内所有资产的交易视为一个整体，
重采样时整块一起进出。这样跨资产的同涨同跌不会被当成独立样本重复计数。

用法
----
    cd backend
    python sl2_portfolio.py                                   # 用库里现有全部资产
    python sl2_portfolio.py --symbols BTC-USDT,ETH-USDT,SOL-USDT
    python sl2_portfolio.py --tf 4 --mult 4 --filter-q 0.70
    python sl2_portfolio.py --filter-q 0                       # 关掉过滤，看纯信号
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from sl2_tf_sweep import load_db, resample, breadth, buy_hold, boot_ci
from sl2_voltarget import build_trades, entry_context, atr_pct_at
from sl2_volfilter import vol_filter_mask, year_of


def available_symbols() -> list[str]:
    import sqlite3
    con = sqlite3.connect("candle_data.db")
    try:
        rows = con.execute("select distinct symbol from candles where tf='1h' order by symbol").fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


def month_key(ts) -> str:
    v = ts
    if isinstance(v, str):
        v = float(v)
    sec = v / 1000.0 if v > 1e11 else v
    import datetime as dt
    return dt.datetime.fromtimestamp(sec, tz=dt.timezone.utc).strftime("%Y-%m")


def run_asset(symbol: str, tf: int, mult: float, filter_q: float, fee: float):
    """在一套资产上跑固定规则，返回全部交易及其「是否通过波动率过滤」标记。"""
    c1h = load_db(symbol, "1h")
    if len(c1h) < 500:
        return None
    cs = resample(c1h, tf)
    if len(cs) < 300:
        return None
    ctx = entry_context(cs)
    trades, _ = build_trades(cs, mult=mult)
    if len(trades) < 20:
        return None
    apct = np.asarray([atr_pct_at(ctx, t["i"]) or np.nan for t in trades], float)

    # 过滤规则统一走唯一实现，禁止在本脚本里另写窗口参数
    min_hist = max(20, min(50, len(trades) // 3))
    mask, _ = vol_filter_mask(apct, filter_q if filter_q else 0.70, min_history=min_hist)

    out = []
    for t, k in zip(trades, mask):
        out.append({"ts": t["ts"], "net": t["gross"] - 2 * fee, "gross": t["gross"],
                    "bars": t["bars"], "symbol": symbol, "kept": bool(k)})
    return {"symbol": symbol, "bars": len(cs), "years": len(cs) * tf / (24 * 365),
            "n_all": len(trades), "trades": out, "bh": buy_hold(c1h),
            "min_hist": min_hist}


def block_bootstrap_ci(vals: np.ndarray, months: np.ndarray, reps: int = 4000, seed: int = 23):
    """按月分块 bootstrap —— 同月不同资产的交易整块进出，避免高相关资产被当独立样本。"""
    uniq = np.unique(months)
    if len(uniq) < 6 or len(vals) < 20:
        return (float("nan"), float("nan"))
    groups = {m: vals[months == m] for m in uniq}
    sums = np.array([groups[m].sum() for m in uniq], float)
    cnts = np.array([len(groups[m]) for m in uniq], float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(uniq), size=(reps, len(uniq)))
    means = sums[idx].sum(axis=1) / cnts[idx].sum(axis=1)
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


def monthly_portfolio(all_trades: list[dict], n_assets: int, assets: list[str]):
    """等权组合的月度收益序列：每个资产分 1/N 资金，月度收益 = 各资产当月收益之和 / N。"""
    buckets: dict[str, dict[str, float]] = {}
    for t in all_trades:
        m = month_key(t["ts"])
        buckets.setdefault(m, {})
        buckets[m][t["symbol"]] = buckets[m].get(t["symbol"], 0.0) + t["net"]
    months = sorted(buckets)
    if not months:
        return None
    rets = np.array([sum(buckets[m].values()) / n_assets for m in months], float)
    eq = np.cumprod(1 + rets / 100.0)
    peak = np.maximum.accumulate(eq)
    dd = float(np.min(eq / peak - 1)) * 100
    sharpe = (rets.mean() / rets.std() * np.sqrt(12)) if rets.std() > 0 else 0.0
    return {"months": len(months), "total": float((eq[-1] - 1) * 100),
            "cagr": float((eq[-1] ** (12 / len(months)) - 1) * 100) if eq[-1] > 0 else -100.0,
            "dd": dd, "sharpe": float(sharpe), "eq": eq.tolist(), "month_labels": months}


def main(argv=None):
    ap = argparse.ArgumentParser(description="多资产组合评估（固定规则，不调参）")
    ap.add_argument("--symbols", default="", help="逗号分隔，留空=库里全部")
    ap.add_argument("--tf", type=int, default=4, help="周期（小时）")
    ap.add_argument("--mult", type=float, default=4.0)
    ap.add_argument("--filter-q", type=float, default=0.70, help="0 表示关闭过滤")
    ap.add_argument("--fee", type=float, default=0.05)
    ap.add_argument("--reps", type=int, default=4000)
    ap.add_argument("--export", default="", help="导出组合净值曲线 JSON")
    a = ap.parse_args(argv)

    syms = [s.strip() for s in a.symbols.split(",") if s.strip()] or available_symbols()
    print("=" * 100)
    print("多资产组合评估 —— 固定规则")
    print("=" * 100)
    print(f"规则（预先固定，未在本脚本内调参）：SuperTrend(10,{a.mult}) · 周期 {a.tf}h · "
          f"ATR 过滤 {'关' if not a.filter_q else f'{a.filter_q:.2f} 分位'} · 单边费率 {a.fee}%")
    print(f"资产：{', '.join(syms)}\n")

    results, ok = [], []
    for s in syms:
        r = run_asset(s, a.tf, a.mult, a.filter_q, a.fee)
        if r is None:
            print(f"[跳过] {s}：数据不足（需要 ≥500 根 1h）")
            continue
        results.append(r)
        ok.append(s)
        all_nets = np.asarray([t["net"] for t in r["trades"]], float)
        kept = np.asarray([t["kept"] for t in r["trades"]], bool)
        kn = all_nets[kept]
        print(f"[{s}] {r['bars']} 根 {a.tf}h ≈ {r['years']:.2f} 年 · "
              f"信号 {r['n_all']} 笔 · 不过滤净均值 {all_nets.mean():+.3f}% · "
              f"过滤后 {len(kn)} 笔 净均值 {kn.mean() if len(kn) else float('nan'):+.3f}% · "
              f"买入持有 {r['bh']['total']:+.1f}%")

    if not results:
        print("\n[失败] 没有可用资产")
        return 1

    def pool_report(use_filter: bool) -> dict | None:
        pool = [t for r in results for t in r["trades"] if (t["kept"] if use_filter else True)]
        title = "过滤后（ATR 分位规则生效）" if use_filter else "不过滤（全部信号）"
        if len(pool) < 30:
            print(f"\n[{title}] 只有 {len(pool)} 笔，样本不足，跳过")
            return None
        vals = np.asarray([t["net"] for t in pool], float)
        gross = np.asarray([t["gross"] for t in pool], float)
        months = np.asarray([month_key(t["ts"]) for t in pool])
        ci = block_bootstrap_ci(vals, months, a.reps)
        ci_plain = boot_ci(vals, a.reps)
        br = breadth(gross)
        pf = monthly_portfolio(pool, len(results), ok)
        print(f"\n【{title}】")
        print(f"  交易 {len(pool)} 笔（名义）· 跨 {len(np.unique(months))} 个月 · {len(results)} 个资产")
        print(f"  每笔净期望 {vals.mean():+.3f}%  ·  胜率 {(vals > 0).mean()*100:.1f}%  ·  "
              f"毛收益中位 {np.median(gross):+.3f}%")
        print(f"  95%CI（简单 bootstrap，偏乐观）   [{ci_plain[0]:+.3f}, {ci_plain[1]:+.3f}]")
        print(f"  95%CI（按月分块，应采用这个）     [{ci[0]:+.3f}, {ci[1]:+.3f}]  → "
              f"{'下界>0，边际为正' if ci[0] > 0 else '跨过 0，**无法判定有正边际**'}")
        print(f"  有效广度：去掉前 {br} 笔后合并毛收益转负（占 {br/max(len(pool),1)*100:.1f}%）")
        if pf:
            bh_rets = [((1 + r["bh"]["total"] / 100) ** (1 / max(r["bh"]["years"], 0.1)) - 1)
                       for r in results]
            bh_avg = float(np.mean(bh_rets)) * 100
            print(f"  等权组合：{pf['months']} 个月 · 总收益 {pf['total']:+.1f}% · "
                  f"年化 {pf['cagr']:+.1f}% · 月频夏普 {pf['sharpe']:.2f} · 回撤 {pf['dd']:.1f}%")
            print(f"  同期等权买入持有年化 {bh_avg:+.1f}%  → "
                  f"{'组合更优' if pf['cagr'] > bh_avg else '**组合不如躺着持有**'}")
        return {"pool": pool, "ci": ci, "breadth": br, "pf": pf}

    r_on = pool_report(True)
    r_off = pool_report(False)

    # 逐年稳健性（用过滤后口径）
    ref = r_on or r_off
    if ref:
        print(f"\n【逐年拆解】{'过滤后' if r_on else '不过滤'}的每笔净期望（看有没有某一年在单扛）")
        print(f"{'年份':<8}{'笔数':>7}{'净期望%':>12}{'胜率%':>9}")
        yrs: dict[str, list] = {}
        for t in ref["pool"]:
            yrs.setdefault(year_of(t["ts"]), []).append(t["net"])
        for y in sorted(yrs):
            v = np.asarray(yrs[y], float)
            print(f"{y:<8}{len(v):>7}{v.mean():>12.3f}{(v > 0).mean()*100:>9.1f}")

    if a.export and ref and ref["pf"]:
        Path(a.export).write_text(json.dumps(
            {"meta": {"symbols": ok, "tf": a.tf, "mult": a.mult, "filter_q": a.filter_q,
                      "fee": a.fee, "n_pool": len(ref["pool"]), "ci_block": ref["ci"],
                      "breadth": ref["breadth"]},
             "months": ref["pf"]["month_labels"], "equity": ref["pf"]["eq"]},
            ensure_ascii=False), encoding="utf-8")
        print(f"\n[导出] 组合净值曲线 → {a.export}")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())
