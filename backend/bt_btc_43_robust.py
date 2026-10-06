# -*- coding: utf-8 -*-
"""bt_btc_43.py 的稳健性补充：去掉前 N 大盈利单后还剩多少。

PF 只有 1.09、248 笔，收益很可能挂在少数几笔上 —— 这一步就是把这个数算出来。
"""
from __future__ import annotations

import numpy as np

from bt_btc_43 import FEE, LIVE, ST_P, prod_rules
from backtest_engine import run_backtest
from sl2_tf_sweep import load_tf

CASH = 1000.0


def main():
    cs = load_tf("BTC-USDT", LIVE["tf"])
    r = run_backtest(cs, ST_P, init_cash=CASH, fee_rate=FEE, allow_short=True,
                     sizing="fixed", margin_usdt=LIVE["margin_usdt"],
                     leverage=LIVE["leverage"], exit_rules=prod_rules(LIVE),
                     full_trades=True)
    tl = r["trades_list"]
    # ⚠️ trade["pnl"] 只算了【出场】手续费；开仓那 0.05% 是在 open_pos 里直接从
    # equity 扣的、没记进这笔。所以要减掉，否则每笔高估 ~0.5U（248 笔共 124U）。
    entry_fee = LIVE["margin_usdt"] * LIVE["leverage"] * FEE
    pnl = np.array([t["pnl"] for t in tl], float) - entry_fee
    tot = pnl.sum()
    print(f"  对账：本金口径净 {r['final'] - CASH:+.1f}U ｜ 逐笔（已扣开仓费）"
          f"{tot:+.1f}U")
    srt = np.sort(pnl)[::-1]
    print(f"  总笔数 {len(tl)} ｜ 净 {tot:+.1f}U ｜ 回撤 {r['max_dd_pct']:.1f}%")
    print(f"\n  {'去掉前N大盈利':<14}{'剩余净U':>10}{'占原收益%':>11}{'剩余笔数':>9}")
    for n in (1, 2, 3, 5, 8, 10):
        rest = tot - srt[:n].sum()
        print(f"  N={n:<12}{rest:>10.1f}{rest / tot * 100:>11.1f}{len(tl) - n:>9}")
    # 单笔贡献分布
    pos = pnl[pnl > 0]
    print(f"\n  盈利笔数 {len(pos)}，其中前 5 大占全部盈利的 "
          f"{np.sort(pos)[::-1][:5].sum() / pos.sum() * 100:.1f}%")
    print(f"  单笔期望 {pnl.mean():+.2f}U ｜ 中位 {np.median(pnl):+.2f}U ｜ "
          f"标准差 {pnl.std(ddof=1):.2f}U")
    print(f"  t 值 = {pnl.mean() / (pnl.std(ddof=1) / np.sqrt(len(pnl))):.2f}"
          f"（|t|<2 ≈ 无法排除「期望为 0」）")


if __name__ == "__main__":
    main()
