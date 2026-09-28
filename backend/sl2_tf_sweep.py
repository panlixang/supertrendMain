# -*- coding: utf-8 -*-
"""
持有周期轴扫描：BTC 趋势策略在 1h / 4h / 12h / 1d 上的「边际 vs 成本」
================================================================================

为什么扫这个轴
--------------
前面的实验已经证明：BTC 1h 上方向不可学（AUC≈0.5）、换信号源无效、波动率模型不能挣钱。
但那些实验全都固定在一个隐含前提上：**1h 周期、平均持仓 ~44 根**。

这里有个被忽略的算术：

    SuperTrend(10,3.0) 在 1h 上 4.17 年翻向 835 次，
    往返手续费 0.10% × 835 ≈ **83 个百分点的累计费用**。
    而毛期望 ≈ 0。

也就是说，这套信号在 1h 上的"边际"和"成本"是同一量级，费用把一切都吃掉了。
**要翻身，需要的不是更强的模型，而是「毛边际 / 单笔成本」这个比值变大。**
比值随持有周期拉长而变大（趋势的幅度随 √t 甚至更快增长，而手续费是固定的）。
这正是本研究唯一还没碰过的、有真实头寸余量的轴。

关键校验：随机入场对照组
------------------------
任何一个 (周期, 倍数) 组合都会有某个毛均值数字，光看它毫无意义。必须回答：

    「SuperTrend 的择时，比**同一批笔数、同一持仓长度分布、同一多空比例**的随机入场更好吗？」

所以每个格子都跑一次随机入场零假设（500 次）：从历史里随机挑起点、用**该格子真实的持仓根数**和
真实的多空比例生成交易，得到毛均值的零分布，再算 p 值。
这个零分布自动包含了 BTC 本身上涨的漂移（因为多空比例被匹配了）。

⚠️ 多重比较：本表有 N 个格子。挑最好那个必然乐观，所以用 Bonferroni 阈值 0.05/N。

⚠️ 随机入场允许重叠持仓（真实交易是一单一仓不重叠），重叠会放大均值估计的方差
  → 零分布更宽 → p 值偏大 → **这是保守方向，可以接受**。

用法：
    cd backend
    python sl2_tf_sweep.py
    python sl2_tf_sweep.py --tfs 1,4,12,24 --mults 2,3,4,5 --reps 800
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np

from sl2_voltarget import build_trades, entry_context, atr_pct_at
from sl2_volfilter import vol_filter_mask


DB_PATH = Path("candle_data.db")


def load_db(symbol: str, tf: str = "1h"):
    """从本地 candle_data.db 读 K 线 —— 用于拿 BTC 之外资产的独立样本。"""
    con = sqlite3.connect(str(DB_PATH))
    try:
        rows = con.execute(
            "select ts,o,h,l,c,vol from candles where symbol=? and tf=? order by ts",
            (symbol, tf)).fetchall()
    finally:
        con.close()
    return [{"ts": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "vol": r[5]}
            for r in rows]


def save_curve(path, curves, title=""):
    Path(path).write_text(json.dumps({"title": title, "curves": curves},
                                     ensure_ascii=False), encoding="utf-8")


# ──────────────────────────────────────────────────────────────
def resample(candles, hours: int):
    """把 1h K 线聚合成 hours 小时 K 线（按 UTC 纪元边界对齐，丢掉不完整的首尾）。"""
    if hours <= 1:
        return candles
    step = hours * 3600_000
    groups, order = {}, []
    for c in candles:
        k = c["ts"] // step
        g = groups.get(k)
        if g is None:
            groups[k] = [c]; order.append(k)
        else:
            g.append(c)
    out = []
    for k in order:
        g = groups[k]
        if len(g) < hours:                 # 数据缺口导致的不完整桶，丢掉
            continue
        out.append({"ts": k * step, "o": g[0]["o"],
                    "h": max(x["h"] for x in g), "l": min(x["l"] for x in g),
                    "c": g[-1]["c"], "vol": sum(x.get("vol") or 0 for x in g), "_n": len(g)})
    while out and out[0]["_n"] < hours:
        out.pop(0)
    return out


TF_HOURS = {"1h": 1, "2h": 2, "4h": 4, "8h": 8, "12h": 12, "1d": 24}


def load_tf(symbol: str, tf: str = "1h"):
    """按周期取 K 线 —— **唯一的取数实现**，所有脚本都从这里走。

    为什么统一从 1h 重采样，而不是直接读库里的 4h：
        candle_data.db 里 BTC 4h 只从 2022-09-01 起（8905 根），
        而 1h 从 2022-01-01 起（41451 根）→ 重采样成 4h 能多出 8 个月历史，
        而且 BTC 与 ETH 用的是**同一窗口、同一 UTC 对齐**，跨资产可比。
    库里没有 1h 时才退回直接读该周期。
    """
    h = TF_HOURS.get(tf)
    if h is None:
        raise ValueError(f"不支持的周期 {tf}，可选 {sorted(TF_HOURS)}")
    c1h = load_db(symbol, "1h")
    if not c1h:
        return load_db(symbol, tf)
    return resample(c1h, h)


def breadth(g: np.ndarray) -> int:
    """去掉前 k 笔盈利后总和转负的最小 k —— 越小越依赖少数大单。"""
    if len(g) == 0 or g.sum() <= 0:
        return 0
    s = np.sort(g)[::-1]
    for k in range(1, len(s) + 1):
        if s[k:].sum() <= 0:
            return k
    return len(s)


def random_null(cl: np.ndarray, bars: np.ndarray, sides: np.ndarray,
                reps: int, rng) -> np.ndarray | None:
    """随机入场零分布：笔数、持仓根数分布、多空比例全部匹配真实交易，只随机化「何时进场」。"""
    L = len(cl)
    n = len(bars)
    if n < 10:
        return None
    maxb = int(bars.max())
    hi = L - maxb - 1
    if hi < 10:
        return None
    starts = rng.integers(1, hi, size=(reps, n))
    e = cl[starts]
    x = cl[starts + bars[None, :]]
    return ((x - e) / e * 100.0 * sides[None, :]).mean(axis=1)


def simulate(trades, fee):
    net = np.asarray([t["gross"] for t in trades], float) - 2 * fee
    eq = np.cumprod(1 + net / 100.0)
    peak = np.maximum.accumulate(eq)
    return eq, float(np.min(eq / peak - 1)) * 100


def boot_ci(x: np.ndarray, reps: int = 4000, seed: int = 11) -> tuple[float, float]:
    """均值的 bootstrap 95% 置信区间 —— 笔数少的时候这一列比均值本身更重要。"""
    if len(x) < 5:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(x), size=(reps, len(x)))
    ms = x[idx].mean(axis=1)
    return (float(np.percentile(ms, 2.5)), float(np.percentile(ms, 97.5)))


def buy_hold(candles):
    """买入持有基准 —— 任何 BTC 策略都必须先打赢这个才有意义。"""
    cl = np.asarray([c["c"] for c in candles], float)
    eq = cl / cl[0]
    peak = np.maximum.accumulate(eq)
    dd = float(np.min(eq / peak - 1)) * 100
    years = len(cl) / (24 * 365)
    total = float((eq[-1] - 1) * 100)
    cagr = ((eq[-1]) ** (1 / years) - 1) * 100
    return {"total": total, "cagr": cagr, "dd": dd, "years": years}


# ──────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(description="BTC 持有周期 × 倍数 扫描（含随机入场对照）")
    ap.add_argument("--tfs", default="1,4,12,24", help="周期（小时，逗号分隔）")
    ap.add_argument("--mults", default="2,3,4,5", help="SuperTrend 倍数")
    ap.add_argument("--fee", type=float, default=0.05, help="单边费率 %（taker）")
    ap.add_argument("--maker-fee", type=float, default=0.02, help="单边费率 %（maker，挂单）")
    ap.add_argument("--reps", type=int, default=500)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--filter-q", type=float, default=0.70, help="ATR 过滤阈值分位")
    ap.add_argument("--symbol", default="", help="从 candle_data.db 读该资产（如 ETH-USDT），留空则用 BTC 1h JSON")
    a = ap.parse_args(argv)

    tfs = [int(x) for x in str(a.tfs).split(",") if x.strip()]
    mults = [float(x) for x in str(a.mults).split(",") if x.strip()]

    if a.symbol:
        c1h = load_db(a.symbol, "1h")
        label = f"{a.symbol} (candle_data.db)"
    else:
        doc = json.loads(Path("backtest/btc_1h_full_fetched.json").read_text(encoding="utf-8"))
        c1h = doc["base"]
        label = "BTC-USDT (backtest/btc_1h_full_fetched.json)"
    bh = buy_hold(c1h)
    print("=" * 108)
    print("持有周期轴扫描：边际 vs 成本（含随机入场对照 + 买入持有基准）")
    print("=" * 108)
    print(f"标的：{label}")
    print(f"原始 1h K 线 {len(c1h)} 根 ≈ {bh['years']:.2f} 年 · "
          f"taker {a.fee}%/边 · maker {a.maker_fee}%/边 · 随机入场对照 {a.reps} 次")
    print(f"\n【基准】买入持有：总收益 {bh['total']:+.1f}% · 年化 {bh['cagr']:.1f}% · "
          f"最大回撤 {bh['dd']:.1f}%")
    print(f"  ⚠️ 任何策略的最终考核标准是这一行。总净值低于 {bh['total']:+.1f}%，就不如躺着不动。\n")

    rng = np.random.default_rng(a.seed)
    cells, rows = [], []
    for tf in tfs:
        cs = resample(c1h, tf)
        if len(cs) < 300:
            print(f"[跳过] {tf}h 只有 {len(cs)} 根，样本太少")
            continue
        cl = np.asarray([c["c"] for c in cs], float)
        ctx = entry_context(cs)
        for m in mults:
            trades, _ = build_trades(cs, mult=m)
            if len(trades) < 20:
                continue
            g = np.asarray([t["gross"] for t in trades], float)
            bars = np.asarray([t["bars"] for t in trades], int)
            sides = np.asarray([t["side"] for t in trades], float)
            eq, dd = simulate(trades, a.fee)
            null = random_null(cl, bars, sides, a.reps, rng)
            p = float((null >= g.mean()).mean()) if null is not None else float("nan")
            ci_lo, ci_hi = boot_ci(g)
            cell = {
                "tf": tf, "mult": m, "n": len(trades), "bars": len(cs),
                "per_year": len(trades) / (len(cs) * tf / (24 * 365)),
                "hold": float(bars.mean()),
                "gross": float(g.mean()), "median": float(np.median(g)),
                "fee_pp": len(trades) * 2 * a.fee,
                "net": float(g.mean() - 2 * a.fee),
                "net_maker": float(g.mean() - 2 * a.maker_fee),
                "total": float((eq[-1] - 1) * 100), "dd": dd,
                "breadth": breadth(g),
                "null_mean": float(null.mean()) if null is not None else float("nan"),
                "p": p, "ci_lo": ci_lo, "ci_hi": ci_hi,
                "ci_net_lo": ci_lo - 2 * a.fee, "ci_net_hi": ci_hi - 2 * a.fee,
                "vs_bh": float((eq[-1] - 1) * 100) - bh["total"],
                "trades": trades, "apct": np.asarray(
                    [atr_pct_at(ctx, t["i"]) or np.nan for t in trades], float),
            }
            cell["alpha"] = cell["gross"] - cell["null_mean"]
            cells.append(cell)
            rows.append(cell)

    if not cells:
        print("[失败] 没有任何有效格子")
        return 1

    bonf = 0.05 / len(cells)
    print("【表 1】成本结构：毛边际能不能盖住手续费，以及有没有打赢买入持有")
    print(f"{'周期':>5}{'倍数':>6}{'笔数':>7}{'笔/年':>8}{'均持仓':>8}{'毛均值%':>10}"
          f"{'费用合计pp':>12}{'净均值%':>10}{'总净值%':>11}{'买入持有%':>11}"
          f"{'策略-持有':>11}{'回撤%':>9}")
    print(f"{'基准':>5}{'—':>6}{'0':>7}{'0':>8}{'—':>8}{'—':>10}{'—':>12}{'—':>10}"
          f"{bh['total']:>11.1f}{bh['total']:>11.1f}{0.0:>11.1f}{bh['dd']:>9.1f}")
    for c in rows:
        print(f"{c['tf']:>4}h{c['mult']:>6.1f}{c['n']:>7}{c['per_year']:>8.1f}{c['hold']:>8.1f}"
              f"{c['gross']:>10.3f}{c['fee_pp']:>12.1f}"
              f"{c['net']:>10.3f}{c['total']:>11.1f}{bh['total']:>11.1f}"
              f"{c['vs_bh']:>11.1f}{c['dd']:>9.1f}")

    print(f"\n【表 2】择时显著性（随机入场对照）+ 置信区间 + 有效广度")
    print(f"  择时alpha = 实际毛均值 - 随机入场均值（扣掉市场漂移后，剩下多少是自己的功劳）")
    print(f"  Bonferroni 阈值 = 0.05/{len(cells)} = {bonf:.4f}（低于它才算真有择时能力）")
    print(f"{'周期':>5}{'倍数':>6}{'笔数':>6}{'随机入场%':>11}{'实际毛%':>10}{'择时alpha%':>12}"
          f"{'净均值%':>10}{'净均值95%CI':>20}{'p值':>8}{'广度':>6}{'过滤净%':>10}")
    for c in cells:
        trades, apct = c["trades"], c["apct"]
        n = len(trades)
        # 过滤规则统一走 sl2_volfilter.vol_filter_mask —— 保证与组合评估脚本同口径
        min_hist = max(20, min(50, n // 3))
        mask, _ = vol_filter_mask(apct, a.filter_q, min_history=min_hist)
        sel = np.arange(n)[mask]
        if len(sel) >= 20:
            gf = np.asarray([trades[k]["gross"] for k in sel], float)
            fnet = float(gf.mean() - 2 * a.fee)
        else:
            fnet = float("nan")
        flag = "  ← 显著" if (np.isfinite(c["p"]) and c["p"] < bonf) else ""
        ci = f"[{c['ci_net_lo']:+.2f},{c['ci_net_hi']:+.2f}]"
        print(f"{c['tf']:>4}h{c['mult']:>6.1f}{c['n']:>6}{c['null_mean']:>11.3f}"
              f"{c['gross']:>10.3f}{c['alpha']:>12.3f}{c['net']:>10.3f}{ci:>20}"
              f"{c['p']:>8.3f}{c['breadth']:>6}{fnet:>10.3f}{flag}")

    # ── 结论汇总 ──
    print("\n" + "=" * 108)
    sig = [c for c in cells if np.isfinite(c["p"]) and c["p"] < bonf]
    pos_net = [c for c in cells if c["net"] > 0]
    pos_net_maker = [c for c in cells if c["net_maker"] > 0]
    beat_bh = [c for c in cells if c["vs_bh"] > 0]
    print(f"净均值为正的格子：taker {len(pos_net)}/{len(cells)} · maker {len(pos_net_maker)}/{len(cells)}")
    print(f"总净值打赢买入持有的格子：**{len(beat_bh)}/{len(cells)}**"
          f"（买入持有 {bh['total']:+.1f}% / 年化 {bh['cagr']:.1f}% / 回撤 {bh['dd']:.1f}%）")
    ci_pos = [c for c in cells if c["ci_net_lo"] > 0]
    print(f"净均值 95% 置信区间下界 > 0 的格子：**{len(ci_pos)}/{len(cells)}**"
          f"（这才是「统计上真的赚」的硬标准）")
    if pos_net:
        b = max(pos_net, key=lambda c: c["net"])
        print(f"  净均值最好（taker）：{b['tf']}h × {b['mult']} · 净均值 {b['net']:+.3f}% · "
              f"笔数 {b['n']} · 95%CI [{b['ci_net_lo']:+.2f}, {b['ci_net_hi']:+.2f}] · "
              f"总净值 {b['total']:+.1f}% · 回撤 {b['dd']:.1f}% · 广度 {b['breadth']}")
    if pos_net_maker:
        b = max(pos_net_maker, key=lambda c: c["net_maker"])
        print(f"  净均值最好（maker）：{b['tf']}h × {b['mult']} · 净均值 {b['net_maker']:+.3f}% · "
              f"笔数 {b['n']} · 广度 {b['breadth']}")
    if cells:
        ba = max(cells, key=lambda c: c["alpha"])
        print(f"  择时 alpha 最大：{ba['tf']}h × {ba['mult']} · alpha {ba['alpha']:+.3f}% · "
              f"p={ba['p']:.3f} · 笔数 {ba['n']}（笔数越少的格子 alpha 越大，这是抽样噪声的特征）")
    print(f"通过择时显著性（Bonferroni）的格子：{len(sig)}/{len(cells)}")
    for c in sig:
        print(f"  {c['tf']}h × {c['mult']} · p={c['p']:.4f} · 毛均值 {c['gross']:+.3f}% "
              f"· 净均值 {c['net']:+.3f}% · 笔数 {c['n']} · 广度 {c['breadth']}")

    # 费用敏感性：最好格子的费用-收益曲线
    if pos_net:
        b = max(pos_net, key=lambda c: c["net"])
        print(f"\n【费用敏感性】净均值最优格子 {b['tf']}h × {b['mult']}"
              f"（毛均值 {b['gross']:+.3f}% · 持仓 {b['hold']:.0f} 根）")
        print(f"{'单边费率%':>10}{'往返%':>8}{'净均值%':>10}{'总净值%':>11}{'vs 买入持有pp':>15}")
        zero_fee_tot = None
        for f in (0.00, 0.02, 0.05, 0.08, 0.10, 0.15):
            eq, _ = simulate(b["trades"], f)
            tot = (eq[-1] - 1) * 100
            if f == 0.0:
                zero_fee_tot = tot
            print(f"{f:>10.2f}{2*f:>8.2f}{b['gross']-2*f:>10.3f}{tot:>11.1f}{tot-bh['total']:>15.1f}")
        if zero_fee_tot is not None:
            print(f"  → 即使手续费归零，总净值 {zero_fee_tot:+.1f}% 仍"
                  f"{'远低于' if zero_fee_tot < bh['total'] else '高于'}买入持有 {bh['total']:+.1f}%。")
    print("=" * 108)
    return 0


if __name__ == "__main__":
    sys.exit(main())
