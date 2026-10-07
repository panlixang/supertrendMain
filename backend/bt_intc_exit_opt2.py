# -*- coding: utf-8 -*-
"""INTC 1h 出场精细寻优 + 最优出场叠 V3 验证。

① 精细网格：tp1_pct × ratio × sl_mode × sl_pct × trail（pct 模式扫 sl_pct，st 模式 sl_pct 无关）
② 取纯 ST Top 出场，叠 v3_filter 对比，验证强趋势市是否该关 V3。
口径：equity 满仓 1x 复利。
"""
import datetime as dt

from backtest_engine import run_backtest
from bt_2026_weekend import ST_P, FEE, is_weekend_et
from position import ExitRules
from sl2_tf_sweep import load_db
from bt_2026_weekend import resample_ms

SYM = "INTC-USDT"
c1h = load_db(SYM, "1h"); c4h = load_db(SYM, "4h")
cs = c1h if len(c1h) >= 500 else resample_ms(load_db(SYM, "15m"), 60 * 60_000, 4)
print(f"{SYM} 1h {len(cs)} 根 "
      f"{dt.datetime.utcfromtimestamp(cs[0]['ts']/1000):%Y-%m-%d}~"
      f"{dt.datetime.utcfromtimestamp(cs[-1]['ts']/1000):%Y-%m-%d} UTC",
      flush=True)
common = dict(init_cash=1000.0, fee_rate=FEE, allow_short=True, sizing="equity")

tp1 = [1.5, 2.0, 3.0, 4.0, 8.0]
ratios = [30.0, 50.0, 70.0, 100.0]

rows = []
for p in tp1:
    for r in ratios:
        for m in ("pct", "st"):
            sps = [1.5, 2.0, 3.0] if m == "pct" else [2.0]
            trails = (True, False) if m == "pct" else (True,)
            for sp in sps:
                for t in trails:
                    rules = ExitRules(tp1_pct=p, tp1_ratio=r, move_sl_to_entry=True,
                                      sl_mode=m, sl_pct=sp, trail_with_st=t,
                                      reverse_close=False)
                    res = run_backtest(cs, ST_P, **common, exit_rules=rules,
                                       block_if=is_weekend_et)
                    ret = res["return_pct"]; dd = res["max_dd_pct"]
                    pf = res["profit_factor"]; n = res["trades"]
                    cal = ret / dd if dd > 0 else 0.0
                    rows.append((cal, ret, dd, pf, n, p, r, m, sp, t))
                    print(f"  ST p={p} r={r} {m} sl={sp} tr={t} "
                          f"ret={ret:.2f} dd={dd:.1f} cal={cal:.2f}", flush=True)

rows.sort(reverse=True, key=lambda x: x[0])
print("\n=== 精细寻优 Top15（纯ST, Calmar=收益/回撤）===")
print(f"{'Calmar':>6} {'ret%':>7} {'dd%':>6} {'PF':>5} {'n':>4}  tp1  r   sl   sl%  trail")
for cal, ret, dd, pf, n, p, r, m, sp, t in rows[:15]:
    print(f"{cal:6.2f} {ret:7.2f} {dd:6.1f} {pf:5.2f} {n:4}  "
          f"{p:4.1f} {r:3.0f} {m:3} {sp:4.1f}  {t}")

print("\n=== 纯ST Top 出场 叠 V3 对比（强趋势市该不该开 V3）===")
print(f"{'tp1':>4}{'r':>4}{'sl':>4}{'sl%':>5}{'trail':>6} | "
      f"{'ST_ret':>7}{'ST_cal':>7} | {'V3_ret':>7}{'V3_cal':>7}{'V3笔':>5}")
for cal, ret, dd, pf, n, p, r, m, sp, t in rows[:8]:
    rules = ExitRules(tp1_pct=p, tp1_ratio=r, move_sl_to_entry=True,
                      sl_mode=m, sl_pct=sp, trail_with_st=t, reverse_close=False)
    rv = run_backtest(cs, ST_P, **common, exit_rules=rules,
                      v3_filter=True, gate_tf="1h",
                      candles_by_tf={"1h": cs, "4h": c4h},
                      block_if=is_weekend_et)
    r3 = rv["return_pct"]; d3 = rv["max_dd_pct"]
    c3 = r3 / d3 if d3 > 0 else 0.0
    print(f"{p:4.1f}{r:4.0f}{m:>4}{sp:5.1f}{str(t):>6} | "
          f"{ret:7.2f}{cal:7.2f} | {r3:7.2f}{c3:7.2f}{rv['trades']:5}", flush=True)
