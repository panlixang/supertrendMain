# -*- coding: utf-8 -*-
"""INTC 1h 出场（止盈止损）寻优：扫描 tp1_pct × tp1_ratio × sl_mode。

口径：equity 满仓 1x 复利（对齐用户当前关注口径），仅纯 ST（不含 V3 过滤）。
按 Calmar = 收益% / 回撤% 排序取 Top。
"""
import datetime as dt

from backtest_engine import run_backtest
from bt_2026_weekend import ST_P, FEE, is_weekend_et
from position import ExitRules
from sl2_tf_sweep import load_db
from bt_2026_weekend import resample_ms

SYM = "INTC-USDT"
c1h = load_db(SYM, "1h")
cs = c1h if len(c1h) >= 500 else resample_ms(load_db(SYM, "15m"), 60 * 60_000, 4)
print(f"{SYM} 1h {len(cs)} 根 "
      f"{dt.datetime.utcfromtimestamp(cs[0]['ts']/1000):%Y-%m-%d}~"
      f"{dt.datetime.utcfromtimestamp(cs[-1]['ts']/1000):%Y-%m-%d} UTC",
      flush=True)

common = dict(init_cash=1000.0, fee_rate=FEE, allow_short=True, sizing="equity")

tp1_pcts = [1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0]
ratios   = [30.0, 50.0, 70.0, 100.0]
sl_modes = ["pct", "st"]

rows = []
for p in tp1_pcts:
    for r in ratios:
        for m in sl_modes:
            rules = ExitRules(tp1_pct=p, tp1_ratio=r, move_sl_to_entry=True,
                              sl_mode=m, sl_pct=2.0, trail_with_st=True,
                              reverse_close=False)
            res = run_backtest(cs, ST_P, **common, exit_rules=rules,
                               block_if=is_weekend_et)
            ret = res["return_pct"]; dd = res["max_dd_pct"]
            pf = res["profit_factor"]; n = res["trades"]
            cal = ret / dd if dd > 0 else 0.0
            rows.append((cal, ret, dd, pf, n, p, r, m))
            print(f"  done p={p} r={r} m={m} ret={ret:.2f} dd={dd:.1f} cal={cal:.2f}",
                  flush=True)

rows.sort(reverse=True, key=lambda x: x[0])
print("\n=== INTC 1h 出场寻优 Top15（按 Calmar=收益/回撤）===")
print(f"{'Calmar':>6} {'ret%':>7} {'dd%':>6} {'PF':>5} {'n':>4}  tp1%  ratio  sl")
for cal, ret, dd, pf, n, p, r, m in rows[:15]:
    print(f"{cal:6.2f} {ret:7.2f} {dd:6.1f} {pf:5.2f} {n:4}  {p:4.1f}  {r:3.0f}  {m}")
print(f"\n扫描组合 {len(rows)} 个")
