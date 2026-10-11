# -*- coding: utf-8 -*-
"""TSLA 形态识别页 + V3 过滤 + 1h 回测，出场(止盈止损)网格寻优。

数据来源：candle_data.db（先跑 download_tsla.py 拉 OKX 历史）。
与 bt_nvda_v3_exitopt.py / bt_skhynix_v3_exitopt.py 同口径，判断「SNDK 那套策略是否适合 TSLA」。
口径：1x 满仓复利（equity 复利），初始 1000U，单边 taker 0.05%。
注意：TSLA 当前 43 配置已是回测最优解（tp1=1.5/70 pct/3 single trail 开 v3 1h，
  见 check_tsla_43.py），本脚本用于复核/看是否值得把 tp1 提到 ~4-5。
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bt_sndk_pg_v3_exitopt import backtest   # 复用同口径回测函数
from bt_pattern_page import build_signals   # 形态识别页 + V3 信号
from sl2_tf_sweep import load_db

SYM = "TSLA-USDT"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--long-only", action="store_true", help="仅多头(V 策略)")
    ap.add_argument("--no-opt", action="store_true", help="只出基准，不寻优")
    a = ap.parse_args()

    base = load_db(SYM, "1h")
    h4 = load_db(SYM, "4h")
    if not base or not h4:
        print("db 无 TSLA 数据 —— 先跑 download_tsla.py（需 OKX 可达）")
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
    print(f"\n== ① 直接套 43-SNDK 配置(tp1=4/50 sl=pct/3 单档 保本 不跟踪) 套 TSLA ==\n"
          f"   收益 {b['ret']:+.2f}%  回撤 {b['dd']:.2f}%  PF {b['pf']:.2f}  "
          f"笔数 {b['n']}  胜率 {b['wr']:.1f}%  Calmar {b['cal']:.2f}")

    # ② 默认出场基准（=TSLA 当前43最优解）
    base_cfg = dict(tp1_pct=1.5, tp1_ratio=70.0, sl_mode="pct", sl_pct=3.0,
                    trail_with_st=True, move_sl_to_entry=True)
    bb = backtest(target, highs, lows, closes, up_plot, dn_plot, flip_idx,
                  **base_cfg, diag=False)
    print(f"\n== ② {tag} 默认出场基准(=TSLA当前43最优 tp1=1.5/70 sl=pct/3 trail=True) ==\n"
          f"   收益 {bb['ret']:+.2f}%  回撤 {bb['dd']:.2f}%  PF {bb['pf']:.2f}  "
          f"笔数 {bb['n']}  胜率 {bb['wr']:.1f}%  Calmar {bb['cal']:.2f}")

    # 买入持有对照
    bh = (closes[-1] / closes[0] - 1) * 100
    print(f"\n== 买入持有对照 ==  首收 {closes[0]:.2f} 末收 {closes[-1]:.2f}  "
          f"收益 {bh:+.2f}%")

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
                    if k % 40 == 0:
                        print(f"  寻优进度 {k}/{total}", flush=True)

    rows.sort(reverse=True, key=lambda x: x[0])
    print(f"\n=== TSLA {tag} 出场寻优 Top40（按 Calmar=收益/回撤，共 {total} 组合）===")
    print(f"{'Calmar':>6} {'ret%':>8} {'dd%':>7} {'PF':>6} {'n':>4} {'WR%':>6}  "
          f"tp1%  ratio  sl       trail")
    for cal, ret, dd, pf, n, wr, p, r, m, sp, tr in rows[:40]:
        sls = f"{m}/{sp:.1f}" if m == "pct" else f"{m}(兜底{sp:.0f})"
        print(f"{cal:6.2f} {ret:8.2f} {dd:7.2f} {pf:6.2f} {n:4} {wr:6.1f}  "
              f"{p:4.1f}  {r:3.0f}  {sls:>8}  {tr}")

    best = rows[0]
    print(f"\n结论: TSLA 在 {tag} 下最优 Calmar={best[0]:.2f} 收益={best[1]:+.2f}% "
          f"回撤={best[2]:.2f}% (对应 tp1={best[6]}/{best[7]} sl={best[8]}/{best[9]} trail={best[10]})")
    print(f"对照: 买入持有 {bh:+.2f}% ｜ SNDK Calmar≈3.24 ｜ AMD≈6.64 ｜ SKHYNIX≈1.52 ｜ NVDA≈7.30")
    print("提示: TSLA 当前43已=②基准；如需提收益看最优档 tp1 是否值得提到 4-5（保持 trail 开）。")


if __name__ == "__main__":
    main()
