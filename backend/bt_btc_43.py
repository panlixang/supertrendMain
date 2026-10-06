# -*- coding: utf-8 -*-
"""按 43 服务器线上pattern配置回测 BTC —— 只看收益/回撤。

配置来源：GET http://43.108.10.84:5174/api/pattern/trade/config （BTC-USDT-SWAP）
    allow_tfs=["4h"]  margin=100U  leverage=10  sizing_mode=fixed
    tp1_pct=4.0  tp1_ratio=50.0  exit_mode=single
    sl_pct=3.0  sl_mode=pct  move_sl_to_entry=true  trail_with_st=true
    reverse_close=false  filter_v3=false
ST 参数用 pattern_trade.py 的生产常量 ST_PERIODS=10 / ST_MULTIPLIER=3.0。

用法：
    cd backend && python bt_btc_43.py
    python bt_btc_43.py --cash 2000        # 换初始资金
    python bt_btc_43.py --tf 1h            # 同参数跑别的周期对照
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import Counter, defaultdict

from backtest_engine import run_backtest
from position import ExitRules
from sl2_tf_sweep import load_tf

# ── 43 线上配置（BTC-USDT-SWAP，2026-10-05 抓取） ─────────────────────────
LIVE = dict(
    tf="4h",
    margin_usdt=100.0,
    leverage=10,
    tp1_pct=4.0,
    tp1_ratio=50.0,
    sl_pct=3.0,
    sl_mode="pct",
    move_sl_to_entry=True,
    trail_with_st=True,
    reverse_close=False,
    filter_v3=False,
)
ST_P = {"periods": 10, "multiplier": 3.0, "src": "hl2", "change_atr": True}
FEE = 0.0005


def prod_rules(c):
    """把线上配置翻成 position.ExitRules（single 档 = 只有 tp1）。"""
    return ExitRules(
        enabled=True,
        tp1_pct=c["tp1_pct"], tp1_ratio=c["tp1_ratio"],
        move_sl_to_entry=c["move_sl_to_entry"],
        sl_mode=c["sl_mode"], sl_pct=c["sl_pct"],
        trail_with_st=c["trail_with_st"],
        reverse_close=c["reverse_close"],
    )


_HDR = (f"{'方案':<30}{'笔数':>6}{'胜率%':>8}{'收益%':>10}{'买入持有%':>10}"
        f"{'超额pp':>9}{'回撤%':>9}{'净U':>10}{'PF':>7}{'均根':>8}"
        f"{'TP1':>6}{'止损':>6}{'反向平':>7}")


def show(tag, r, init_cash):
    if not r or r.get("error"):
        print(f"  {tag:<30} {(r or {}).get('error', '无结果')}")
        return None
    net_u = r["final"] - init_cash
    print(f"  {tag:<30}{r['trades']:>6}{r['win_rate']:>8.1f}"
          f"{r['return_pct']:>10.2f}{r['hold_pct']:>10.1f}{r['alpha_pct']:>9.2f}"
          f"{r['max_dd_pct']:>9.1f}{net_u:>10.1f}"
          f"{(r['profit_factor'] if r['profit_factor'] is not None else 0):>7.2f}"
          f"{r['avg_bars']:>8.1f}{r['tp1_count']:>6}{r['stop_count']:>6}"
          f"{r['reverse_count']:>7}")
    return r


def yearly(trades, tag):
    g = defaultdict(list)
    for t in trades:
        y = dt.datetime.fromtimestamp(t["exit_ts"] / 1000, dt.timezone.utc).strftime("%Y")
        g[y].append(t)
    print(f"\n    {tag}")
    print(f"      {'年份':<6}{'笔数':>5}{'净U':>10}{'胜率%':>8}{'净均%':>9}   离场原因")
    for y in sorted(g):
        ts = g[y]
        n = len(ts)
        usd = sum(t["pnl"] for t in ts)
        win = sum(1 for t in ts if t["pnl"] > 0) / n * 100
        net = sum(t["pnl_pct"] for t in ts) / n - 2 * FEE * 100
        c = Counter(t["reason"] for t in ts)
        top = " ".join(f"{k}×{v}" for k, v in c.most_common(3))
        print(f"      {y:<6}{n:>5}{usd:>10.1f}{win:>8.1f}{net:>9.2f}   {top}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTC-USDT")
    ap.add_argument("--tf", default=LIVE["tf"])
    ap.add_argument("--cash", type=float, default=1000.0,
                    help="初始资金（默认 = 10x 线上保证金，即单笔占 10% 权益）")
    ap.add_argument("--margin", type=float, default=LIVE["margin_usdt"])
    ap.add_argument("--leverage", type=int, default=LIVE["leverage"])
    a = ap.parse_args(argv)

    cs = load_tf(a.symbol, a.tf)
    if len(cs) < 300:
        print(f"数据不足：{a.symbol} {a.tf} 只有 {len(cs)} 根")
        return 1
    t0 = dt.datetime.fromtimestamp(cs[0]["ts"] / 1000, dt.timezone.utc)
    t1 = dt.datetime.fromtimestamp(cs[-1]["ts"] / 1000, dt.timezone.utc)

    print("=" * 128)
    print(f"43 线上配置回测 · {a.symbol} {a.tf} · SuperTrend(10, 3.0) · "
          f"{len(cs)} 根 · {t0:%Y-%m-%d} ~ {t1:%Y-%m-%d}")
    print(f"  线上参数：TP1 {LIVE['tp1_pct']}% 平 {LIVE['tp1_ratio']}% ｜ 止损 "
          f"{LIVE['sl_mode']} {LIVE['sl_pct']}% ｜ 保本={LIVE['move_sl_to_entry']} "
          f"ST跟踪={LIVE['trail_with_st']} 反向平={LIVE['reverse_close']} "
          f"filter_v3={LIVE['filter_v3']}")
    print(f"  仓位：固定 {a.margin}U × {a.leverage}x ｜ 初始资金 {a.cash:.0f}U ｜ "
          f"费率 {FEE*100:.2f}%/边")
    print("=" * 128)
    print("  " + _HDR)

    base = dict(init_cash=a.cash, fee_rate=FEE,
                sizing="fixed", margin_usdt=a.margin, leverage=a.leverage)
    ex = prod_rules(LIVE)

    r_main = show("① 线上配置（4h 生产出场）",
                  run_backtest(cs, ST_P, **base, exit_rules=ex, full_trades=True),
                  a.cash)
    show("② 同配置只做多（allow_short=F）",
         run_backtest(cs, ST_P, **base, exit_rules=ex, allow_short=False),
         a.cash)
    show("③ 简化出场（出场全关，翻向反手）",
         run_backtest(cs, ST_P, **base,
                      exit_rules=ExitRules(enabled=False, trail_with_st=False)),
         a.cash)
    show("④ 线上配置 + 复利（equity 满仓）",
         run_backtest(cs, ST_P, init_cash=a.cash, fee_rate=FEE,
                      allow_short=True, exit_rules=ex),
         a.cash)

    print("=" * 128)
    if r_main and r_main.get("trades_list"):
        yearly(r_main["trades_list"], "逐年（按离场年）")
        tl = r_main["trades_list"]
        print(f"\n  汇总：净 {r_main['final'] - a.cash:+.1f}U ｜ 收益 "
              f"{r_main['return_pct']:+.2f}% ｜ 最大回撤 {r_main['max_dd_pct']:.2f}% ｜ "
              f"笔数 {r_main['trades']} ｜ 胜率 {r_main['win_rate']}% ｜ "
              f"PF {r_main['profit_factor']}")
        worst = sorted(tl, key=lambda t: t["pnl"])[:3]
        best = sorted(tl, key=lambda t: -t["pnl"])[:3]
        print(f"  最差3笔：{['%.2f%%' % t['pnl_pct'] for t in worst]}")
        print(f"  最好3笔：{['%.2f%%' % t['pnl_pct'] for t in best]}")
    print("=" * 128)
    return 0


if __name__ == "__main__":
    sys.exit(main())
