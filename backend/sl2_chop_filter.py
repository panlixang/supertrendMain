# -*- coding: utf-8 -*-
"""
按用户三点意见重做：震荡过滤 + 保留样本量 + 逐年核算
==============================================================================

用户三点（逐条落实为可检验实验）
--------------------------------
① 「SuperTrend 在无序震荡里反复出错，把这段连续的去掉就行了」
   → 这正是你项目**生产环境既有**的思路：`regime.efficiency_ratio()`（Kaufman 效率比）
     + `er_hide_below=0.10`「ER 低于此值默认静默」+ `regime="range" → tradable False`。
     本脚本**直接复用该实现**，不写第二份。
   → 但分两步验：先看「震荡里的交易是否真的更差」（分档诊断），
     再看「事前跳过震荡」能否赚钱（因果规则 + **等量随机删单**对照）。
   → 同源机制另外补测：**连亏后停手**（交易盈亏的序列相关性）。

② 「不要把数据删得只剩一点」
   → 所有规则按**保留率曲线**报告（90/80/70/60/50%），逐行打印笔数与保留率，
     不再只给一个把样本砍到几十笔的阈值。
   → 频率对齐：1h×3.0 是 3.9 笔/周；**1–2 笔/周对应 1h×5.0（1.8 笔/周）**，故一并跑。

③ 「按年分开算，一次性长周期手续费会影响判断」
   → 逐年输出：笔数 / 毛期望 / **当年手续费合计** / 净期望 / 当年净值 / 胜率。

纪律（不放松）
--------------
- 每个规则都配**等量随机删单**对照：删掉同样多的笔数，但随机选。只删对才有意义。
- 逐根 K 线盯市净值；为让保留率曲线跑得动，改用「每笔按月份拆乘数因子」的 O(笔数) 评估
  （等价且快，见 `sl2_filter_recheck.trade_month_factors`）。
- 所有信号判定只用**开仓当时**可知的信息。
- 自检：skip 恒为 False 时必须与 `build_trades()` 逐笔一致，否则直接报错停下。

用法：
    cd backend
    python sl2_chop_filter.py
    python sl2_chop_filter.py --mults 3,5 --er-windows 20,60 --control-reps 200
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from sl2_tf_sweep import load_db, resample
from sl2_voltarget import build_trades
from sl2_riskeval import (trade_month_factors, to_pct, monthly_bh, perf,
                          mk_month, sign_test)
from regime import efficiency_ratio, ER_WINDOW
from indicators import super_trend


# ──────────────────────────────────────────────────────────────
def flips_of(cs, period, mult):
    st = super_trend([c["o"] for c in cs], [c["h"] for c in cs],
                     [c["l"] for c in cs], [c["c"] for c in cs],
                     periods=period, multiplier=mult, change_atr=True)
    return sorted(st["flips"], key=lambda f: f["i"])


def simulate(cs, flips, skip_fn, fee):
    """按翻向顺序推进；skip_fn(bar_i, 已完成交易, 自上一笔以来已跳过次数) → True 表示不进场。

    「跳过一次」= 这次翻向不建仓，保持空仓；下一次翻向再判断。
    因此**执行过的交易始终是基线交易的一个子集**（进场点永远是某个翻向点）。
    """
    cl = np.asarray([c["c"] for c in cs], float)
    trades, pos, skips = [], None, 0
    for f in flips:
        i = f["i"]
        if pos is not None:
            e, x = cl[pos["i"]], cl[i]
            gross = (x - e) / e * 100.0 * pos["side"]
            trades.append({"i": pos["i"], "j": i, "side": pos["side"],
                           "ts": cs[pos["i"]]["ts"], "gross": gross,
                           "bars": i - pos["i"], "net": gross - 2 * fee})
            pos, skips = None, 0
        if skip_fn is not None and skip_fn(i, trades, skips):
            skips += 1
            continue
        pos = {"i": i, "side": 1 if f["type"] == "buy" else -1}
    return trades


def er_series(cs, window):
    """复用 regime.efficiency_ratio。注意它要求 len >= window+1，所以传 window+1 根。"""
    out = np.full(len(cs), np.nan)
    for i in range(window, len(cs)):
        v = efficiency_ratio(cs[i - window:i + 1], window)
        if v is not None:
            out[i] = v
    return out


def breadth(g):
    if len(g) == 0 or g.sum() <= 0:
        return 0
    s = np.sort(g)[::-1]
    for k in range(1, len(s) + 1):
        if s[k:].sum() <= 0:
            return k
    return len(s)


def runs_test(signs):
    """Wald–Wolfowitz 游程检验：盈亏有无序列相关性。"""
    s = signs.astype(int)
    n1, n2 = int(s.sum()), int((1 - s).sum())
    n = len(s)
    if n1 == 0 or n2 == 0 or n < 10:
        return None
    runs = 1 + int((s[1:] != s[:-1]).sum())
    mu = 1 + 2 * n1 * n2 / n
    var = (2 * n1 * n2 * (2 * n1 * n2 - n)) / (n * n * (n - 1))
    return (runs - mu) / np.sqrt(var) if var > 0 else None


def _rank(x):
    """平均秩（处理并列），用于 Spearman。"""
    order = np.argsort(x, kind="mergesort")
    r = np.empty(len(x), float)
    r[order] = np.arange(len(x), dtype=float)
    sx = x[order]
    i = 0
    while i < len(sx):
        j = i
        while j + 1 < len(sx) and sx[j + 1] == sx[i]:
            j += 1
        if j > i:
            r[order[i:j + 1]] = (i + j) / 2.0
        i = j + 1
    return r


def spearman(a, b):
    """Spearman 秩相关。>0 表示 ER 越高越好；<0 表示 ER 越高越差。

    这是"震荡里是否真的反复出错"的可判定形式：
    如果低 ER（震荡）真的更差，那么 ER 分档均值与分档逐笔净期望应当**正相关**，
    且这个符号应当在换倍数、换资产后保持稳定。
    """
    a = np.asarray(a, float); b = np.asarray(b, float)
    if len(a) < 4 or len(a) != len(b):
        return None
    ra, rb = _rank(a), _rank(b)
    ra = ra - ra.mean(); rb = rb - rb.mean()
    d = np.sqrt((ra ** 2).sum() * (rb ** 2).sum())
    return float((ra * rb).sum() / d) if d > 0 else None


def yearly(cs, base, idx, fee):
    buckets: dict[str, list] = {}
    for k in idx:
        y = mk_month(cs[base[k]["j"]]["ts"])[:4]
        buckets.setdefault(y, []).append(base[k])
    out = []
    for y in sorted(buckets):
        ts = buckets[y]
        g = np.asarray([t["gross"] for t in ts], float)
        n = np.asarray([t["net"] for t in ts], float)
        out.append({"year": y, "n": len(ts), "gross": float(g.mean()),
                    "fee_pp": len(ts) * 2 * fee, "net": float(n.mean()),
                    "total": float((np.prod(1 + n / 100.0) - 1) * 100),
                    "win": float((n > 0).mean() * 100)})
    return out


# ──────────────────────────────────────────────────────────────
def run_sample(name, c1h, args, rng):
    cs = resample(c1h, args.tf)
    if len(cs) < 300:
        return [], []
    flips = flips_of(cs, args.period, args.mult)
    base = simulate(cs, flips, None, args.fee)
    ref, _ = build_trades(cs, mult=args.mult)   # 注意：build_trades 返回 (trades, st)
    if len(base) != len(ref) or any(abs(x["gross"] - y["gross"]) > 1e-9
                                    for x, y in zip(base, ref)):
        print(f"[{name}] **自检失败**：simulate 与 build_trades 不一致，停止本样本")
        return [], []

    years = len(cs) * args.tf / (24 * 365)
    key = {(t["i"], t["j"]): k for k, t in enumerate(base)}
    factors = trade_month_factors(cs, base, args.fee)
    months = [m for m in sorted(monthly_bh(cs)) if m >= mk_month(cs[flips[0]["i"]]["ts"])]
    nets = np.asarray([t["net"] for t in base], float)
    grosses = np.asarray([t["gross"] for t in base], float)
    monos: list[dict] = []

    def idx_of(skip_fn):
        return sorted(key[(t["i"], t["j"])] for t in simulate(cs, flips, skip_fn, args.fee))

    def evaluate(idx):
        if len(idx) < 10:
            return None
        p = perf(to_pct(factors, idx, months), months)
        if not p:
            return None
        n = nets[idx]
        p.update({"n": len(idx), "net": float(n.mean()),
                  "win": float((n > 0).mean() * 100),
                  "breadth": breadth(grosses[idx])})
        return p

    print("─" * 106)
    per_week = len(base) / years / 52
    print(f"【{name}】{args.tf}h × SuperTrend({args.period},{args.mult}) · {years:.2f} 年 · "
          f"翻向 {len(flips)} 次 · 基线成交 {len(base)} 笔 "
          f"（{len(base)/years:.0f} 笔/年 · **{per_week:.1f} 笔/周**）· 自检通过")
    m0 = evaluate(list(range(len(base))))
    print(f"  基线：逐笔净 {m0['net']:+.3f}% · 净值夏普 {m0['sharpe']:.2f} · "
          f"总收益 {m0['total']:+.1f}% · 回撤 {m0['maxdd']:.1f}% · 广度 {m0['breadth']}")

    # ── 诊断①：效率比分档 ──
    print(f"\n  诊断①：按开仓当时 ER 分档 —— 「无序震荡里的交易是否真的更差」")
    for w in args.er_windows:
        er = er_series(cs, w)
        vals = np.asarray([er[t["i"]] for t in base], float)
        okm = np.isfinite(vals)
        v = vals[okm]
        if len(v) < 60:
            continue
        sub = np.arange(len(base))[okm]
        edges = [-np.inf] + list(np.percentile(v, [20, 40, 60, 80])) + [np.inf]
        print(f"    ER{w}：{'档':<5}{'笔数':>7}{'ER均值':>9}{'逐笔净%':>10}{'胜率%':>8}")
        ctr, pnl = [], []
        for qi in range(5):
            mask = (v >= edges[qi]) & (v < edges[qi + 1])
            if mask.sum() < 10:
                continue
            sel = nets[sub[mask]]
            ctr.append(float(v[mask].mean())); pnl.append(float(sel.mean()))
            print(f"    {'':<7}Q{qi+1:<4}{int(mask.sum()):>7}{v[mask].mean():>9.3f}"
                  f"{sel.mean():>10.3f}{(sel > 0).mean()*100:>8.1f}")
        rho = spearman(ctr, pnl)
        if rho is not None:
            tag = ("ER 越高越好（与你的直觉一致）" if rho > 0.3 else
                   "ER 越高越差（与你的直觉相反）" if rho < -0.3 else "无方向")
            print(f"    {'':<7}→ Spearman(ER档均值, 逐笔净) rho = {rho:+.2f}  {tag}")
            monos.append({"sample": name, "mult": args.mult, "window": w, "rho": rho})

    # ── 诊断②：序列相关性 ──
    z = runs_test(nets > 0)
    print(f"\n  诊断②：盈亏的序列相关性 —— 连亏之后会不会更容易接着亏")
    print(f"    游程检验 z = {z:+.2f}" + ("（|z|<1.96 → 无序列相关）" if z is not None else ""))
    groups: dict[int, list] = {}
    prev = 0
    for t in base:
        groups.setdefault(min(prev, 3), []).append(t["net"])
        prev = prev + 1 if t["net"] <= 0 else 0
    print(f"    {'前面连亏':<10}{'笔数':>7}{'逐笔净%':>11}{'胜率%':>8}")
    for k in sorted(groups):
        v = np.asarray(groups[k], float)
        lab = f"{k} 笔" if k < 3 else "3 笔以上"
        print(f"    {lab:<10}{len(v):>7}{v.mean():>11.3f}{(v > 0).mean()*100:>8.1f}")

    # ── 规则对照（保留率曲线 + 连亏停手 + 随机删单对照）──
    print(f"\n  规则对照（要点②：按保留率列出，不把样本砍到没意义）")
    print(f"  {'规则':<30}{'笔数':>7}{'保留%':>7}{'笔/周':>7}{'逐笔净%':>10}"
          f"{'净值夏普':>10}{'总收益%':>10}{'回撤%':>9}{'随机删单夏普':>13}{'p':>8}")
    variants = []
    for w in args.er_windows:
        er = er_series(cs, w)
        vals = np.asarray([er[t["i"]] if np.isfinite(er[t["i"]]) else 1.0 for t in base], float)
        for keep in args.keeps:
            thr = float(np.percentile(vals, (1 - keep) * 100))
            variants.append((f"ER{w} < {thr:.3f}（目标保留{keep*100:.0f}%）",
                             lambda i, tr, sk, thr=thr, er=er:
                             bool(np.isfinite(er[i])) and er[i] < thr))
        # 生产口径锚点：regime.py 里"震荡"的官方定义就是这几条线
        for a in args.er_abs:
            variants.append((f"ER{w} < {a:.2f}（生产口径）",
                             lambda i, tr, sk, a=a, er=er:
                             bool(np.isfinite(er[i])) and er[i] < a))
    for k, m in args.loss_rules:
        variants.append((f"连亏{k}笔后停手{m}次",
                         lambda i, tr, sk, k=k, m=m:
                         len(tr) >= k and all(t["net"] <= 0 for t in tr[-k:]) and sk < m))

    best = None
    rows: list[dict] = []
    for lab, fn in variants:
        idx = idx_of(fn)
        m = evaluate(idx)
        if not m:
            continue
        null = np.empty(args.control_reps)
        for r_ in range(args.control_reps):
            pick = np.sort(rng.choice(len(base), m["n"], replace=False))
            mm = evaluate(pick.tolist())
            null[r_] = mm["sharpe"] if mm else np.nan
        null = null[np.isfinite(null)]
        pv = float((null >= m["sharpe"]).mean()) if len(null) else float("nan")
        p95 = float(np.percentile(null, 95)) if len(null) else float("nan")
        print(f"  {lab:<30}{m['n']:>7}{m['n']/len(base)*100:>7.0f}"
              f"{m['n']/years/52:>7.1f}{m['net']:>10.3f}{m['sharpe']:>10.2f}"
              f"{m['total']:>10.1f}{m['maxdd']:>9.1f}"
              f"{(null.mean() if len(null) else float('nan')):>13.2f}{pv:>8.3f}")
        rows.append({"sample": name, "mult": args.mult, "rule": lab,
                     "n": m["n"], "keep": m["n"] / len(base),
                     "per_week": m["n"] / years / 52,
                     "net": m["net"], "sharpe": m["sharpe"],
                     "total": m["total"], "maxdd": m["maxdd"],
                     "breadth": m["breadth"], "base_sharpe": m0["sharpe"],
                     "rmean": float(null.mean()) if len(null) else float("nan"),
                     "p": pv, "above_p95": bool(m["sharpe"] > p95)})
        if best is None or m["sharpe"] > best[2]["sharpe"]:
            best = (lab, idx, m)

    # ── 逐年拆解（要点③）──
    print(f"\n  逐年拆解（要点③：分开算，看清费用集中在哪一年）")
    todo = [("基线（不过滤）", list(range(len(base))))]
    if best:
        todo.append((f"最优规则：{best[0]}", best[1]))
    for lab, idx in todo:
        print(f"    {lab}")
        print(f"      {'年份':<8}{'笔数':>7}{'毛期望%':>10}{'当年手续费pp':>14}"
              f"{'净期望%':>10}{'当年净值%':>11}{'胜率%':>8}")
        rows_y = yearly(cs, base, idx, args.fee)
        for r in rows_y:
            gross_yr = r["gross"] * r["n"]
            print(f"      {r['year']:<8}{r['n']:>7}{r['gross']:>10.3f}{r['fee_pp']:>14.1f}"
                  f"{r['net']:>10.3f}{r['total']:>11.1f}{r['win']:>8.1f}"
                  f"   ← 当年毛合计 {gross_yr:+.1f}pp，费用占毛 "
                  f"{(r['fee_pp'] / abs(gross_yr) * 100 if abs(gross_yr) > 1e-9 else float('inf')):.0f}%")
    return rows, monos


def main(argv=None):
    ap = argparse.ArgumentParser(description="震荡过滤：保留率曲线 + 逐年核算")
    ap.add_argument("--tf", type=int, default=1)
    ap.add_argument("--period", type=int, default=10)
    ap.add_argument("--mults", default="5,3", help="倍数，逗号分隔；1h 上 5.0≈1.8笔/周")
    ap.add_argument("--fee", type=float, default=0.05)
    ap.add_argument("--er-windows", default="20,60")
    ap.add_argument("--keeps", default="0.90,0.80,0.70,0.60,0.50")
    ap.add_argument("--er-abs", default="0.10,0.15,0.30",
                    help="生产口径绝对阈值（regime.er_hide_below / er_min / er_trend）")
    ap.add_argument("--loss-rules", default="2:1,3:1,2:3", help="连亏k笔后停手m次，k:m")
    ap.add_argument("--control-reps", type=int, default=200)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)
    a.er_windows = [int(x) for x in a.er_windows.split(",") if x.strip()]
    a.keeps = [float(x) for x in a.keeps.split(",") if x.strip()]
    a.er_abs = [float(x) for x in a.er_abs.split(",") if x.strip()]
    a.loss_rules = [tuple(int(v) for v in x.split(":")) for x in a.loss_rules.split(",") if x.strip()]
    rng = np.random.default_rng(a.seed)
    all_rows: list[dict] = []
    all_monos: list[dict] = []

    print("=" * 106)
    print("震荡过滤重做：保留率曲线 + 逐年核算（复用 regime.efficiency_ratio，与生产同口径）")
    print("=" * 106)
    print(f"周期 {a.tf}h · SuperTrend({a.period}, m) · 费率 {a.fee}%/边 · "
          f"ER 窗口 {a.er_windows}（生产默认 {ER_WINDOW}）· 随机对照 {a.control_reps} 次")

    samples = [("BTC(回测JSON)", json.loads(
        Path("backtest/btc_1h_full_fetched.json").read_text(encoding="utf-8"))["base"])]
    for s in ("BTC-USDT", "ETH-USDT"):
        c = load_db(s, "1h")
        if len(c) > 500:
            samples.append((s + "(本地库)", c))

    for mult in [float(x) for x in a.mults.split(",") if x.strip()]:
        a.mult = mult
        print(f"\n{'#'*106}\n### 倍数 {mult}\n{'#'*106}")
        for name, c1h in samples:
            r_, m_ = run_sample(name, c1h, a, rng)
            all_rows += r_
            all_monos += m_

    # ── 判定「震荡是否真的更差」：ER 方向必须跨倍数、跨资产稳定才算机制 ──
    print("\n" + "=" * 106)
    print("判定「震荡里反复出错」：看 ER 方向能不能跨倍数/跨资产站稳")
    print("=" * 106)
    if all_monos:
        print(f"  {'样本':<18}{'倍数':>6}{'ER窗口':>8}{'rho':>8}   方向")
        for r in sorted(all_monos, key=lambda x: (x["sample"], x["window"], x["mult"])):
            tag = ("ER 越高越好" if r["rho"] > 0.3 else
                   "ER 越高越差" if r["rho"] < -0.3 else "无方向")
            print(f"  {r['sample']:<18}{r['mult']:>6}{r['window']:>8}{r['rho']:>+8.2f}   {tag}")
        for w in sorted({r["window"] for r in all_monos}):
            sub = [r for r in all_monos if r["window"] == w]
            pos = sum(1 for r in sub if r["rho"] > 0.3)
            neg = sum(1 for r in sub if r["rho"] < -0.3)
            none = len(sub) - pos - neg
            print(f"\n  ER{w} 共 {len(sub)} 格：ER越高越好 {pos} · ER越高越差 {neg} · 无方向 {none}")
            if pos and neg:
                print(f"    → **同一条规则在不同倍数上方向相反**，符号一致率 "
                      f"{max(pos, neg)}/{pos + neg}"
                      f"（若为真机制应为 {pos + neg}/{pos + neg}）")
    if all_rows:
        print("\n" + "=" * 106)
        print("跨样本汇总（要点：单格显著性 vs 整体一致性，两件事）")
        print("=" * 106)
        for mult in sorted({r["mult"] for r in all_rows}):
            sub = [r for r in all_rows if r["mult"] == mult]
            beat = [r for r in sub if r["sharpe"] > r["rmean"]]
            p95 = [r for r in sub if r["above_p95"]]
            sp = sign_test(len(beat), len(sub) - len(beat))
            print(f"  倍数 {mult}：{len(sub)} 格 · 实际夏普 > 随机删单均值 {len(beat)}/{len(sub)}"
                  f"（符号检验 p={sp:.4f}）· 超过随机 95 分位 {len(p95)}/{len(sub)}"
                  f" · 实际夏普 > 基线 {sum(1 for r in sub if r['sharpe'] > r['base_sharpe'])}/{len(sub)}")
        print(f"\n  全部 {len(all_rows)} 格："
              f"最小 p = {min(r['p'] for r in all_rows):.3f}"
              f" · Bonferroni 阈值 = {0.05/len(all_rows):.2e}")
        print("  → 保留率 >=60% 的格子（你要求的样本量下限）表现：")
        for r in sorted([r for r in all_rows if r["keep"] >= 0.60],
                        key=lambda r: -r["sharpe"])[:8]:
            print(f"      {r['sample']:<16} m={r['mult']}  {r['rule']:<28}"
                  f"保留{r['keep']*100:>3.0f}% ({r['per_week']:.1f}笔/周)"
                  f" 夏普 {r['sharpe']:+.2f}（基 {r['base_sharpe']:+.2f}）"
                  f" 随机 {r['rmean']:+.2f} p={r['p']:.3f}")
        out = Path("ml_runs/_diag/chop_filter.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(all_rows, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n  明细已存 {out}")

    print("\n" + "=" * 106)
    print("读法：① 诊断①各档若无单调性，「震荡=更差」在这套信号上不成立；")
    print("      ② 规则若打不过「等量随机删单」（p 不显著），说明只是删得少、不是删得对；")
    print("      ③ 逐年看费用与净值；若某年费用吃掉全部毛收益，那年的结论不能外推。")
    print("=" * 106)
    return 0


if __name__ == "__main__":
    sys.exit(main())
