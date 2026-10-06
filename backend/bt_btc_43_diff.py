# -*- coding: utf-8 -*-
"""归因：上次那个「正向」的数 → 线上实际配置，到底是哪一步掉的。

对照源：backend/_diag_4h_prodexit.txt（上次存档）
归档结论在 backend/_diag_4h_yearly.txt：「生产出场 总收益 +64.0% / 回撤 27.3%」

STEP 0 已自证数据零漂移：用上次的口径（mult=5.0 / init 10000 / equity 1x）
今天跑出来仍是 114 笔 / 36.8% / +93.77% / 回撤 65.3%（简化出场那行），逐位吻合。

所以剩下的差异必须在参数上找：
    上次   mult=5.0  + tp1 1.5%/平30%  + equity 复利(10000U)
    线上   mult=3.0  + tp1 4.0%/平50%  + fixed 100U×10x
         ↑ 生产常量 ST_MULTIPLIER（pattern_trade.py），面板改不了
                    ↑ 43 面板上 BTC 的实际值
"""
from __future__ import annotations

import datetime as dt

from backtest_engine import run_backtest
from position import ExitRules
from sl2_tf_sweep import load_tf

FEE = 0.0005
EQUITY_BASE = dict(init_cash=10000.0, fee_rate=FEE)          # 上次口径
FIXED_BASE = dict(init_cash=1000.0, fee_rate=FEE,            # 线上口径
                  sizing="fixed", margin_usdt=100.0, leverage=10)
_SIMPLE = ExitRules(enabled=False, trail_with_st=False)


def prod(tp1=1.5, ratio=70.0, sl_mode="pct", sl_pct=3.0):
    return ExitRules(enabled=True, tp1_pct=tp1, tp1_ratio=ratio,
                     move_sl_to_entry=True, sl_mode=sl_mode, sl_pct=sl_pct,
                     trail_with_st=True, reverse_close=False)


def show(tag, r):
    if not r or r.get("error"):
        print(f"  {tag:<32}{r.get('error') if r else '无结果'}")
        return
    print(f"  {tag:<32}{r['trades']:>5}{r['win_rate']:>7.1f}{r['return_pct']:>10.2f}"
          f"{r['max_dd_pct']:>9.1f}{r['avg_bars']:>8.1f}{r['tp1_count']:>6}"
          f"{r['stop_count']:>6}{r['reverse_count']:>6}")
    return r


HDR = (f"  {'' :<32}{'笔数':>5}{'胜率%':>7}{'收益%':>10}{'回撤%':>9}"
       f"{'均根':>8}{'TP1':>6}{'止损':>6}{'反向':>6}")


def main():
    cs = load_tf("BTC-USDT", "4h")
    print(f"数据：BTC-USDT 4h {len(cs)} 根 "
          f"{dt.datetime.fromtimestamp(cs[0]['ts']/1000, dt.timezone.utc):%Y-%m-%d} ~ "
          f"{dt.datetime.fromtimestamp(cs[-1]['ts']/1000, dt.timezone.utc):%Y-%m-%d}"
          f"（与上次同一份 candle_data.db，见 STEP 0 复现）\n")

    def go(mult, rules, base):
        p = {"periods": 10, "multiplier": mult, "src": "hl2", "change_atr": True}
        return run_backtest(cs, p, **base, exit_rules=rules)

    print("=" * 100)
    print("STEP 1  归因链：从「上次那个正向数」走到「线上实际配置」")
    print("=" * 100)
    print(HDR)
    print("  ── 起点：上次的口径与参数 ──")
    show("① mult5.0 · 1.5/30 · equity", go(5.0, prod(ratio=30.0), EQUITY_BASE))
    print("  ── 换掉 mult（真正的生产值 3.0）──")
    show("② mult3.0 · 1.5/30 · equity", go(3.0, prod(ratio=30.0), EQUITY_BASE))
    print("  ── 再把 tp1 换成线上的 4.0/50 ──")
    show("③ mult3.0 · 4.0/50 · equity", go(3.0, prod(tp1=4.0, ratio=50.0), EQUITY_BASE))
    print("  ── 最后换仓位口径（fixed，线上 sizing_mode）──")
    show("④ mult3.0 · 4.0/50 · fixed ←线上", go(3.0, prod(tp1=4.0, ratio=50.0), FIXED_BASE))
    print("  ── 参照：若只有 mult=5.0 而 tp1 仍是线上值 ──")
    show("⑤ mult5.0 · 4.0/50 · equity", go(5.0, prod(tp1=4.0, ratio=50.0), EQUITY_BASE))
    show("⑥ mult5.0 · 1.5/30 · fixed", go(5.0, prod(ratio=30.0), FIXED_BASE))

    print("\n" + "=" * 100)
    print("STEP 2  生产 mult=3.0 不可改，那 tp1 该配多少？（线上 fixed 口径）")
    print("=" * 100)
    print(HDR)
    show("线上现值  4.0% / 平50%", go(3.0, prod(tp1=4.0, ratio=50.0), FIXED_BASE))
    for tp1, ratio in ((3.0, 50.0), (2.5, 50.0), (2.0, 50.0), (2.0, 30.0),
                       (1.5, 50.0), (1.5, 30.0), (1.5, 70.0), (1.5, 20.0),
                       (1.0, 30.0)):
        tag = f"tp1={tp1}% / 平{ratio:.0f}%"
        show(tag + ("  ←现值" if (tp1, ratio) == (4.0, 50.0) else ""),
             go(3.0, prod(tp1=tp1, ratio=ratio), FIXED_BASE))
    print("  ── 参照：出场全关，持到翻向（这一泣策略的毛上限）──")
    show("简化出场（无 TP/SL）", go(3.0, _SIMPLE, FIXED_BASE))
    print("=" * 100)


if __name__ == "__main__":
    main()
