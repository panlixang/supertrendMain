# -*- coding: utf-8 -*-
"""SNDK 形态识别页 + V3 过滤 + 1h 回测，并对出场(止盈止损)做网格寻优。

信号：复用 bt_pattern_page.build_signals —— 1h SuperTrend 翻转 + 4h 形态上下文
      + V3 趋势打分闸门（filter_v3=true 即策略 C）。
数据：从本地 candle_data.db 读 SNDK-USDT 的 1h / 4h（download_sndk.py 已下）。
口径：1x 满仓复利（equity 复利，对齐用户当前关注口径），初始 1000U，单边 taker 0.05%。
寻优：tp1_pct × tp1_ratio × sl_mode(sl_pct) × trail_with_st，按 Calmar=收益/回撤 排序。

用法：
    python bt_sndk_pg_v3_exitopt.py                 # 默认 C 策略(V3) + 寻优
    python bt_sndk_pg_v3_exitopt.py --long-only     # 仅多头(V 策略)
    python bt_sndk_pg_v3_exitopt.py --no-opt        # 只出基准，不寻优
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bt_pattern_page import build_signals   # 形态识别页 + V3 信号
from sl2_tf_sweep import load_db            # 本地 db 读取

SYM = "SNDK-USDT"
FEE = 0.05 / 100                 # 单边 taker，与 bt_pattern_page 一致
INIT = 1000.0                    # 初始权益（1x 满仓复利）
SL_FALLBACK = 2.0                # sl_mode='st' 时 ST 线无效的兜底 %


# ── 回测（参数化出场 + equity 满仓1x 复利）──
def backtest(sigs, highs, lows, closes, up_plot, dn_plot, flip_idx,
             tp1_pct, tp1_ratio, sl_mode, sl_pct, trail_with_st,
             move_sl_to_entry=True, reverse_close=False, init=INIT, fee=FEE,
             diag=False):
    tp1_ratio = tp1_ratio / 100.0        # 入参是百分比(70)，转比例(0.70)
    equity = init
    trades = []
    eq_curve = [equity]
    last_exit = -1
    for s in sigs:
        i, long = s["i"], s["dir"] > 0
        if i <= last_exit:        # 上一笔尚未平仓 -> 严格 1x 单持仓，跳过不重叠
            continue
        entry = closes[i]
        coins = equity / entry    # 满仓 1x 复利（不重叠，不会超额杠杆）
        # 初始止损
        if sl_mode == "st":
            stp0 = up_plot[i] if long else dn_plot[i]
            if stp0 is None or (long and stp0 >= entry) or (not long and stp0 <= entry):
                stp0 = entry * (1 - SL_FALLBACK / 100) if long else entry * (1 + SL_FALLBACK / 100)
        else:
            stp0 = entry * (1 - sl_pct / 100) if long else entry * (1 + sl_pct / 100)
        stop = stp0
        tp1p = entry * (1 + tp1_pct / 100) if long else entry * (1 - tp1_pct / 100)
        tp1 = False
        pnl = -entry * coins * fee                    # 开仓费
        reason, ex, ex_i, closed = "末根平仓", closes[-1], len(closes) - 1, False
        for j in range(i + 1, len(closes)):
            if not reverse_close:
                # 跟踪止损跟随 SuperTrend 轨道（只朝有利方向）
                if trail_with_st:
                    nl = up_plot[j] if long else dn_plot[j]
                    if nl is not None:
                        if long and nl > stop:
                            stop = nl
                        elif (not long) and nl < stop:
                            stop = nl
                # 先判止损（保守）
                if long and lows[j] <= stop:
                    px, rest = stop, 1 - (tp1_ratio if tp1 else 0)
                    pnl += (px - entry) * coins * rest - px * coins * rest * fee
                    reason, ex, ex_i, closed = "止损", px, j, True
                    break
                if (not long) and highs[j] >= stop:
                    px, rest = stop, 1 - (tp1_ratio if tp1 else 0)
                    pnl += (entry - px) * coins * rest - px * coins * rest * fee
                    reason, ex, ex_i, closed = "止损", px, j, True
                    break
                # TP1 触发 -> 平 tp1_ratio% 并(可选)保本
                if not tp1:
                    if long and highs[j] >= tp1p:
                        pnl += (tp1p - entry) * coins * tp1_ratio - tp1p * coins * tp1_ratio * fee
                        tp1, stop = True, (entry if move_sl_to_entry else stop)
                    elif (not long) and lows[j] <= tp1p:
                        pnl += (entry - tp1p) * coins * tp1_ratio - tp1p * coins * tp1_ratio * fee
                        tp1, stop = True, (entry if move_sl_to_entry else stop)
            if j in flip_idx:                          # 下一个翻转必为反向 -> 平剩余
                px = closes[j]
                rest = 1 - (tp1_ratio if tp1 else 0)
                pnl += ((px - entry) if long else (entry - px)) * coins * rest - px * coins * rest * fee
                reason, ex, ex_i, closed = "反向信号", px, j, True
                break
        if not closed:
            px = closes[-1]
            rest = 1 - (tp1_ratio if tp1 else 0)
            pnl += ((px - entry) if long else (entry - px)) * coins * rest - px * coins * rest * fee
        equity += pnl
        eq_curve.append(equity)
        trades.append({"pnl": pnl, "dir": s["dir"], "reason": reason, "tp1": tp1,
                       "entry": entry, "exit": ex, "stop0": stp0})
        last_exit = ex_i
        if diag:
            if len(trades) <= 25:
                print(f"[t{len(trades):2d}] dir={s['dir']} entry={entry:.2f} "
                      f"exit={ex:.2f} stop0={stp0:.2f} reason={reason} tp1={tp1} "
                      f"pnl={pnl:.2f} eq={equity:.2f}")
            if abs(equity) > 1e9:
                print("[boom-break]")
                break

    if trades:
        big = max(trades, key=lambda t: abs(t["pnl"]))
        print(f"[diag] 最大|pnl|={big['pnl']:.2f} dir={big['dir']} "
              f"entry={big['entry']:.2f} exit={big['exit']:.2f} stop0={big['stop0']:.2f} "
              f"reason={big['reason']} tp1={big['tp1']}")
    ret = (equity / init - 1) * 100
    peak = init
    max_dd = 0.0
    for e in eq_curve:
        peak = max(peak, e)
        if peak > 0:
            max_dd = max(max_dd, (peak - e) / peak * 100)
    wins = [t for t in trades if t["pnl"] > 0]
    loss = [t for t in trades if t["pnl"] <= 0]
    gross_w = sum(t["pnl"] for t in wins)
    gross_l = sum(-t["pnl"] for t in loss)
    pf = gross_w / gross_l if gross_l > 0 else float("inf")
    n = len(trades)
    return dict(ret=ret, dd=max_dd, pf=pf, n=n,
                cal=(ret / max_dd if max_dd > 0 else 0.0),
                wr=(len(wins) / n * 100 if n else 0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--long-only", action="store_true", help="仅多头(V 策略)")
    ap.add_argument("--no-opt", action="store_true", help="只出基准，不寻优")
    a = ap.parse_args()

    base = load_db(SYM, "1h")
    h4 = load_db(SYM, "4h")
    if not base or not h4:
        print("本地 db 无 SNDK 数据，先跑 download_sndk.py")
        return
    print(f"{SYM} 1h {len(base)} 根 "
          f"{dt.datetime.utcfromtimestamp(base[0]['ts']/1000):%Y-%m-%d} ~ "
          f"{dt.datetime.utcfromtimestamp(base[-1]['ts']/1000):%Y-%m-%d} UTC",
          flush=True)

    sigs, opens, highs, lows, closes, up_plot, dn_plot, flip_idx = build_signals(base, h4)
    all_s = sigs
    c_s = [s for s in sigs if s.get("v3_execute")]
    v_s = [s for s in c_s if s["dir"] > 0]
    print(f"信号: 全部翻转 {len(all_s)} | V3放行(C) {len(c_s)} | V3+仅多(V) {len(v_s)}")

    target = v_s if a.long_only else c_s
    tag = "V(仅多)" if a.long_only else "C(V3)"

    # 基准：默认出场（对齐 bt_pattern_page 默认档）
    base_cfg = dict(tp1_pct=1.5, tp1_ratio=70.0, sl_mode="st", sl_pct=2.0,
                    trail_with_st=True, move_sl_to_entry=True)
    b = backtest(target, highs, lows, closes, up_plot, dn_plot, flip_idx,
                 **base_cfg, diag=True)
    print(f"\n== {tag} 基准(默认出场 tp1=1.5/70 sl=st/2 trail=True) ==\n"
          f"   收益 {b['ret']:+.2f}%  回撤 {b['dd']:.2f}%  PF {b['pf']:.2f}  "
          f"笔数 {b['n']}  胜率 {b['wr']:.1f}%  Calmar {b['cal']:.2f}")

    if a.no_opt:
        return

    # 网格寻优
    tp1_pcts = [1.0, 2.0, 3.0, 4.0, 5.0]
    ratios = [30.0, 50.0, 70.0, 100.0]
    sl_cfgs = [("pct", 3.0), ("st", 2.0)]
    trails = [True, False]

    rows = []
    total = len(tp1_pcts) * len(ratios) * len(sl_cfgs) * len(trails)
    k = 0
    for p in tp1_pcts:
        for r in ratios:
            for m, sp in sl_cfgs:
                for tr in trails:
                    k += 1
                    res = backtest(target, highs, lows, closes, up_plot, dn_plot, flip_idx,
                                   tp1_pct=p, tp1_ratio=r, sl_mode=m, sl_pct=sp,
                                   trail_with_st=tr)
                    rows.append((res["cal"], res["ret"], res["dd"], res["pf"], res["n"],
                                res["wr"], p, r, m, sp, tr))
                    if k % 20 == 0:
                        print(f"  寻优进度 {k}/{total}", flush=True)

    rows.sort(reverse=True, key=lambda x: x[0])
    print(f"\n=== {tag} 出场寻优 Top40（按 Calmar=收益/回撤，共 {total} 组合）===")
    print(f"{'Calmar':>6} {'ret%':>8} {'dd%':>7} {'PF':>6} {'n':>4} {'WR%':>6}  "
          f"tp1%  ratio  sl       trail")
    for cal, ret, dd, pf, n, wr, p, r, m, sp, tr in rows[:40]:
        sls = f"{m}/{sp:.1f}" if m == "pct" else f"{m}(兜底{sp:.0f})"
        print(f"{cal:6.2f} {ret:8.2f} {dd:7.2f} {pf:6.2f} {n:4} {wr:6.1f}  "
              f"{p:4.1f}  {r:3.0f}  {sls:>8}  {tr}")
    print("\n提示：SNDK 样本仅 ~7 个月（2026-03 上线），寻优结论有过拟合风险，"
          "建议对 BTC/ETH 等更长样本交叉验证。")


if __name__ == "__main__":
    main()
