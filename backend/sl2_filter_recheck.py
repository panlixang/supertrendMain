# -*- coding: utf-8 -*-
"""
波动率过滤：配对复核 + 「等量随机删单」对照
==============================================================================

要回答的两个问题（第二个才是关键的）
-----------------------------------
Q1 逐笔期望的改善，能不能翻译成净值（夏普/Calmar）的改善？
   —— 用配对方式量：每个格子同时算逐笔口径与盯市净值口径。

Q2 **改善到底来自「挑掉了哪些交易」，还是仅仅来自「交易变少了」？**
   —— 这是本脚本的核心。
   过滤砍掉约 60% 的交易，而 1h 上毛边际≈0、净边际≈-手续费，
   那么「少交易」本身就天然抬高夏普（少交 60% 的费用）。
   如果不控制这一点，就会把「少交手续费」的功劳记到「波动率择时」头上。

   做法：对每个格子，随机保留**与过滤完全相同数量的交易**，重复 200 次，
   得到夏普的零分布，再看真实过滤的夏普落在什么位置。
   · 落在零分布中部  → 过滤没有任何择时信息，只是「交易变少」。
   · 落在零分布尾部  → 过滤确实挑对了交易（这才是真发现）。

技术处理
--------
每笔交易按月份拆成乘数因子，于是任意交易子集都能 O(笔数) 算出月度净值，
不必对每根 K 线重算 —— 这样 200 次随机对照才跑得动。

用法：
    cd backend
    python sl2_filter_recheck.py
    python sl2_filter_recheck.py --control-reps 300 --filter-q 0.70
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from sl2_tf_sweep import load_db, resample
from sl2_voltarget import build_trades, entry_context, atr_pct_at
from sl2_volfilter import vol_filter_mask
from sl2_riskeval import (monthly_bh, perf, mk_month, trade_month_factors,
                          to_pct, sign_test)
from sl2_riskeval import monthly_bh, perf, mk_month


def samples():
    out = []
    doc = json.loads(Path("backtest/btc_1h_full_fetched.json").read_text(encoding="utf-8"))
    out.append(("BTC(4.17y)", doc["base"]))
    for s in ("BTC-USDT", "ETH-USDT"):
        c = load_db(s, "1h")
        if len(c) > 500:
            out.append((s + "(本地库)", c))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="过滤复核：配对口径 + 等量随机删单对照")
    ap.add_argument("--tfs", default="1,4,12")
    ap.add_argument("--mults", default="2,3,4,5")
    ap.add_argument("--filter-q", type=float, default=0.70)
    ap.add_argument("--fee", type=float, default=0.05)
    ap.add_argument("--control-reps", type=int, default=200,
                    help="等量随机删单对照次数")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)

    tfs = [int(x) for x in a.tfs.split(",") if x.strip()]
    mults = [float(x) for x in a.mults.split(",") if x.strip()]
    rng = np.random.default_rng(a.seed)

    print("=" * 112)
    print("波动率过滤复核：配对口径 + 等量随机删单对照")
    print("=" * 112)
    print(f"阈值 {a.filter_q:.2f} · 费率 {a.fee}%/边 · 随机对照 {a.control_reps} 次/格\n")
    print(f"{'样本':<15}{'周期':>5}{'倍数':>6}{'笔数':>6}{'过滤后':>7}{'在场%':>6}"
          f"{'逐笔净%':>9}{'→过滤':>9}{'夏普':>7}{'→过滤':>7}"
          f"{'随机删单夏普均值':>16}{'对照分位':>10}{'p':>8}")

    rows = []
    for name, c1h in samples():
        for tf in tfs:
            cs = resample(c1h, tf)
            if len(cs) < 300:
                continue
            ctx = entry_context(cs)
            for m in mults:
                trades, _ = build_trades(cs, mult=m)
                if len(trades) < 30:
                    continue
                apct = np.asarray([atr_pct_at(ctx, t["i"]) or np.nan for t in trades], float)
                min_hist = max(20, min(50, len(trades) // 3))
                mask, _ = vol_filter_mask(apct, a.filter_q, min_history=min_hist)
                for t in trades:
                    t["net"] = t["gross"] - 2 * a.fee
                idx_on = np.arange(len(trades))[mask]
                if len(idx_on) < 15:
                    continue

                fac = trade_month_factors(cs, trades, a.fee)
                bh = monthly_bh(cs)
                start = mk_month(cs[trades[0]["i"]]["ts"])
                months = [x for x in sorted(bh) if x >= start]
                if len(months) < 12:
                    continue

                all_idx = np.arange(len(trades))
                p_off = perf(to_pct(fac, all_idx, months), months)
                st_on = to_pct(fac, idx_on, months)
                p_on = perf(st_on, months)
                if not (p_off and p_on):
                    continue

                # 等量随机删单：保留同样多的交易，但随机选
                n_keep = len(idx_on)
                null = np.empty(a.control_reps)
                for r_ in range(a.control_reps):
                    pick = rng.choice(len(trades), n_keep, replace=False)
                    pr = perf(to_pct(fac, pick, months), months)
                    null[r_] = pr["sharpe"] if pr else np.nan
                null = null[np.isfinite(null)]
                if len(null) < 20:
                    continue
                pctl = float((null < p_on["sharpe"]).mean() * 100)
                pval = float((null >= p_on["sharpe"]).mean())

                exp_off = float(np.mean([t["net"] for t in trades]))
                exp_on = float(np.mean([trades[k]["net"] for k in idx_on]))
                rows.append({"sample": name, "tf": tf, "mult": m, "n": len(trades),
                             "n_on": n_keep, "exp_off": exp_off, "exp_on": exp_on,
                             "sh_off": p_off["sharpe"], "sh_on": p_on["sharpe"],
                             "cal_off": p_off["calmar"], "cal_on": p_on["calmar"],
                             "null_mean": float(null.mean()), "pctl": pctl, "p": pval})
                print(f"{name:<15}{tf:>4}h{m:>6.1f}{len(trades):>6}{n_keep:>7}"
                      f"{n_keep/len(trades)*100:>6.0f}{exp_off:>9.3f}{exp_on:>9.3f}"
                      f"{p_off['sharpe']:>7.2f}{p_on['sharpe']:>7.2f}"
                      f"{null.mean():>16.2f}{pctl:>9.0f}%{pval:>8.3f}")

    if not rows:
        print("\n[失败] 没有有效格子")
        return 1

    print("\n" + "=" * 112)
    dn = [r["exp_on"] - r["exp_off"] for r in rows]
    ds = [r["sh_on"] - r["sh_off"] for r in rows]
    dc = [r["cal_on"] - r["cal_off"] for r in rows]
    print(f"【口径配对】{len(rows)} 个格子")
    print(f"  逐笔净期望  变好 {sum(1 for x in dn if x>0)} / 变差 {sum(1 for x in dn if x<0)}"
          f"  → p = {sign_test(sum(1 for x in dn if x>0), sum(1 for x in dn if x<0)):.4f}")
    print(f"  净值夏普    变好 {sum(1 for x in ds if x>0)} / 变差 {sum(1 for x in ds if x<0)}"
          f"  → p = {sign_test(sum(1 for x in ds if x>0), sum(1 for x in ds if x<0)):.4f}")
    print(f"  净值 Calmar 变好 {sum(1 for x in dc if x>0)} / 变差 {sum(1 for x in dc if x<0)}"
          f"  → p = {sign_test(sum(1 for x in dc if x>0), sum(1 for x in dc if x<0)):.4f}")

    # 过滤 vs 等量随机删单
    beats = sum(1 for r in rows if r["p"] < 0.05)
    above_med = sum(1 for r in rows if r["pctl"] >= 50)
    print(f"\n【关键对照】过滤 vs **等量随机删单**（同样少交易，但随机选）")
    print(f"  过滤夏普落在随机对照 95 分位以上的格子：{sum(1 for r in rows if r['pctl']>=95)}/{len(rows)}")
    print(f"  过滤夏普高于随机对照中位数的格子：      {above_med}/{len(rows)}")
    print(f"  单格 p<0.05 的格子：                    {beats}/{len(rows)}"
          f"（多重比较下 Bonferroni 阈值 0.05/{len(rows)} = {0.05/len(rows):.4f}）")
    print(f"  随机对照夏普均值（全体）：{np.mean([r['null_mean'] for r in rows]):.2f} · "
          f"实际过滤后夏普均值：{np.mean([r['sh_on'] for r in rows]):.2f}")
    if beats == 0:
        print("  → **没有任何一格能证明过滤优于等量随机删单。**")
        print("     结论：过滤的收益来自「交易变少」（省手续费 + 降低暴露），不是来自择时。")
    elif beats <= 2:
        print(f"  → 只有 {beats} 格达标，在 {len(rows)} 格里属于噪声水平，不足以支撑择时结论。")
    else:
        print(f"  → 有 {beats} 格达标，值得在更多资产上验证。")

    avg_in = float(np.mean([r["n_on"] / r["n"] for r in rows]))
    print(f"\n  平均保留比例 {avg_in*100:.0f}% → 过滤把约 {(1-avg_in)*100:.0f}% 的时间换成空仓/空手。")
    print("=" * 112)
    return 0


if __name__ == "__main__":
    sys.exit(main())
