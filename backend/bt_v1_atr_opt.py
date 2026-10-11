# -*- coding: utf-8 -*-
"""v1 策略的 ATR 参数寻优：periods × multiplier（10*3、10*4、10*5 …）

================================================================================
v1 是什么（全项目唯一的基线口径）
--------------------------------------------------------------------------------
    router.py:168  「当前策略信号：原始 SuperTrend 翻转（ATR 周期=10, factor=3.0）」
    pattern_trade.py:56  ST_PERIODS = 10 / ST_MULTIPLIER = 3.0
    → v1 = super_trend(periods=10, multiplier=3.0, src='hl2', change_atr=True)

所以本脚本固定 src=hl2、change_atr=True（与线上一致），**只扫 ATR 的
periods × multiplier 两个维度**，不做 V3 打分、不做形态过滤、不碰首页 Bias 表。

出场两套口径，各自成表
--------------------------------------------------------------------------------
① flip（v1 基线，sl2_voltarget.build_trades 口径）
    注释原文：「SuperTrend 翻向即开仓、下个反向翻向即平仓 —— 与实盘出场口径一致」
    仓位 1x 满仓复利。

② tp（形态识别页默认档，bt_pattern_page.backtest 口径）
    TP1 +1.5% 平 70% 并把止损移到开仓价（保本）；
    剩余 30% 跟随 SuperTrend 轨道；轨道无效时 2% 兜底；
    遇下一个反向翻转收盘平掉剩余。
    固定名义 10,000U（与 bt_pattern_page 同口径，无复利）。

成本：OKX taker 单边 0.05%。样本：前 70% IS 选优 / 后 30% OOS 只报告。

用法：
    python bt_v1_atr_opt.py                      # 四品种 × 两套出场
    python bt_v1_atr_opt.py --exit flip          # 只跑 flip 口径
    python bt_v1_atr_opt.py --sym XAU-USDT       # 单品种
    python bt_v1_atr_opt.py --tf 4h              # 换周期
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

from indicators import ta_rma, source, ta_tr

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "candle_data.db")

SYMBOLS = ["TSLA-USDT", "INTC-USDT", "AMD-USDT", "XAU-USDT"]
FEE = 0.05 / 100
INIT = 1000.0            # flip 口径初始权益（1x 满仓复利）
NOTIONAL = 10_000.0      # tp 口径固定名义（对齐 bt_pattern_page）
IS_RATIO = 0.70
MIN_TRADES = 12

# v1 默认出场档（bt_pattern_page / position.ExitRules 默认值）
TP1_PCT, TP1_RATIO = 1.5, 0.70
SL_FALLBACK = 2.0

PERIODS = [5, 7, 10, 12, 14, 15, 18, 20, 25, 30]
MULTS = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0, 7.0, 8.0, 9.1, 10.0]


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
def st_calc(opens, highs, lows, closes, periods, multiplier, src="hl2", change_atr=True):
    """v1 SuperTrend。返回 up_plot / dn_plot / flips。

    逻辑与 indicators.super_trend 逐行一致；up_plot/dn_plot 是 Pine 的
    plot.style_linebr（非当前趋势侧为 None），跟踪止损要用它。
    """
    n = len(closes)
    if n < periods + 1:
        return None
    tr = ta_tr(highs, lows, closes)
    atr = ta_rma(tr, periods) if change_atr else None
    if atr is None:
        from indicators import ta_sma
        atr = ta_sma(tr, periods)
    srcv = source(highs, lows, closes, opens, src)

    start = next((i for i in range(n) if atr[i] is not None), None)
    if start is None:
        return None

    up = [0.0] * n
    dn = [0.0] * n
    up_plot = [None] * n
    dn_plot = [None] * n
    trend = [0] * n
    flips = []

    up[start] = up_plot[start] = srcv[start] - multiplier * atr[start]
    dn[start] = srcv[start] + multiplier * atr[start]
    trend[start] = 1
    for i in range(start + 1, n):
        up1, dn1 = up[i - 1], dn[i - 1]
        ru = srcv[i] - multiplier * atr[i]
        rd = srcv[i] + multiplier * atr[i]
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
        if cur == 1:
            up_plot[i] = up[i]
        else:
            dn_plot[i] = dn[i]
    return dict(up_plot=up_plot, dn_plot=dn_plot, flips=flips)


# ═══════════════════════════════════════════════════════════════
def backtest_flip(st, closes, i0, i1):
    """出场①：下一个反向翻转收盘平仓（1x 满仓复利）。"""
    flips = st["flips"]
    n = min(i1, len(closes))
    equity = INIT
    peak = INIT
    max_dd = 0.0
    pnls = []
    m = len(flips)
    for k in range(m):
        i, side = flips[k]
        if i < i0 or i >= n:
            continue
        j = flips[k + 1][0] if k + 1 < m else n - 1
        if j >= n:
            j = n - 1
        if j <= i:
            continue
        before = equity
        coins = equity * (1 - FEE) / closes[i]
        equity = (equity * (1 - FEE) + side * coins * (closes[j] - closes[i])
                  - coins * closes[j] * FEE)
        pnls.append(equity - before)
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100)
    # flip 是复利：equity 从 INIT 起算，收益 = equity/INIT - 1
    return _metrics(pnls, equity, max_dd, INIT, INIT)


def backtest_tp(st, closes, highs, lows, i0, i1):
    """出场②：v1 默认出场档（固定名义 10,000U，对齐 bt_pattern_page）。"""
    n = min(i1, len(closes))
    flip_idx = {i for i, _ in st["flips"]}
    up_plot, dn_plot = st["up_plot"], st["dn_plot"]
    pnls = []
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    for i, side in st["flips"]:
        if i < i0 or i >= n:
            continue
        long = side > 0
        entry = closes[i]
        stp0 = up_plot[i] if long else dn_plot[i]
        if stp0 is None or (long and stp0 >= entry) or (not long and stp0 <= entry):
            stp0 = entry * (1 - SL_FALLBACK / 100) if long else entry * (1 + SL_FALLBACK / 100)
        stop = stp0
        tp1p = entry * (1 + TP1_PCT / 100) if long else entry * (1 - TP1_PCT / 100)
        tp1 = False
        coins = NOTIONAL / entry
        pnl = -entry * coins * FEE
        closed = False
        for j in range(i + 1, n):
            nl = up_plot[j] if long else dn_plot[j]
            if nl is not None:
                if long and nl > stop:
                    stop = nl
                elif (not long) and nl < stop:
                    stop = nl
            if long and lows[j] <= stop:
                rest = 1 - (TP1_RATIO if tp1 else 0)
                pnl += (stop - entry) * coins * rest - stop * coins * rest * FEE
                closed = True
                break
            if (not long) and highs[j] >= stop:
                rest = 1 - (TP1_RATIO if tp1 else 0)
                pnl += (entry - stop) * coins * rest - stop * coins * rest * FEE
                closed = True
                break
            if not tp1:
                if long and highs[j] >= tp1p:
                    pnl += (tp1p - entry) * coins * TP1_RATIO - tp1p * coins * TP1_RATIO * FEE
                    tp1, stop = True, entry
                elif (not long) and lows[j] <= tp1p:
                    pnl += (entry - tp1p) * coins * TP1_RATIO - tp1p * coins * TP1_RATIO * FEE
                    tp1, stop = True, entry
            if j in flip_idx:
                px = closes[j]
                rest = 1 - (TP1_RATIO if tp1 else 0)
                pnl += ((px - entry) if long else (entry - px)) * coins * rest - px * coins * rest * FEE
                closed = True
                break
        if not closed:
            px = closes[n - 1]
            rest = 1 - (TP1_RATIO if tp1 else 0)
            pnl += ((px - entry) if long else (entry - px)) * coins * rest - px * coins * rest * FEE
        pnls.append(pnl)
        eq += pnl
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / NOTIONAL * 100)
    return _metrics(pnls, eq, max_dd, NOTIONAL)


def _metrics(pnls, equity, max_dd, base, base_eq=0.0):
    wins = [p for p in pnls if p > 0]
    loss = [-p for p in pnls if p <= 0]
    pf = (sum(wins) / sum(loss)) if loss and sum(loss) > 0 else float("inf")
    denom = base
    ret = (equity - base_eq) / denom * 100 if denom else 0.0
    n = len(pnls)
    return dict(ret=ret, dd=max_dd, pf=pf, n=n,
                wr=(len(wins) / n * 100 if n else 0.0),
                cal=(ret / max_dd if max_dd > 1e-9 else 0.0))


def buy_hold(closes, i0, i1):
    return (closes[min(i1, len(closes)) - 1] / closes[i0] - 1) * 100


# ═══════════════════════════════════════════════════════════════
def run_one(sym, tf, mode, topn):
    cs = load_db(sym, tf)
    if not cs:
        print(f"[skip] {sym} {tf} 无数据")
        return None
    o = [c["o"] for c in cs]
    h = [c["h"] for c in cs]
    l = [c["l"] for c in cs]
    c = [c["c"] for c in cs]
    n = len(c)
    sp = int(n * IS_RATIO)
    f = lambda t: dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y-%m-%d")

    print("\n" + "=" * 100)
    print(f"【v1 ATR 寻优】{sym} {tf} · 出场={mode} · {n} 根 "
          f"{f(cs[0]['ts'])} ~ {f(cs[-1]['ts'])} UTC")
    print("=" * 100)
    bh_is = buy_hold(c, 0, sp)
    bh_oos = buy_hold(c, sp, n)
    print(f"买入持有：IS {bh_is:+.2f}% ｜ OOS {bh_oos:+.2f}%")
    print("固定 src=hl2 / change_atr=True（与线上 v1 一致），只扫 periods × multiplier")

    rows = []
    for p in PERIODS:
        for m in MULTS:
            st = st_calc(o, h, l, c, p, m)
            if not st:
                continue
            r_is = (backtest_flip(st, c, 0, sp) if mode == "flip"
                    else backtest_tp(st, c, h, l, 0, sp))
            if r_is["n"] < MIN_TRADES:
                continue
            r_oos = (backtest_flip(st, c, sp, n) if mode == "flip"
                     else backtest_tp(st, c, h, l, sp, n))
            r_all = (backtest_flip(st, c, 0, n) if mode == "flip"
                     else backtest_tp(st, c, h, l, 0, n))
            rows.append(dict(p=p, m=m, is_ret=r_is["ret"], is_dd=r_is["dd"],
                             is_cal=r_is["cal"], is_pf=r_is["pf"], is_n=r_is["n"],
                             is_wr=r_is["wr"],
                             oos_ret=r_oos["ret"], oos_n=r_oos["n"],
                             all_ret=r_all["ret"], all_dd=r_all["dd"],
                             all_cal=r_all["cal"], all_n=r_all["n"]))
    if not rows:
        print("  无有效组合")
        return None

    lut = {(r["p"], r["m"]): r for r in rows}

    # ── 二维矩阵：IS 收益 / OOS 收益 ──
    print(f"\n── periods × multiplier 矩阵（格内 = IS收益% / OOS收益%，"
          f"— 表示 IS 笔数 <{MIN_TRADES}）──")
    print("  " + "".join(f"{m:>12.1f}" for m in MULTS))
    for p in PERIODS:
        line = f"{p:>3}"
        for m in MULTS:
            r = lut.get((p, m))
            if r is None:
                line += f"{'—':>12}"
            else:
                mark = "*" if (p == 10 and m == 3.0) else " "
                line += f"{r['is_ret']:>5.0f}/{r['oos_ret']:<6.0f}{mark}"
        print(line)
    print("  （* 号 = v1 基线 10 × 3.0）")

    # ── Top 明细 ──
    rows.sort(key=lambda r: r["is_cal"], reverse=True)
    print(f"\n── IS Top{topn}（按 Calmar）──")
    hdr = (f"{'#':>3} {'ATR':>8} {'IS笔':>5}{'IS收益%':>9}{'IS回撤%':>9}{'IS_Cal':>8}"
           f"{'IS_PF':>7}{'IS胜率':>8}{'OOS笔':>6}{'OOS收益%':>10}"
           f"{'全样本%':>10}{'全回撤%':>9}")
    print(hdr)
    print("-" * len(hdr))
    for k, r in enumerate(rows[:topn], 1):
        print(f"{k:>3} {r['p']:>3}×{r['m']:<4.1f}{r['is_n']:>5}{r['is_ret']:>9.2f}"
              f"{r['is_dd']:>9.2f}{r['is_cal']:>8.2f}{r['is_pf']:>7.2f}"
              f"{r['is_wr']:>7.1f}%{r['oos_n']:>6}{r['oos_ret']:>10.2f}"
              f"{r['all_ret']:>10.2f}{r['all_dd']:>9.2f}")

    base = lut.get((10, 3.0))
    if base:
        print(f"\n── v1 基线 10 × 3.0 定位（网格 {len(rows)} 组里的排名）──")
        rk_cal = sorted(rows, key=lambda r: r["is_cal"], reverse=True).index(base) + 1
        rk_oos = sorted(rows, key=lambda r: r["oos_ret"], reverse=True).index(base) + 1
        print(f"   IS 收益 {base['is_ret']:+.2f}% 回撤 {base['is_dd']:.2f}% "
              f"Calmar {base['is_cal']:.2f}（第 {rk_cal}/{len(rows)}）笔数 {base['is_n']}")
        print(f"   OOS 收益 {base['oos_ret']:+.2f}%（第 {rk_oos}/{len(rows)}，"
              f"同期买入持有 {bh_oos:+.2f}%）")
        print(f"   全样本 收益 {base['all_ret']:+.2f}% 回撤 {base['all_dd']:.2f}% "
              f"笔数 {base['all_n']}")

    n_beat = sum(1 for r in rows if r["oos_ret"] > bh_oos)
    n_pos = sum(1 for r in rows if r["oos_ret"] > 0)
    print(f"\n   网格 {len(rows)} 组：OOS 收益为正 {n_pos} 组 ｜ "
          f"OOS 跑赢买入持有({bh_oos:+.2f}%) {n_beat} 组")
    return dict(sym=sym, tf=tf, mode=mode, rows=rows, bh_oos=bh_oos,
                n_beat=n_beat, n_pos=n_pos, total=len(rows), base=base)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sym", default="")
    ap.add_argument("--tf", default="1h")
    ap.add_argument("--exit", choices=["flip", "tp", "both"], default="both")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--out", default="", help="同时落盘（utf-8）")
    a = ap.parse_args()

    if a.out:
        import io, atexit
        buf = io.StringIO()
        real = sys.stdout
        class Tee:
            def write(self, s):
                real.write(s); buf.write(s)
            def flush(self):
                real.flush()
        sys.stdout = Tee()
        @atexit.register
        def _dump():
            sys.stdout = real
            with open(a.out, "w", encoding="utf-8") as f:
                f.write(buf.getvalue())
            real.write(f"\n[输出已写入 {a.out}]\n")

    syms = [a.sym] if a.sym else SYMBOLS
    modes = ["flip", "tp"] if a.exit == "both" else [a.exit]
    print("v1 ATR 参数寻优（periods × multiplier）")
    print(f"  periods={PERIODS}")
    print(f"  multiplier={MULTS}   → {len(PERIODS)*len(MULTS)} 组合/品种")
    print(f"  固定 src=hl2 / change_atr=True（v1 基线）｜ 品种 {syms} ｜ 出场 {modes}")
    print(f"  成本 taker {FEE*100:.2f}%/边 ｜ IS 70% 选优 / OOS 30% 只报告")

    summary = []
    for mode in modes:
        for s in syms:
            r = run_one(s, a.tf, mode, a.top)
            if r:
                summary.append(r)

    if not summary:
        return
    print("\n" + "=" * 100)
    print("【四品种汇总】各出场口径下 IS 最优 ATR 参数")
    print("=" * 100)
    hdr = (f"{'出场':<6}{'品种':<12}{'最优ATR':>10}{'IS收益%':>10}{'IS_Cal':>8}"
           f"{'OOS收益%':>10}{'OOS买入持有%':>13}{'全样本%':>10}{'基线10×3全样本%':>16}")
    print(hdr)
    print("-" * len(hdr))
    for r in summary:
        b = sorted(r["rows"], key=lambda x: x["is_cal"], reverse=True)[0]
        bl = f"{r['base']['all_ret']:+.2f}%" if r["base"] else "—"
        atr = f"{b['p']}×{b['m']:.1f}"
        print(f"{r['mode']:<6}{r['sym']:<12}{atr:>10}"
              f"{b['is_ret']:>10.2f}{b['is_cal']:>8.2f}{b['oos_ret']:>10.2f}"
              f"{r['bh_oos']:>13.2f}{b['all_ret']:>10.2f}{bl:>16}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
