# -*- coding: utf-8 -*-
"""首页参数面板（SuperTrend + MTF Bias）在 TSLA / INTC / AMD / XAU 上的网格寻优。

================================================================================
寻的是什么
--------------------------------------------------------------------------------
首页「参数」页（frontend/src/components/ParamPanel.jsx）暴露两组指标参数：

    ▸ Supertrend Settings : periods(ATR Period) · multiplier(ATR Multiplier)
                            src(Source) · change_atr(Change ATR Method)
    ▸ MTF Bias Table      : fast_len · slow_len · ma_type

这两组就是本脚本的搜索空间。注意它们只管「指标怎么画 / 方向怎么判」，
不属于挂单页的 tp1/sl/trail（那些是出场参数，不在首页，本脚本不碰）。

为什么出场只用「下一个反向翻转平仓」
--------------------------------------------------------------------------------
为了让评价对象纯粹是**指标本身的方向判断力**，不带任何出场参数：
翻转开仓，下一个翻转平仓并反手。这样参数好坏 = 指标好坏，不会被
tp/sl 的参数海污染。想看带出场的版本用 bt_*_v3_exitopt.py。

过拟合防护（三重）
--------------------------------------------------------------------------------
① 样本外：前 70% IS 选优，后 30% OOS 只报告不复选；
② 随机入场零假设：对齐 sl2_tf_sweep.random_null，笔数/持仓根数/多空比例
   全部匹配，只随机化「何时进场」→ 择时 alpha 的 p 值；
③ 邻域稳定性：打印 periods × multiplier 的收益矩阵，看最优点是「高原」还是
   「孤峰」。孤峰 = 过拟合，落地要避开。

数据：本地 candle_data.db 的 1h（配置里 allow_tfs=1h，与实盘一致）。
成本：1x 满仓复利，初始 1000U，OKX taker 单边 0.05%。

用法：
    python bt_home_param_opt.py                       # 四品种全跑（ST + Bias）
    python bt_home_param_opt.py --sym XAU-USDT        # 单品种
    python bt_home_param_opt.py --part st --quick     # 只跑 ST，小网格
    python bt_home_param_opt.py --long-only           # 只做多不做空
    python bt_home_param_opt.py --tf 4h               # 换周期
================================================================================
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from indicators import ta_rma, ta_sma, ta_tr, source, ma   # 严格复用线上指标实现

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "candle_data.db")

SYMBOLS = ["TSLA-USDT", "INTC-USDT", "AMD-USDT", "XAU-USDT"]
FEE = 0.05 / 100        # 单边 taker
INIT = 1000.0           # 初始权益（1x 满仓复利）
IS_RATIO = 0.70         # 样本内占比
MIN_TRADES = 15         # ST 最少笔数（低于此视为样本不足）
MIN_TRADES_BIAS = 8     # Bias 是指标不是信号源，笔数门槛放低

# ── 搜索空间 ────────────────────────────────────────────────────
PERIODS_FULL = [5, 7, 10, 12, 14, 15, 18, 21, 25, 30]
MULTS_FULL = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0, 7.0, 9.1]
PERIODS_QUICK = [7, 10, 14, 20]
MULTS_QUICK = [1.5, 2.0, 3.0, 4.0, 6.0, 9.1]
SRCS = ["hl2", "hlc3", "close", "ohlc4"]
CHANGE_ATRS = [True, False]

FAST_LENS = [5, 10, 15, 20, 30, 50]
SLOW_LENS = [30, 50, 60, 100, 150, 200]
MA_TYPES = ["EMA", "SMA"]


# ═══════════════════════════════════════════════════════════════
# 数据
# ═══════════════════════════════════════════════════════════════
def load_db(symbol: str, tf: str = "1h"):
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute(
            "select ts,o,h,l,c,vol from candles where symbol=? and tf=? order by ts",
            (symbol, tf)).fetchall()
    finally:
        con.close()
    return [{"ts": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "vol": r[5]}
            for r in rows]


# ═══════════════════════════════════════════════════════════════
# 指标：SuperTrend（只取翻转点，逻辑与 indicators.super_trend 逐行一致）
# ═══════════════════════════════════════════════════════════════
def st_flips(opens, highs, lows, closes, periods, multiplier, src, change_atr):
    """返回 [(i, side)]，side=+1 翻多 / -1 翻空。

    只做棘轮 + 翻转判定，不构造 up_plot/dn_plot，网格里快很多。
    三处 Pine 细节照抄 indicators.super_trend 的注释：
      棘轮与翻转都用**前一根**的 up1/dn1；trend 种子 = 1；ATR 未成型前不参与。
    """
    n = len(closes)
    if n < periods + 1:
        return []
    tr = ta_tr(highs, lows, closes)
    atr = ta_rma(tr, periods) if change_atr else ta_sma(tr, periods)
    srcv = source(highs, lows, closes, opens, src)

    start = next((i for i in range(n) if atr[i] is not None), None)
    if start is None:
        return []

    up = [0.0] * n
    dn = [0.0] * n
    trend = [0] * n
    up[start] = srcv[start] - multiplier * atr[start]
    dn[start] = srcv[start] + multiplier * atr[start]
    trend[start] = 1

    flips = []
    for i in range(start + 1, n):
        up1, dn1 = up[i - 1], dn[i - 1]
        raw_up = srcv[i] - multiplier * atr[i]
        raw_dn = srcv[i] + multiplier * atr[i]
        up[i] = max(raw_up, up1) if closes[i - 1] > up1 else raw_up
        dn[i] = min(raw_dn, dn1) if closes[i - 1] < dn1 else raw_dn
        prev = trend[i - 1]
        if prev == -1 and closes[i] > dn1:
            cur = 1
        elif prev == 1 and closes[i] < up1:
            cur = -1
        else:
            cur = prev
        trend[i] = cur
        if cur != prev:
            flips.append((i, cur))
    return flips


def bias_flips(closes, fast_len, slow_len, ma_type):
    """MTF Bias 的翻转点：fast >= slow → +1，否则 -1（与 indicators.bias 同口径）。"""
    f = ma(closes, fast_len, ma_type)
    s = ma(closes, slow_len, ma_type)
    flips, prev = [], None
    for i in range(len(closes)):
        if f[i] is None or s[i] is None:
            continue
        b = 1 if f[i] >= s[i] else -1
        if prev is not None and b != prev:
            flips.append((i, b))
        prev = b
    return flips


# ═══════════════════════════════════════════════════════════════
# 回测：翻转开仓 → 下一个反向翻转平仓（纯指标择时，无 TP/SL）
# ═══════════════════════════════════════════════════════════════
def build_trades(flips, closes, long_only=False, i0=0, i1=None):
    """只统计开仓点落在 [i0, i1) 的交易。

    long_only: 只在翻多时开仓，遇到翻空信号平仓离场（空仓等待）。
    双向: 每次翻转都平掉上一笔并反手（ST 的经典用法）。
    """
    n = len(closes) if i1 is None else min(i1, len(closes))
    out = []
    m = len(flips)
    for k in range(m):
        i, side = flips[k]
        if i < i0 or i >= n:
            continue
        if long_only:
            if side < 0:
                continue
            j = next((flips[x][0] for x in range(k + 1, m) if flips[x][1] < 0), None)
            j = n - 1 if j is None or j >= n else j
        else:
            j = flips[k + 1][0] if k + 1 < m else n - 1
            if j >= n:
                j = n - 1
        if j <= i:
            continue
        out.append({"i": i, "j": j, "side": side,
                    "entry": closes[i], "exit": closes[j], "bars": j - i})
    return out


def simulate(trades, init=INIT):
    """1x 满仓复利。返回收益/回撤/PF/胜率/笔数。"""
    equity = init
    peak = init
    max_dd = 0.0
    pnls = []
    for t in trades:
        before = equity
        notional = equity
        coins = notional * (1 - FEE) / t["entry"]
        equity = (notional * (1 - FEE)
                  + t["side"] * coins * (t["exit"] - t["entry"])
                  - coins * t["exit"] * FEE)
        pnls.append(equity - before)
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100)
    wins = [p for p in pnls if p > 0]
    loss = [-p for p in pnls if p <= 0]
    pf = (sum(wins) / sum(loss)) if loss and sum(loss) > 0 else float("inf")
    ret = (equity / init - 1) * 100
    return dict(ret=ret, dd=max_dd, pf=pf, n=len(trades),
                wr=(len(wins) / len(trades) * 100 if trades else 0.0),
                cal=(ret / max_dd if max_dd > 1e-9 else 0.0),
                hold=(sum(t["bars"] for t in trades) / len(trades) if trades else 0.0))


def buy_hold(closes, i0=0, i1=None):
    n = len(closes) if i1 is None else i1
    a, b = closes[i0], closes[n - 1]
    return (b / a - 1) * 100


# ═══════════════════════════════════════════════════════════════
# 随机入场零假设（对齐 sl2_tf_sweep.random_null）
# ═══════════════════════════════════════════════════════════════
def random_null(closes, trades, reps, rng, i0=0, i1=None):
    """笔数、持仓根数、多空比例全部匹配真实交易，只随机化何时进场。

    零分布自动包含标的本身的漂移 → 剩下的差额才是「择时」的功劳。
    """
    n = len(closes) if i1 is None else i1
    if len(trades) < 10:
        return None
    cl = np.asarray(closes, float)
    bars = np.asarray([t["bars"] for t in trades], int)
    sides = np.asarray([t["side"] for t in trades], float)
    maxb = int(bars.max())
    hi = n - maxb - 1
    if hi <= i0 + 10:
        return None
    starts = rng.integers(i0 + 1, hi, size=(reps, len(trades)))
    e = cl[starts]
    x = cl[starts + bars[None, :]]
    gross = (x - e) / e * 100.0 * sides[None, :]
    return gross.mean(axis=1)


def trade_gross(trades):
    """真实交易的单笔毛收益 %（不含手续费），用于和零分布对比。"""
    return np.asarray([t["side"] * (t["exit"] - t["entry"]) / t["entry"] * 100.0
                       for t in trades], float)


# ═══════════════════════════════════════════════════════════════
# Part A：SuperTrend 参数寻优
# ═══════════════════════════════════════════════════════════════
def run_st(sym, tf, periods_list, mults, long_only, reps, seed, topn):
    cs = load_db(sym, tf)
    if not cs:
        print(f"[skip] {sym} {tf} 无数据")
        return None
    opens = [c["o"] for c in cs]
    highs = [c["h"] for c in cs]
    lows = [c["l"] for c in cs]
    closes = [c["c"] for c in cs]
    n = len(closes)
    split = int(n * IS_RATIO)
    rng = np.random.default_rng(seed)

    f = lambda t: dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
    print("\n" + "=" * 104)
    print(f"【Part A · SuperTrend 参数寻优】{sym}  {tf}  {n} 根  "
          f"{f(cs[0]['ts'])} ~ {f(cs[-1]['ts'])} UTC  "
          f"{'仅做多' if long_only else '多空双向'}")
    print("=" * 104)
    print(f"IS 前 {split} 根 ({f(cs[0]['ts'])} ~ {f(cs[split]['ts'])})   "
          f"OOS 后 {n - split} 根 ({f(cs[split]['ts'])} ~ {f(cs[-1]['ts'])})")
    bh_all = buy_hold(closes)
    bh_is = buy_hold(closes, 0, split)
    bh_oos = buy_hold(closes, split, n)
    print(f"买入持有基准：全样本 {bh_all:+.2f}% ｜ IS {bh_is:+.2f}% ｜ OOS {bh_oos:+.2f}%")

    # ATR/src 与 multiplier 无关，先缓存，省掉大量重复计算
    atr_cache = {}
    src_cache = {}
    rows = []
    total = len(periods_list) * len(mults) * len(SRCS) * len(CHANGE_ATRS)
    done = 0
    for ca in CHANGE_ATRS:
        for p in periods_list:
            key = (p, ca)
            if key not in atr_cache:
                tr = ta_tr(highs, lows, closes)
                atr_cache[key] = ta_rma(tr, p) if ca else ta_sma(tr, p)
            atr = atr_cache[key]
            for s in SRCS:
                skey = (s,)
                if skey not in src_cache:
                    src_cache[skey] = source(highs, lows, closes, opens, s)
                srcv = src_cache[skey]
                # 棘轮循环（照抄 indicators.super_trend，只保留翻转提取）
                start = next((i for i in range(n) if atr[i] is not None), None)
                if start is None:
                    done += len(mults)
                    continue
                for mp in mults:
                    done += 1
                    up = [0.0] * n
                    dn = [0.0] * n
                    trend = [0] * n
                    up[start] = srcv[start] - mp * atr[start]
                    dn[start] = srcv[start] + mp * atr[start]
                    trend[start] = 1
                    flips = []
                    for i in range(start + 1, n):
                        up1, dn1 = up[i - 1], dn[i - 1]
                        ru = srcv[i] - mp * atr[i]
                        rd = srcv[i] + mp * atr[i]
                        up[i] = ru if closes[i - 1] <= up1 else (ru if ru > up1 else up1)
                        dn[i] = rd if closes[i - 1] >= dn1 else (rd if rd < dn1 else dn1)
                        prev = trend[i - 1]
                        if prev == -1 and closes[i] > dn1:
                            cur = 1
                        elif prev == 1 and closes[i] < up1:
                            cur = -1
                        else:
                            cur = prev
                        trend[i] = cur
                        if cur != prev:
                            flips.append((i, cur))
                    tr_is = build_trades(flips, closes, long_only, 0, split)
                    if len(tr_is) < MIN_TRADES:
                        continue
                    r_is = simulate(tr_is)
                    tr_oos = build_trades(flips, closes, long_only, split, n)
                    r_oos = simulate(tr_oos) if tr_oos else dict(
                        ret=0.0, dd=0.0, pf=0.0, n=0, wr=0.0, cal=0.0, hold=0.0)
                    rows.append(dict(p=p, m=mp, src=s, ca=ca,
                                     is_ret=r_is["ret"], is_dd=r_is["dd"],
                                     is_cal=r_is["cal"], is_pf=r_is["pf"],
                                     is_n=r_is["n"], is_wr=r_is["wr"],
                                     is_hold=r_is["hold"],
                                     oos_ret=r_oos["ret"], oos_dd=r_oos["dd"],
                                     oos_cal=r_oos["cal"], oos_n=r_oos["n"],
                                     flips=flips))
                    if done % 200 == 0:
                        print(f"  网格进度 {done}/{total}", flush=True)

    if not rows:
        print(f"[失败] {sym} 没有任何满足最少 {MIN_TRADES} 笔的参数组合")
        return None
    print(f"有效组合 {len(rows)}/{total}（IS 笔数 ≥ {MIN_TRADES}）")

    rows.sort(key=lambda r: r["is_cal"], reverse=True)
    print(f"\n── IS Top{topn}（按 Calmar=收益/回撤 排序，OOS 列是同参数在后 30% 的表现）──")
    hdr = (f"{'#':>3} {'period':>6} {'mult':>5} {'src':>6} {'atr':>7} "
           f"{'IS笔':>5} {'IS收益%':>9} {'IS回撤%':>8} {'IS_Cal':>7} {'IS_PF':>6} {'IS胜率':>7} "
           f"{'OOS笔':>6} {'OOS收益%':>9} {'OOS_Cal':>8}")
    print(hdr)
    print("-" * len(hdr))
    for k, r in enumerate(rows[:topn], 1):
        print(f"{k:>3} {r['p']:>6} {r['m']:>5.1f} {r['src']:>6} "
              f"{'RMA' if r['ca'] else 'SMA':>7} "
              f"{r['is_n']:>5} {r['is_ret']:>9.2f} {r['is_dd']:>8.2f} "
              f"{r['is_cal']:>7.2f} {r['is_pf']:>6.2f} {r['is_wr']:>7.1f} "
              f"{r['oos_n']:>6} {r['oos_ret']:>9.2f} {r['oos_cal']:>8.2f}")

    # ── 最优组合的详检：显著性 + 邻域稳定性 ──
    best = rows[0]
    print(f"\n── IS 最优详检：period={best['p']} mult={best['m']} src={best['src']} "
          f"atr={'RMA' if best['ca'] else 'SMA'} ──")
    print(f"   IS  收益 {best['is_ret']:+.2f}% 回撤 {best['is_dd']:.2f}% "
          f"Calmar {best['is_cal']:.2f} PF {best['is_pf']:.2f} "
          f"胜率 {best['is_wr']:.1f}% 笔数 {best['is_n']} 均持仓 {best['is_hold']:.0f} 根"
          f"  （同期买入持有 {bh_is:+.2f}%）")
    print(f"   OOS 收益 {best['oos_ret']:+.2f}% 回撤 {best['oos_dd']:.2f}% "
          f"笔数 {best['oos_n']}  （同期买入持有 {bh_oos:+.2f}%）")

    tr_is = build_trades(best["flips"], closes, long_only, 0, split)
    g = trade_gross(tr_is)
    null = random_null(closes, tr_is, reps, rng, 0, split)
    if null is not None:
        p = float((null >= g.mean()).mean())
        alpha = float(g.mean() - null.mean())
        print(f"   随机入场对照（{reps} 次）：随机毛均值 {null.mean():+.3f}% ｜ "
              f"实际毛均值 {g.mean():+.3f}% ｜ 择时 alpha {alpha:+.3f}% ｜ p={p:.3f}"
              f"  {'← 显著' if p < 0.05 else '← 不显著（可能是运气）'}")
    else:
        print("   随机入场对照：样本不足，跳过")

    # 邻域矩阵：固定最优 src / change_atr，扫 periods × multiplier 的 OOS 收益
    print(f"\n── 邻域稳定性（最优 src={best['src']} atr={'RMA' if best['ca'] else 'SMA'}，"
          f"格子内 = IS收益/OOS收益 %，看最优点是高原还是孤峰）──")
    ps = periods_list
    ms = mults
    print("  " + "".join(f"{m:>12.1f}" for m in ms))
    lut = {(r["p"], r["m"]): r for r in rows
           if r["src"] == best["src"] and r["ca"] == best["ca"]}
    for p in ps:
        line = f"{p:>3}"
        for m in ms:
            r = lut.get((p, m))
            line += f"{'—':>12}" if r is None else \
                f"{r['is_ret']:>6.0f}/{r['oos_ret']:<6.0f}"
        print(line)

    # 稳健推荐：IS 前 30 里挑 OOS 最好的（承认 IS 排序噪声，用 OOS 做第二次筛选的参照）
    print(f"\n── 稳健性交叉：IS Top30 里 OOS 收益最好的是谁（若与 IS 最优差距大 = 参数不稳定）──")
    cand = sorted(rows[:30], key=lambda r: r["oos_ret"], reverse=True)[:5]
    for k, r in enumerate(cand, 1):
        print(f"   {k}. period={r['p']:<3} mult={r['m']:<4} src={r['src']:<6} "
              f"atr={'RMA' if r['ca'] else 'SMA':<3} IS {r['is_ret']:+7.2f}% "
              f"OOS {r['oos_ret']:+7.2f}%  IS_Cal {r['is_cal']:5.2f}")

    n_pos = sum(1 for r in rows if r["oos_ret"] > 0)
    n_beat = sum(1 for r in rows if r["oos_ret"] > bh_oos)
    print(f"\n   全网格 {len(rows)} 组：OOS 收益为正 {n_pos} 组 ｜ "
          f"OOS 跑赢买入持有({bh_oos:+.2f}%) {n_beat} 组")
    return {"sym": sym, "tf": tf, "n": n, "split": split, "rows": rows,
            "best": best, "bh_all": bh_all, "bh_is": bh_is, "bh_oos": bh_oos,
            "n_pos": n_pos, "n_beat": n_beat, "total": len(rows)}


# ═══════════════════════════════════════════════════════════════
# Part B：MTF Bias 表参数寻优
# ═══════════════════════════════════════════════════════════════
def run_bias(sym, tf, long_only, topn):
    cs = load_db(sym, tf)
    if not cs:
        return None
    closes = [c["c"] for c in cs]
    n = len(closes)
    split = int(n * IS_RATIO)

    print("\n" + "-" * 104)
    print(f"【Part B · MTF Bias 表参数寻优】{sym} {tf} —— "
          f"按 fast/slow MA 金叉死叉方向持仓（翻转换仓）")
    print("-" * 104)
    bh_is = buy_hold(closes, 0, split)
    bh_oos = buy_hold(closes, split, n)
    print(f"买入持有：IS {bh_is:+.2f}% ｜ OOS {bh_oos:+.2f}%")

    rows = []
    for mt in MA_TYPES:
        for fl in FAST_LENS:
            for sl in SLOW_LENS:
                if fl >= sl:
                    continue
                flips = bias_flips(closes, fl, sl, mt)
                tr_is = build_trades(flips, closes, long_only, 0, split)
                if len(tr_is) < MIN_TRADES_BIAS:
                    continue
                r_is = simulate(tr_is)
                tr_oos = build_trades(flips, closes, long_only, split, n)
                r_oos = simulate(tr_oos) if tr_oos else dict(
                    ret=0.0, dd=0.0, pf=0.0, n=0, wr=0.0, cal=0.0, hold=0.0)
                r_all = simulate(build_trades(flips, closes, long_only, 0, n))
                rows.append(dict(f=fl, s=sl, mt=mt,
                                 is_ret=r_is["ret"], is_dd=r_is["dd"],
                                 is_cal=r_is["cal"], is_n=r_is["n"],
                                 oos_ret=r_oos["ret"], oos_dd=r_oos["dd"],
                                 oos_n=r_oos["n"],
                                 all_ret=r_all["ret"], all_dd=r_all["dd"],
                                 all_cal=r_all["cal"], all_n=r_all["n"]))
    if not rows:
        print("  无有效组合")
        return None
    bh_all = buy_hold(closes)
    rows.sort(key=lambda r: r["is_cal"], reverse=True)
    print(f"\n── Bias Top{topn}（按 IS Calmar）──")
    hdr = (f"{'#':>3} {'fast':>5} {'slow':>5} {'type':>5} {'IS笔':>5} "
           f"{'IS收益%':>9} {'IS回撤%':>8} {'IS_Cal':>7} {'OOS笔':>6} {'OOS收益%':>9}"
           f" {'全样本%':>9} {'全回撤%':>9} {'全_Cal':>7}")
    print(hdr)
    print("-" * len(hdr))
    for k, r in enumerate(rows[:topn], 1):
        print(f"{k:>3} {r['f']:>5} {r['s']:>5} {r['mt']:>5} {r['is_n']:>5} "
              f"{r['is_ret']:>9.2f} {r['is_dd']:>8.2f} {r['is_cal']:>7.2f} "
              f"{r['oos_n']:>6} {r['oos_ret']:>9.2f} "
              f"{r['all_ret']:>9.2f} {r['all_dd']:>9.2f} {r['all_cal']:>7.2f}")

    # 按全样本 Calmar 再排一次：IS 排序会被样本内噪声带偏，全样本更能看长期性质
    print(f"\n── Bias 按全样本 Calmar 排序 Top{min(topn, 8)}"
          f"（全样本买入持有 {bh_all:+.2f}%）──")
    rs = sorted(rows, key=lambda r: r["all_cal"], reverse=True)
    for k, r in enumerate(rs[:min(topn, 8)], 1):
        print(f"{k:>3} fast={r['f']:<3} slow={r['s']:<4} {r['mt']:<4} "
              f"全样本 {r['all_ret']:+8.2f}% 回撤 {r['all_dd']:6.2f}% "
              f"Calmar {r['all_cal']:5.2f} 笔数 {r['all_n']:<4} "
              f"IS {r['is_ret']:+8.2f}% OOS {r['oos_ret']:+7.2f}%")
    return rows


# ═══════════════════════════════════════════════════════════════
# Part D：给定一组参数，出四品种的 IS / OOS / 全样本明细
# ═══════════════════════════════════════════════════════════════
def run_check(syms, tf, p, m, src, ca, long_only, reps, seed):
    print("\n" + "=" * 104)
    print(f"【参数详检】periods={p}  multiplier={m}  src={src}  "
          f"change_atr={'RMA' if ca else 'SMA'}  {tf}  "
          f"{'仅做多' if long_only else '多空双向'}")
    print("=" * 104)
    hdr = (f"{'品种':<12}{'区间':<6}{'收益%':>10}{'回撤%':>9}{'Calmar':>8}"
           f"{'PF':>7}{'胜率%':>8}{'笔数':>6}{'均持仓':>8}{'买入持有%':>11}{'超额%':>10}")
    print(hdr)
    print("-" * len(hdr))
    rng = np.random.default_rng(seed)
    for s in syms:
        cs = load_db(s, tf)
        if not cs:
            continue
        closes = [c["c"] for c in cs]
        n = len(closes)
        sp = int(n * IS_RATIO)
        flips = st_flips([c["o"] for c in cs], [c["h"] for c in cs],
                         [c["l"] for c in cs], closes, p, m, src, ca)
        for lab, i0, i1 in (("IS", 0, sp), ("OOS", sp, n), ("全样本", 0, n)):
            tr = build_trades(flips, closes, long_only, i0, i1)
            r = simulate(tr) if tr else dict(ret=0.0, dd=0.0, pf=0.0, n=0,
                                             wr=0.0, cal=0.0, hold=0.0)
            bh = buy_hold(closes, i0, i1)
            print(f"{s:<12}{lab:<6}{r['ret']:>10.2f}{r['dd']:>9.2f}{r['cal']:>8.2f}"
                  f"{r['pf']:>7.2f}{r['wr']:>8.1f}{r['n']:>6}{r['hold']:>8.0f}"
                  f"{bh:>11.2f}{r['ret'] - bh:>10.2f}")
        # 全样本的随机入场对照
        tr_all = build_trades(flips, closes, long_only, 0, n)
        if len(tr_all) >= 10:
            g = trade_gross(tr_all)
            null = random_null(closes, tr_all, reps, rng, 0, n)
            if null is not None:
                pv = float((null >= g.mean()).mean())
                print(f"{'':<12}全样本随机入场对照：随机 {null.mean():+.3f}% ｜ "
                      f"实际 {g.mean():+.3f}% ｜ alpha {g.mean()-null.mean():+.3f}% ｜ "
                      f"p={pv:.3f} {'← 显著' if pv < 0.05 else '← 不显著'}")
        print()


# ═══════════════════════════════════════════════════════════════
# Part C：跨品种一致性 —— 找一组四品种都通用的参数
# ═══════════════════════════════════════════════════════════════
def run_cross(syms, tf, periods_list, mults, long_only, topn):
    """同一组参数在四个品种上的 OOS 表现放在一起看。

    单品种选优必然挑到该品种的噪声；能在四个品种上都不难看的参数，
    才是「指标本身的性质」而不是「某段行情的巧合」。
    排序用 OOS 中位数（抗单点暴富），同时给出最差值（看最坏情况下多惨）。
    """
    data = {}
    for s in syms:
        cs = load_db(s, tf)
        if not cs:
            continue
        closes = [c["c"] for c in cs]
        data[s] = dict(
            o=[c["o"] for c in cs], h=[c["h"] for c in cs],
            l=[c["l"] for c in cs], c=closes, n=len(closes),
            split=int(len(closes) * IS_RATIO),
            bh_oos=buy_hold(closes, int(len(closes) * IS_RATIO), len(closes)))
    if not data:
        return None

    print("\n" + "=" * 104)
    print(f"【Part C · 跨品种一致性】{tf} · {'仅做多' if long_only else '多空双向'} · "
          f"同一组参数在 {len(data)} 个品种上的样本外表现")
    print("=" * 104)
    print("  OOS 买入持有：" + "  ".join(f"{s} {d['bh_oos']:+.1f}%" for s, d in data.items()))

    rows = []
    total = len(periods_list) * len(mults) * len(SRCS) * len(CHANGE_ATRS)
    done = 0
    for ca in CHANGE_ATRS:
        for p in periods_list:
            atr_c, src_c = {}, {}
            for s, d in data.items():
                tr = ta_tr(d["h"], d["l"], d["c"])
                atr_c[s] = ta_rma(tr, p) if ca else ta_sma(tr, p)
            for src in SRCS:
                for s, d in data.items():
                    src_c[s] = source(d["h"], d["l"], d["c"], d["o"], src)
                for m in mults:
                    done += 1
                    oos, isr, ns = [], [], []
                    for s, d in data.items():
                        flips = _flips_from(d, atr_c[s], src_c[s], m)
                        t_oos = build_trades(flips, d["c"], long_only, d["split"], d["n"])
                        t_is = build_trades(flips, d["c"], long_only, 0, d["split"])
                        if len(t_is) < MIN_TRADES or not t_oos:
                            oos.append(float("nan"))
                        else:
                            oos.append(simulate(t_oos)["ret"])
                        isr.append(simulate(t_is)["ret"] if t_is else float("nan"))
                        ns.append(len(t_oos))
                    val = [x for x in oos if x == x]
                    if len(val) < len(data):
                        continue
                    rows.append(dict(p=p, m=m, src=src, ca=ca,
                                     med=float(np.median(oos)),
                                     mean=float(np.mean(oos)),
                                     worst=float(np.min(oos)),
                                     is_med=float(np.median(isr)),
                                     oos=oos, n=ns,
                                     beat=sum(1 for s, x in zip(data, oos)
                                              if x > data[s]["bh_oos"])))
                    if done % 200 == 0:
                        print(f"  进度 {done}/{total}", flush=True)

    if not rows:
        print("  无有效组合")
        return None

    for key, lab in (("med", "OOS 收益中位数"), ("worst", "OOS 最差品种")):
        rs = sorted(rows, key=lambda r: r[key], reverse=True)
        print(f"\n── 按{lab}排序 Top{topn}（列 = 各品种 OOS 收益%）──")
        names = list(data.keys())
        print(f"{'#':>3} {'per':>4}{'mult':>6} {'src':>6}{'atr':>5}" +
              "".join(f"{s.replace('-USDT', ''):>9}" for s in names) +
              f"{'中位数':>9}{'最差':>9}{'IS中位':>9}{'跑赢BH':>7}")
        for k, r in enumerate(rs[:topn], 1):
            cells = "".join(f"{x:>9.1f}" for x in r["oos"])
            beat = f"{r['beat']}/{len(names)}"
            print(f"{k:>3} {r['p']:>4}{r['m']:>6.1f} {r['src']:>6}"
                  f"{'RMA' if r['ca'] else 'SMA':>5}{cells}"
                  f"{r['med']:>9.1f}{r['worst']:>9.1f}{r['is_med']:>9.1f}"
                  f"{beat:>7}")
    return rows


def _flips_from(d, atr, srcv, m):
    """棘轮 + 翻转（与 st_flips 同逻辑，接受预计算好的 atr/srcv 以免重复算）。"""
    n = d["n"]
    closes = d["c"]
    start = next((i for i in range(n) if atr[i] is not None), None)
    if start is None:
        return []
    up = [0.0] * n
    dn = [0.0] * n
    trend = [0] * n
    up[start] = srcv[start] - m * atr[start]
    dn[start] = srcv[start] + m * atr[start]
    trend[start] = 1
    flips = []
    for i in range(start + 1, n):
        up1, dn1 = up[i - 1], dn[i - 1]
        ru = srcv[i] - m * atr[i]
        rd = srcv[i] + m * atr[i]
        up[i] = ru if closes[i - 1] <= up1 else (ru if ru > up1 else up1)
        dn[i] = rd if closes[i - 1] >= dn1 else (rd if rd < dn1 else dn1)
        prev = trend[i - 1]
        if prev == -1 and closes[i] > dn1:
            cur = 1
        elif prev == 1 and closes[i] < up1:
            cur = -1
        else:
            cur = prev
        trend[i] = cur
        if cur != prev:
            flips.append((i, cur))
    return flips


# ═══════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sym", default="", help="只跑该品种，如 XAU-USDT；留空跑全部")
    ap.add_argument("--tf", default="1h")
    ap.add_argument("--part", choices=["st", "bias", "both"], default="both")
    ap.add_argument("--quick", action="store_true", help="小网格（先探路）")
    ap.add_argument("--long-only", action="store_true", help="只做多")
    ap.add_argument("--reps", type=int, default=500, help="随机入场对照次数")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--cross", action="store_true", help="跨品种一致参数分析")
    ap.add_argument("--check", default="", help="详检一组参数，如 5,4.0,hl2,sma")
    ap.add_argument("--out", default="", help="同时把输出写进该文件（utf-8）")
    a = ap.parse_args()

    if a.check:
        ps = [x.strip() for x in a.check.split(",")]
        run_check([a.sym] if a.sym else SYMBOLS, a.tf, int(float(ps[0])),
                  float(ps[1]), ps[2], ps[3].lower() != "sma",
                  a.long_only, a.reps, a.seed)
        return

    if a.out:
        import io
        buf = io.StringIO()
        real = sys.stdout
        class Tee:
            def write(self, s):
                real.write(s)
                buf.write(s)
            def flush(self):
                real.flush()
        sys.stdout = Tee()
        import atexit
        @atexit.register
        def _dump():
            sys.stdout = real
            with open(a.out, "w", encoding="utf-8") as f:
                f.write(buf.getvalue())
            real.write(f"\n[输出已写入 {a.out}]\n")

    periods = PERIODS_QUICK if a.quick else PERIODS_FULL
    mults = MULTS_QUICK if a.quick else MULTS_FULL
    syms = [a.sym] if a.sym else SYMBOLS

    print("首页参数寻优 · 搜索空间")
    print(f"  SuperTrend: periods={periods}")
    print(f"              multiplier={mults}")
    print(f"              src={SRCS}  change_atr={CHANGE_ATRS}")
    print(f"  → {len(periods)*len(mults)*len(SRCS)*len(CHANGE_ATRS)} 组合/品种")
    print(f"  MTF Bias  : fast={FAST_LENS} slow={SLOW_LENS} ma_type={MA_TYPES}")
    print(f"  出场：下一个反向翻转平仓（纯指标择时，无 TP/SL）｜ 成本 taker {FEE*100:.2f}%/边")

    if a.cross:
        run_cross(syms, a.tf, periods, mults, a.long_only, a.top)
        return

    summary = []
    for sym in syms:
        if a.part in ("st", "both"):
            r = run_st(sym, a.tf, periods, mults, a.long_only, a.reps, a.seed, a.top)
            if r:
                summary.append(r)
        if a.part in ("bias", "both"):
            run_bias(sym, a.tf, a.long_only, min(a.top, 12))

    if not summary:
        return
    print("\n" + "=" * 104)
    print("【四品种汇总】IS 最优参数 + 样本外表现")
    print("=" * 104)
    hdr = (f"{'品种':<12}{'period':>7}{'mult':>6}{'src':>6}{'atr':>5} "
           f"{'IS收益%':>9}{'IS_Cal':>8}{'OOS收益%':>10}{'OOS买入持有%':>13}"
           f"{'OOS正收益组合':>14}")
    print(hdr)
    print("-" * len(hdr))
    for r in summary:
        b = r["best"]
        pos = f"{r['n_pos']}/{r['total']}"
        print(f"{r['sym']:<12}{b['p']:>7}{b['m']:>6.1f}{b['src']:>6}"
              f"{'RMA' if b['ca'] else 'SMA':>5} "
              f"{b['is_ret']:>9.2f}{b['is_cal']:>8.2f}{b['oos_ret']:>10.2f}"
              f"{r['bh_oos']:>13.2f}{pos:>14}")
    print("\n注：OOS 是同参数在后 30% 的表现，未参与选优 → 才是可信度指标。")
    print("    IS 最优 ≠ 应落地；优先选邻域矩阵里「整片为正」的高原参数。")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
