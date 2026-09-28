# -*- coding: utf-8 -*-
"""
风险口径评估：把「低波动过滤 + 长持有」定性为「风控覆盖层」还是「边际来源」
==============================================================================

背景
----
本项目到目前为止唯一跨样本复现的东西，是「开仓波动率过滤」：
28 个可比格子中 23 个变好（BTC 4.17y / BTC 4.73y / ETH 2.73y）。
但它的表现一直是**逐笔期望**口径，而这个口径有两个盲点：

  1. 逐笔期望为正 ≠ 净值更好（笔数与持有期不同，复利路径完全不同）；
  2. 逐笔期望为正**可能只是低 beta**——BTC 长期上涨，任何"少下单"的规则都会显得期望更高。

本脚本就是来堵这两个盲点的。规则**预先固定、不调参**：

    SuperTrend(10, 4.0) 翻向开仓、下个反向翻向平仓；
    开仓时若「当时 ATR%」在过去一段的滚动分位 > 0.70，则放弃这笔。

然后做三件事
------------
① **全风险指标**：用**逐月精确净值曲线**（按平仓时点归属，不是按开仓月粗略分摊）
   算 总收益 / 年化 / 年化波动 / 夏普 / 索提诺 / 最大回撤 / Calmar。
② **alpha-beta 分解**：把策略月度收益对**买入持有月度收益**做回归。
   · beta 明显 < 1 且 alpha 不显著 → 它只是「减配版的持有」，属于**风险覆盖层**；
   · beta ≈ 0 且 alpha 显著 > 0 → 才是**独立的边际来源**。
   这一步是判定"风控覆盖层 vs 边际来源"的**决定性检验**。
③ **beta 对齐**：把策略月度收益按 1/beta 放大到与买入持有同 beta，再看回撤——
   回答"如果我只想要同样的市场暴露，这条规则有没有让我少受点回撤"。

判定规则（事先写在代码里，不事后调整）
--------------------------------------
  · 夏普更高 且 beta<0.9 且 alpha 不显著  → 有效的**风险覆盖层**（风险调整后更优，但不是独立边际）
  · alpha 显著 > 0（t > 2）              → **独立边际来源**
  · 夏普更低 且 beta 接近或大于 1        → 被买入持有**支配**，没有使用价值

用法
----
    cd backend
    python sl2_riskeval.py
    python sl2_riskeval.py --tf 1 --mult 3.0          # 短周期对照
    python sl2_riskeval.py --export ml_runs/_diag/riskeval.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np

from sl2_tf_sweep import load_db, resample, breadth, boot_ci
from sl2_voltarget import build_trades, entry_context, atr_pct_at
from sl2_volfilter import vol_filter_mask

ASSETS = ["BTC-USDT", "ETH-USDT"]
ANN = 12.0


# ──────────────────────────────────────────────────────────────
def mk_month(ts_ms) -> str:
    return dt.datetime.fromtimestamp(ts_ms / 1000.0, dt.UTC).strftime("%Y-%m")


def monthly_bh(cs):
    """买入持有的月度收益（在**同一根重采样序列**上算，保证与策略同周期、同口径）。"""
    last: dict[str, float] = {}
    for c in cs:
        last[mk_month(c["ts"])] = c["c"]
    ms = sorted(last)
    out = {}
    for i in range(1, len(ms)):
        out[ms[i]] = (last[ms[i]] / last[ms[i - 1]] - 1) * 100
    return out


def strategy_bar_returns(cs, trades, mask, fee):
    """**逐根 K 线**的策略收益序列 —— 这是本脚本的关键。

    为什么不能只用「按平仓月归属」：
        4h×4.0 在 4.7 年里只有 ~150 笔交易，摊到 56 个月里大量月份收益是 0。
        拿这种被零值污染的月度序列做回归，beta 和 R² 会被机械地压到 0，完全没有意义。

    做法：持仓期间逐根计算盯市收益，费率扣在开仓腿与平仓腿各自那根 K 线上。
        过滤掉的交易期间 → 空仓（收益 0），这正是"减配"效应的正确体现。
        每笔交易合计仍严格等于 gross - 2*fee。
    """
    cl = np.asarray([c["c"] for c in cs], float)
    bar = np.zeros(len(cs), float)
    in_mkt = np.zeros(len(cs), bool)
    for t, k in zip(trades, mask):
        if not k:
            continue
        i, j, side = t["i"], t["j"], t["side"]
        for b in range(i, j):
            bar[b] += (cl[b + 1] / cl[b] - 1.0) * 100.0 * side
            in_mkt[b] = True
        bar[i] -= fee          # 开仓腿
        bar[j - 1] -= fee      # 平仓腿
    return bar, in_mkt


def to_monthly(cs, bar):
    """把逐根收益按月复利成月度收益（%）—— 盯市口径，不再有零值污染。"""
    acc: dict[str, float] = {}
    for c, r in zip(cs, bar):
        m = mk_month(c["ts"])
        acc[m] = acc.get(m, 1.0) * (1 + r / 100.0)
    return {m: (v - 1) * 100 for m, v in acc.items()}


def trade_month_factors(cs, trades, fee):
    """把每笔交易拆成 {月份: 乘数(1+r)}；费率扣在开仓腿与平仓腿各自那根 K 线上。

    这是**唯一**一份实现（`sl2_filter_recheck` / `sl2_chop_filter` 都从这里 import）。
    好处：任意子集的组合净值只需 O(笔数) 乘法即可还原，不必每次重算逐根盯市，
    因此像"等量随机删单 200 次"这种对照实验才跑得动。
    口径等价性：每笔交易乘数之积 == 1 + (gross - 2*fee)/100。
    """
    cl = np.asarray([c["c"] for c in cs], float)
    f = fee / 100.0
    out = []
    for t in trades:
        i, j, side = t["i"], t["j"], t["side"]
        fac: dict[str, float] = {}
        for b in range(i, j):
            r = (cl[b + 1] / cl[b] - 1.0) * side
            if b == i:
                r -= f
            if b == j - 1:
                r -= f
            m = mk_month(cs[b]["ts"])
            fac[m] = fac.get(m, 1.0) * (1.0 + r)
        out.append(fac)
    return out


def to_pct(factors, idx, months):
    """把选中的交易因子按月份累乘成月度收益（%），列 `months` 中缺月记 0。"""
    acc = {m: 1.0 for m in months}
    for k in idx:
        for m, v in factors[k].items():
            if m in acc:
                acc[m] *= v
    return {m: (acc[m] - 1.0) * 100.0 for m in months}


def sign_test(k_pos: int, k_neg: int) -> float:
    """符号检验（连续性校正）：k_pos 胜 / k_neg 负，返回双侧 p。

    用途：当有 N 个互相独立的格子、每格只有"变好/变坏"这一位信息时，
    逐格 p 值会因为多重检验而集体失真；此时对"N 格里有几格变好"做一次符号检验，
    比盯着单格的最小 p 值靠谱得多。
    """
    n = k_pos + k_neg
    if n == 0:
        return 1.0
    from math import erfc, sqrt
    z = max(abs(k_pos - n / 2) - 0.5, 0.0) / np.sqrt(n / 4)
    return float(erfc(z / sqrt(2)))


def perf(monthly_pct: dict, months: list[str], fee_note=""):
    """在给定的月份序列上算全套风险指标（缺失月份收益记 0）。"""
    r = np.array([monthly_pct.get(m, 0.0) / 100.0 for m in months], float)
    if len(r) < 6:
        return None
    eq = np.cumprod(1 + r)
    years = len(r) / ANN
    total = eq[-1] - 1
    cagr = (eq[-1] ** (1 / years) - 1) if eq[-1] > 0 else -1.0
    vol = float(r.std(ddof=1) * np.sqrt(ANN))
    sharpe = float(r.mean() / r.std(ddof=1) * np.sqrt(ANN)) if r.std(ddof=1) > 0 else 0.0
    dn = r[r < 0]
    sortino = (float(r.mean() / dn.std(ddof=1) * np.sqrt(ANN))
               if len(dn) > 1 and dn.std(ddof=1) > 0 else 0.0)
    peak = np.maximum.accumulate(eq)
    dd = float(np.min(eq / peak - 1))
    return {"months": len(r), "total": total * 100, "cagr": cagr * 100,
            "vol": vol * 100, "sharpe": sharpe, "sortino": sortino,
            "maxdd": dd * 100, "calmar": (cagr / abs(dd)) if dd < 0 else 0.0,
            "eq": eq.tolist()}


def alpha_beta(st_pct: dict, bh_pct: dict, months: list[str]):
    """策略月度收益 ~ alpha + beta × 买入持有月度收益。

    单位说明：两侧都是**百分数**（5.0 表示 5%），所以返回的 alpha 也是百分数/月，**不要再乘 100**。
    """
    y = np.array([st_pct.get(m, 0.0) for m in months], float)
    x = np.array([bh_pct.get(m, 0.0) for m in months], float)
    if len(y) < 8 or x.std() == 0:
        return None
    xm, ym = x.mean(), y.mean()
    sxx = ((x - xm) ** 2).sum()
    b = ((x - xm) * (y - ym)).sum() / sxx
    a = ym - b * xm
    resid = y - (a + b * x)
    n = len(y)
    if n > 2 and sxx > 0:
        sigma2 = (resid ** 2).sum() / (n - 2)
        se_a = np.sqrt(sigma2 * (1 / n + xm ** 2 / sxx))
        t_a = a / se_a if se_a > 0 else 0.0
    else:
        t_a = 0.0
    ss_tot = ((y - ym) ** 2).sum()
    r2 = 1 - (resid ** 2).sum() / ss_tot if ss_tot > 0 else 0.0
    return {"alpha": float(a), "beta": float(b), "t_alpha": float(t_a), "r2": float(r2)}


def scale_months(monthly_pct: dict, months: list[str], k: float):
    return {m: monthly_pct.get(m, 0.0) * k for m in months}


# ──────────────────────────────────────────────────────────────
def evaluate_sample(name, candles_1h, tf, mult, filter_q, fee):
    cs = resample(candles_1h, tf)
    if len(cs) < 300:
        return None
    ctx = entry_context(cs)
    trades, _ = build_trades(cs, mult=mult)
    if len(trades) < 20:
        return None
    apct = np.asarray([atr_pct_at(ctx, t["i"]) or np.nan for t in trades], float)
    min_hist = max(20, min(50, len(trades) // 3))
    mask, _ = vol_filter_mask(apct, filter_q, min_history=min_hist)
    # 把净收益挂到交易上（原 trades 只有 gross）
    for t in trades:
        t["net"] = t["gross"] - 2 * fee

    mask_off = np.ones(len(trades), bool)
    bar_off, in_off = strategy_bar_returns(cs, trades, mask_off, fee)
    bar_on, in_on = strategy_bar_returns(cs, trades, mask, fee)
    st_off = to_monthly(cs, bar_off)
    st_on = to_monthly(cs, bar_on)
    bh = monthly_bh(cs)

    bh_months = sorted(bh)
    start = mk_month(cs[trades[0]["i"]]["ts"])
    months = [m for m in bh_months if m >= start]
    if len(months) < 12:
        return None

    res = {"name": name, "tf": tf, "mult": mult, "years": len(cs) * tf / (24 * 365),
           "months": len(months), "n_all": len(trades),
           "n_on": int(mask.sum()),
           "in_mkt_off": float(in_off.mean() * 100),
           "in_mkt_on": float(in_on.mean() * 100)}
    res["off"] = perf(st_off, months)
    res["on"] = perf(st_on, months)
    res["bh"] = perf(bh, months)

    nets_off = np.asarray([t["net"] for t in trades], float)
    sel_on = [t for t, k in zip(trades, mask) if k]
    nets_on = np.asarray([t["net"] for t in sel_on], float)
    gross_on = np.asarray([t["gross"] for t in sel_on], float)
    res["exp_off"] = float(nets_off.mean())
    res["exp_on"] = float(nets_on.mean()) if len(nets_on) else float("nan")
    res["ci_on"] = block_ci_by_month(trades, mask, cs, months)
    res["breadth_on"] = breadth(gross_on)
    res["on_net_all"] = [float(t["net"]) for t in sel_on]
    res["on_net_month"] = [mk_month(cs[t["j"]]["ts"]) for t in sel_on]

    res["ab_off"] = alpha_beta(st_off, bh, months)
    res["ab_on"] = alpha_beta(st_on, bh, months)
    # beta 对齐只在 beta 有意义的区间做；beta≈0 时 1/beta 会变成不现实的高杠杆
    if res["ab_on"] and 0.3 <= res["ab_on"]["beta"] < 2.0:
        k = 1.0 / res["ab_on"]["beta"]
        res["on_beta_matched"] = perf(scale_months(st_on, months, k), months)
        res["beta_scale"] = k
    else:
        res["on_beta_matched"] = None
        res["beta_scale"] = None
    return res


def block_ci_by_month(trades, mask, cs, months, reps=4000, seed=23):
    """按月分块 bootstrap（单资产时等价于月度块重采样），给逐笔期望一个诚实的区间。"""
    sel = [(cs[t["j"]]["ts"], t["net"]) for t, k in zip(trades, mask) if k]
    if len(sel) < 20:
        return (float("nan"), float("nan"))
    by: dict[str, list] = {}
    for ts, v in sel:
        by.setdefault(mk_month(ts), []).append(v)
    keys = sorted(by)
    sums = np.array([sum(by[k]) for k in keys], float)
    cnts = np.array([len(by[k]) for k in keys], float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(keys), size=(reps, len(keys)))
    means = sums[idx].sum(axis=1) / cnts[idx].sum(axis=1)
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


def print_sample(r, filter_q, fee):
    print("─" * 96)
    print(f"【{r['name']}】{r['tf']}h · SuperTrend(10,{r['mult']}) · "
          f"{r['years']:.2f} 年 · {r['months']} 个月")
    print(f"  信号 {r['n_all']} 笔 → 过滤后 {r['n_on']} 笔 · "
          f"逐笔净期望 {r['exp_off']:+.3f}% → {r['exp_on']:+.3f}% · "
          f"过滤后广度 {r['breadth_on']} 笔")
    print(f"  在场时间占比：不过滤 {r['in_mkt_off']:.0f}% → 过滤后 {r['in_mkt_on']:.0f}%"
          f"（这是「减配」程度的直接度量）")
    print(f"  过滤后逐笔期望 95%CI（按月分块）[{r['ci_on'][0]:+.3f}, {r['ci_on'][1]:+.3f}] "
          f"→ {'下界>0' if r['ci_on'][0] > 0 else '跨过 0'}")
    print(f"\n  {'指标':<14}{'不过滤':>13}{'过滤后':>13}{'买入持有':>13}")
    labels = [("总收益%", "total"), ("年化%", "cagr"), ("年化波动%", "vol"),
              ("夏普", "sharpe"), ("索提诺", "sortino"), ("最大回撤%", "maxdd"),
              ("Calmar", "calmar")]
    for lab, key in labels:
        cells = []
        for v in ("off", "on", "bh"):
            d = r[v]
            cells.append(f"{d[key]:>13.2f}" if d else f"{'—':>13}")
        print(f"  {lab:<14}" + "".join(cells))

    print(f"\n  alpha-beta 分解（盯市月度收益 ~ alpha + beta × 买入持有月度收益）")
    for v, lab in (("ab_off", "不过滤"), ("ab_on", "过滤后")):
        ab = r[v]
        if not ab:
            print(f"    {lab}：样本不足")
            continue
        sig = "显著" if abs(ab["t_alpha"]) > 2 else "不显著"
        print(f"    {lab:<8} alpha {ab['alpha']:+.2f}%/月 (t={ab['t_alpha']:+.2f}, {sig}) · "
              f"beta {ab['beta']:+.2f} · R² {ab['r2']:.2f}")
    if r["on_beta_matched"]:
        bm = r["on_beta_matched"]
        print(f"    beta 对齐（×{r['beta_scale']:.2f}）后：年化 {bm['cagr']:+.1f}% · "
              f"波动 {bm['vol']:.1f}% · 回撤 {bm['maxdd']:.1f}%")

    # 判定（规则事先写死在代码里）
    on, bh, ab = r["on"], r["bh"], r["ab_on"]
    if not (on and bh and ab):
        return
    better_sharpe = on["sharpe"] > bh["sharpe"]
    alpha_sig = ab["t_alpha"] > 2
    indep = ab["r2"] < 0.10
    if alpha_sig:
        v = "**独立边际来源**（alpha 显著为正）"
    elif indep and better_sharpe:
        v = ("**与买入持有基本无关的独立收益流**（R²<0.10），"
             "风险调整后更优，但均值未被证实显著为正 → 只能算「未证实的独立流」")
    elif indep:
        v = ("与买入持有基本无关（R²<0.10），但夏普更低 → 独立但没有价值")
    elif better_sharpe:
        v = "低 beta 的持有替代品（风险覆盖层），alpha 不显著 → 不是独立边际"
    else:
        v = "**被买入持有支配**（夏普更低）→ 没有使用价值"
    print(f"\n  判定：{v}")
    print(f"    夏普 {on['sharpe']:.2f} vs 持有 {bh['sharpe']:.2f} · "
          f"回撤 {on['maxdd']:.1f}% vs {bh['maxdd']:.1f}% · "
          f"Calmar {on['calmar']:.2f} vs {bh['calmar']:.2f} · "
          f"在场 {r['in_mkt_on']:.0f}% vs 100%")
    print(f"    注：阈值 {filter_q:.2f} / 周期 {r['tf']}h / 倍数 {r['mult']} 均为**预先固定**；"
          f"费率 {fee}%/边；回撤为月度粒度，真实日内回撤会更大。")


def main(argv=None):
    ap = argparse.ArgumentParser(description="风险口径评估：风控覆盖层 vs 边际来源")
    ap.add_argument("--tf", type=int, default=4)
    ap.add_argument("--mult", type=float, default=4.0)
    ap.add_argument("--filter-q", type=float, default=0.70)
    ap.add_argument("--fee", type=float, default=0.05)
    ap.add_argument("--export", default="")
    a = ap.parse_args(argv)

    print("=" * 96)
    print("风险口径评估 —— 规则预先固定，不调参")
    print("=" * 96)
    print(f"规则：SuperTrend(10,{a.mult}) · 周期 {a.tf}h · 波动率过滤阈值 {a.filter_q:.2f} · "
          f"费率 {a.fee}%/边")
    print("样本：BTC 1h(2022-07起, 回测 JSON) / BTC 1h(2022-01起, 本地库) / ETH 1h(2024-01起, 本地库)")

    samples = [("BTC-USDT（回测 JSON）", "json"), ("BTC-USDT（本地库）", "db"),
               ("ETH-USDT（本地库）", "db")]
    out = []
    for name, kind in samples:
        if kind == "json":
            doc = json.loads(Path("backtest/btc_1h_full_fetched.json").read_text(encoding="utf-8"))
            c1h = doc["base"]
        else:
            sym = name.split("（")[0]
            c1h = load_db(sym, "1h")
        if len(c1h) < 500:
            print(f"\n[跳过] {name}：数据不足")
            continue
        r = evaluate_sample(name, c1h, a.tf, a.mult, a.filter_q, a.fee)
        if r:
            out.append(r)
            print_sample(r, a.filter_q, a.fee)

    if not out:
        print("\n[失败] 没有可用样本")
        return 1

    # 汇总
    print("\n" + "=" * 96)
    print("【汇总】三样本对照")
    print(f"{'样本':<24}{'夏普':>8}{'买入持有夏普':>13}{'回撤%':>9}{'持有回撤%':>11}"
          f"{'beta':>7}{'alpha%/月':>11}{'t(alpha)':>10}")
    for r in out:
        on, bh, ab = r["on"], r["bh"], r["ab_on"]
        if not (on and bh and ab):
            continue
        print(f"{r['name']:<24}{on['sharpe']:>8.2f}{bh['sharpe']:>13.2f}"
              f"{on['maxdd']:>9.1f}{bh['maxdd']:>11.1f}"
              f"{ab['beta']:>7.2f}{ab['alpha']:>11.2f}{ab['t_alpha']:>10.2f}")
    n_sharpe = sum(1 for r in out if r["on"] and r["bh"] and r["on"]["sharpe"] > r["bh"]["sharpe"])
    n_sig = sum(1 for r in out if r["ab_on"] and r["ab_on"]["t_alpha"] > 2)
    print(f"\n  夏普优于买入持有的样本：{n_sharpe}/{len(out)}")
    print(f"  alpha 显著为正（t>2）的样本：**{n_sig}/{len(out)}**")
    print("  判定规则（事先约定）：alpha 显著为正才算独立边际；")
    print("  否则即便夏普更高、回撤更小，它也只是「减配版的持有」= 风险覆盖层。")

    if a.export:
        slim = [{k: v for k, v in r.items() if k not in ("off", "on", "bh", "on_beta_matched")}
                | {kk: {k2: v2 for k2, v2 in r[kk].items() if k2 != "eq"}
                   for kk in ("off", "on", "bh") if r[kk]} for r in out]
        Path(a.export).write_text(json.dumps(slim, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
        print(f"\n[导出] {a.export}")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    sys.exit(main())
