# -*- coding: utf-8 -*-
"""形态识别策略(V3) 回测：1h 与 30m 的回撤/收益。

用法：python bt_spcx_v3.py SPCX-USDT
      python bt_spcx_v3.py INTC-USDT
数据：本地 candle_data.db。优先用直接下载的 1h/4h；缺 1h 时由 15m 重采样。
口径：ST(10,3.0) hl2 changeATR · 费率 0.05%/边 · fixed 50U×10x（对齐线上配置）。
      V3 过滤：signal_v3 打分（1h 原生拟合；30m 上谨慎参考）。
"""
from __future__ import annotations

import datetime as dt
import os
import sys

from backtest_engine import run_backtest
from bt_2026_weekend import (ST_P, FEE, PROD_EXIT, SIMPLE_EXIT,
                             resample_ms, is_weekend_et)
from position import ExitRules
from indicators import super_trend
from sl2_tf_sweep import load_db

SYM = sys.argv[1] if len(sys.argv) > 1 else "SPCX-USDT"
UTC = dt.timezone.utc

# 杠杆/仓位口径通过环境变量切换（默认对齐线上 50U×10x）。
# 例：BT_FIXED=1000 BT_LEV=1 python bt_spcx_v3.py INTC-USDT  -> 1x 满仓
FIXED = float(os.environ.get("BT_FIXED", "50"))
LEV = float(os.environ.get("BT_LEV", "10"))

# 出场口径：prod=线上BTC(TP1 4.0/50, pct止损3%)；s150=TP1 1.5/70(单档, pct止损3%)
EXIT_150 = ExitRules(tp1_pct=1.5, tp1_ratio=70.0, move_sl_to_entry=True,
                     sl_mode="pct", sl_pct=3.0, trail_with_st=True,
                     reverse_close=False)
EXIT = {"prod": PROD_EXIT, "s150": EXIT_150}[os.environ.get("BT_EXIT", "prod").strip()]
EXIT_LABEL = os.environ.get("BT_EXIT", "prod").strip()


def buy_hold(cs):
    if not cs:
        return 0.0
    return (cs[-1]["c"] - cs[0]["c"]) / cs[0]["c"] * 100


def main():
    c1h = load_db(SYM, "1h")
    c4h = load_db(SYM, "4h")
    c15 = load_db(SYM, "15m")
    if not c1h and not c15:
        print(f"没有 {SYM} 的 1h/15m 数据，先运行对应下载脚本")
        return
    cs1h = c1h if len(c1h) >= 500 else resample_ms(c15, 60 * 60_000, 4)
    cs30 = resample_ms(c15, 30 * 60_000, 2) if len(c15) >= 500 else []
    cs4h = c4h if len(c4h) >= 200 else (resample_ms(cs1h, 4 * 60 * 60_000, 4)
                                        if cs1h else [])
    src = "1h直接" if len(c1h) >= 500 else "15m重采样"
    print(f"标的 {SYM} · 1h {len(cs1h)} 根({src}) · 4h {len(cs4h)} 根 · 30m {len(cs30)} 根")
    if cs1h:
        print(f"  1h 范围 {dt.datetime.utcfromtimestamp(cs1h[0]['ts']/1000):%Y-%m-%d} ~ "
              f"{dt.datetime.utcfromtimestamp(cs1h[-1]['ts']/1000):%Y-%m-%d} UTC")
    SIZING = os.environ.get("BT_SIZING", "fixed").strip()
    sizing_label = (f"fixed {FIXED}U×{LEV}x" if SIZING == "fixed"
                    else "equity满仓1x(复利)")
    print(f"口径：ST{ST_P} · 费率 {FEE*100:.2f}%/边 · {sizing_label} · 出场={EXIT_LABEL} · "
          f"V3=信号周期+4h上下文 · 美东周末不开新仓\n")

    common = dict(init_cash=1000.0, fee_rate=FEE, allow_short=True, sizing=SIZING)
    if SIZING == "fixed":
        common.update(margin_usdt=FIXED, leverage=LEV)
    tfs = [("1h", cs1h)]
    if cs30:
        tfs.append(("30m", cs30))
    for tf, cs in tfs:
        cbtf = {tf: cs, "4h": cs4h}
        rn = run_backtest(cs, ST_P, **common, exit_rules=EXIT,
                          block_if=is_weekend_et)
        rv = run_backtest(cs, ST_P, **common, exit_rules=EXIT,
                          v3_filter=True, gate_tf=tf, candles_by_tf=cbtf,
                          block_if=is_weekend_et)
        bh = buy_hold(cs)
        print("=" * 92)
        print(f"### {tf}（买入持有 {bh:+.2f}%）")
        print("=" * 92)
        print(f"  {'模式':<10}{'笔数':>6}{'收益%':>9}{'回撤%':>9}{'PF':>7}{'净U':>9}{'skip':>6}")
        print("  " + "-" * 90)
        pf = lambda r: r["profit_factor"] if r["profit_factor"] is not None else 0.0
        print(f"  {'纯ST':<10}{rn['trades']:>6}{rn['return_pct']:>9.2f}"
              f"{rn['max_dd_pct']:>9.1f}{pf(rn):>7.2f}"
              f"{rn['final']-common['init_cash']:>9.0f}{rn['skipped_insufficient']:>6}")
        print(f"  {'V3':<10}{rv['trades']:>6}{rv['return_pct']:>9.2f}"
              f"{rv['max_dd_pct']:>9.1f}{pf(rv):>7.2f}"
              f"{rv['final']-common['init_cash']:>9.0f}{rv['skipped_insufficient']:>6}")
        v3_rate = rv["trades"] / rn["trades"] * 100 if rn["trades"] else 0
        print(f"\n  V3 放行率 {v3_rate:.1f}%（纯ST {rn['trades']} 笔 → V3 {rv['trades']} 笔）")
        print(f"  V3 相对纯ST：收益 {rv['return_pct']-rn['return_pct']:+.2f}pp · "
              f"回撤 {rv['max_dd_pct']-rn['max_dd_pct']:+.1f}pp\n")

    print("注：样本较短、统计意义有限；30m+V3 中 V3 是在 1h 上拟合的，原周期外请谨慎参考。")


if __name__ == "__main__":
    main()
