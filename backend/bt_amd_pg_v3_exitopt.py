# -*- coding: utf-8 -*-
"""AMD 形态识别页 + V3 过滤 + 1h 回测，并对出场(止盈止损)做网格寻优。

与 bt_sndk_pg_v3_exitopt.py 同口径，用于判断「SNDK 那套策略(43 当前配置)是否适合 AMD」。
① 先用 43 当前 SNDK 配置(tp1=4.0/50, sl=pct/3.0, 保本, 不跟踪, 单档, v3)直接套 AMD；
② 再对 AMD 单独做出场网格寻优(按 Calmar)，看最优档与收益天花板。

数据：本地 candle_data.db 的 AMD-USDT 1h / 4h（download_amd.py 已下）。
口径：1x 满仓复利（equity 复利），初始 1000U，单边 taker 0.05%。
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bt_sndk_pg_v3_exitopt import backtest   # 复用同口径回测函数
from bt_pattern_page import build_signals   # 形态识别页 + V3 信号
from sl2_tf_sweep import load_db            # 本地 db 读取

SYM = "AMD-USDT"
FEE = 0.05 / 100
INIT = 1000.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--long-only", action="store_true", help="仅多头(V 策略)")
    ap.add_argument("--no-opt", action="store_true", help="只出基准，不寻优")
    a = ap.parse_args()

    base = load_db(SYM, "1h")
    h4 = load_db(SYM, "4h")
    if not base or not h4:
        print("本地 db 无 AMD 数据，先跑 download_amd.py")
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

    # ① 直接套 43 当前 SNDK 配置
    cfg43 = dict(tp1_pct=4.0, tp1_ratio=50.0, sl_mode="pct", sl_pct=3.0,
                 trail_with_st=False, move_sl_to_entry=True, reverse_close=False)
    b = backtest(target, highs, lows, closes, up_plot, dn_plot, flip_idx,
                 **cfg43, diag=True)
    print(f"\n== ① 直接套 43-SNDK 配置(tp1=4/50 sl=pct/3 单档 保本 不跟踪) 套 AMD ==\n"
          f"   收益 {b['ret']:+.2f}%  回撤 {b['dd']:.2f}%  PF {b['pf']:.2f}  "
          f"笔数 {b['n']}  胜率 {b['wr']:.1f}%  Calmar {b['cal']:.2f}")

    # ② 默认出场基准
    base_cfg = dict(tp1_pct=1.5, tp1_ratio=70.0, sl_mode="st", sl_pct=2.0,
                    trail_with_st=True, move_sl_to_entry=True)
    bb = backtest(target, highs, lows, closes, up_plot, dn_plot, flip_idx,
                  **base_cfg, diag=False)
    print(f"\n== ② {tag} 默认出场基准(tp1=1.5/70 sl=st/2 trail=True) ==\n"
          f"   收益 {bb['ret']:+.2f}%  回撤 {bb['dd']:.2f}%  PF {bb['pf']:.2f}  "
          f"笔数 {bb['n']}  胜率 {bb['wr']:.1f}%  Calmar {bb['cal']:.2f}")

    if a.no_opt:
        return

    # ③ 网格寻优
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
    print(f"\n=== AMD {tag} 出场寻优 Top40（按 Calmar=收益/回撤，共 {total} 组合）===")
    print(f"{'Calmar':>6} {'ret%':>8} {'dd%':>7} {'PF':>6} {'n':>4} {'WR%':>6}  "
          f"tp1%  ratio  sl       trail")
    for cal, ret, dd, pf, n, wr, p, r, m, sp, tr in rows[:40]:
        sls = f"{m}/{sp:.1f}" if m == "pct" else f"{m}(兜底{sp:.0f})"
        print(f"{cal:6.2f} {ret:8.2f} {dd:7.2f} {pf:6.2f} {n:4} {wr:6.1f}  "
              f"{p:4.1f}  {r:3.0f}  {sls:>8}  {tr}")

    best = rows[0]
    print(f"\n结论: AMD 在 {tag} 下最优 Calmar={best[0]:.2f} 收益={best[1]:+.2f}% 回撤={best[2]:.2f}% "
          f"(对应 tp1={best[6]}/{best[7]} sl={best[8]}/{best[9]} trail={best[10]})")
    print("对照 SNDK 同口径(C) Calmar≈3.24 / 收益≈+26%；此行判断 AMD 是否同级别合适。")


if __name__ == "__main__":
    main()
