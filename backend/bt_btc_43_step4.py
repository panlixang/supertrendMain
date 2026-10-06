# -*- coding: utf-8 -*-
"""STEP 4：mult 才是大头吗？（mult 3.0→5.0 差了 23pp，必须单独验）

⚠️ 改动代价提醒：ST_MULTIPLIER 是 pattern_trade.py 里的**全局常量**，
改它会同时影响 NVDA / CL / SKHYNIX / TSLA（它们都跑 1h），不是 BTC 专属。
所以这里只能回答「BTC 4h 上值不值得动」，动之前必须先在 1h 品种上验证。
"""
from __future__ import annotations

import datetime as dt

from backtest_engine import run_backtest
from position import ExitRules
from sl2_tf_sweep import load_tf

FEE = 0.0005
FIXED = dict(sizing="fixed", margin_usdt=100.0, leverage=10, fee_rate=FEE)


def prod(tp1=1.5, ratio=70.0, sl_mode="pct", sl_pct=3.0):
    return ExitRules(enabled=True, tp1_pct=tp1, tp1_ratio=ratio,
                     move_sl_to_entry=True, sl_mode=sl_mode, sl_pct=sl_pct,
                     trail_with_st=True, reverse_close=False)


def go(cs, mult, rules):
    p = {"periods": 10, "multiplier": mult, "src": "hl2", "change_atr": True}
    return run_backtest(cs, p, init_cash=1000.0, **FIXED,
                        allow_short=True, exit_rules=rules)


def main():
    cs = load_tf("BTC-USDT", "4h")
    mid = len(cs) // 2
    mid_ts = cs[mid]["ts"]
    parts = {
        "上半": [c for c in cs if c["ts"] <= mid_ts],
        "下半": [c for c in cs if c["ts"] >= mid_ts],
        "全程": cs,
    }
    print(f"数据 BTC-USDT 4h {len(cs)} 根｜切点 "
          f"{dt.datetime.fromtimestamp(mid_ts/1000, dt.timezone.utc):%Y-%m-%d}｜"
          f"fixed 100U×10x / 本金 1000U\n")
    print(f"  {'方案':<30}{'段':<6}{'笔数':>5}{'胜率%':>7}{'收益%':>9}{'回撤%':>8}")
    print("  " + "─" * 66)
    for mult in (3.0, 4.0, 5.0):
        for tp1, ratio in ((4.0, 50.0), (1.5, 20.0)):
            tag = f"mult{mult:.1f} · tp1 {tp1}/{ratio:.0f}"
            if mult == 3.0 and tp1 == 4.0:
                tag += " ←线上"
            for label, part in parts.items():
                r = go(part, mult, prod(tp1=tp1, ratio=ratio))
                if not r or r.get("error"):
                    print(f"  {tag:<30}{label:<6}{(r or {}).get('error','')}")
                    continue
                print(f"  {tag:<30}{label:<6}{r['trades']:>5}{r['win_rate']:>7.1f}"
                      f"{r['return_pct']:>9.2f}{r['max_dd_pct']:>8.1f}")
            print("  " + "─" * 66)


if __name__ == "__main__":
    main()
