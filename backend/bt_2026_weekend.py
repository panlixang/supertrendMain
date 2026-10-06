# -*- coding: utf-8 -*-
"""BTC 2026 · 15m / 30m ·「周末休市不开单」下，纯 ST vs ST+形态识别页 V3。

用户口径（本次）：
  - 去掉之前「美东 09:30-16:00」的时段过滤 —— 周一到周五全天照常开单
  - 只拦【休市日】：美东周六 / 周日，**不开新仓**（已有的反向平仓照常执行）
  - 周期 15m / 30m，窗口 2026 年
  - 对比两条：① 纯 SuperTrend 翻转（不过滤）② SuperTrend + 形态识别页 V3

实现要点
--------
1) 休市日只拦开仓、不拦平反向仓 —— 与 executor.on_signal 一致，
   否则周五开的仓碰到周末信号会被无限期挂住，回测就失真了。
2) V3 必须喂 4h 上下文。生产 pattern_trade.py:_v3_allow 是
   features_from_candles(cs=信号周期, cs4=4h, st_periods=10, st_mult=3.0)，
   回测里通过 candles_by_tf={"4h": ...} 还原；不传 4h 会算出完全不同的分。
3) 时区用美东（自带 DST 规则，不依赖 tzdata），沿用 backtest_us_session_1h_2026.py。

⚠️ signal_v3 是在「1h 信号 + 4h 上下文」上拟合的（pattern_trade.py 源码注释），
   套到 15m / 30m 是外推，本脚本只负责把这个差异量化出来。
"""
from __future__ import annotations

import datetime as dt
import functools
import sqlite3
import sys
from pathlib import Path

from backtest_engine import run_backtest
from indicators import super_trend
from position import ExitRules
from sl2_tf_sweep import load_db, load_tf

DB = Path("candle_data.db")
UTC = dt.timezone.utc
FEE = 0.0005

# 生产 ST 参数（pattern_trade.py ST_PERIODS / ST_MULTIPLIER）
ST_P = {"periods": 10, "multiplier": 3.0, "src": "hl2", "change_atr": True}

# 43 线上 BTC 的出场配置（上一轮核对过）
PROD_EXIT = ExitRules(enabled=True, tp1_pct=4.0, tp1_ratio=50.0,
                      move_sl_to_entry=True, sl_mode="pct", sl_pct=3.0,
                      trail_with_st=True, reverse_close=False)
SIMPLE_EXIT = ExitRules(enabled=False, trail_with_st=False)  # 翻向反手，纯信号

# ───────── 美东时间（自带 DST，沿用既有脚本） ─────────
def nth_weekday(y, m, wd, n):
    d = dt.date(y, m, 1)
    return dt.date(y, m, 1 + ((wd - d.weekday()) % 7) + (n - 1) * 7)


@functools.lru_cache(maxsize=None)
def et_weekday(ts_ms: int) -> int:
    """美东星期几：周一=0 … 周六=5 周日=6。"""
    t = dt.datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
    y = t.year
    ds = dt.datetime.combine(nth_weekday(y, 3, 6, 2), dt.time(7, 0), tzinfo=UTC)
    de = dt.datetime.combine(nth_weekday(y, 11, 6, 1), dt.time(6, 0), tzinfo=UTC)
    off = -4 if (ds <= t < de) else -5
    return (t + dt.timedelta(hours=off)).weekday()


def is_weekend_et(ts_ms: int) -> bool:
    return et_weekday(ts_ms) >= 5


# ───────── 取数 ─────────
def resample_ms(candles, step_ms: int, need: int):
    """按毫秒步长聚合（用于 15m → 30m）。丢掉不完整桶。"""
    groups, order = {}, []
    for c in candles:
        k = c["ts"] // step_ms
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(c)
    return [{"ts": k * step_ms, "o": g[0]["o"],
             "h": max(x["h"] for x in g), "l": min(x["l"] for x in g),
             "c": g[-1]["c"], "vol": sum(x.get("vol") or 0 for x in g)}
            for k in order for g in [groups[k]] if len(g) >= need]


def load_period(tf: str, lo_ms: int, hi_ms: int):
    if tf == "30m":
        base = load_db("BTC-USDT", "15m")
        cs = resample_ms(base, 30 * 60_000, 2)
    else:
        cs = load_db("BTC-USDT", tf)
    return [c for c in cs if lo_ms <= c["ts"] <= hi_ms]


HDR = (f"  {'' :<28}{'笔数':>5}{'胜率%':>7}{'收益%':>9}{'回撤%':>8}"
       f"{'PF':>7}{'均根':>7}{'费率耗U':>8}{'休市拦':>7}{'V3拦':>6}")


def show(tag, r):
    if not r or r.get("error"):
        print(f"  {tag:<28}{(r or {}).get('error', '无结果')}")
        return
    # fixed 100U×10x → 每笔名义 1000U，往返 0.1% = 1U 费率
    fees = r["trades"] * 100.0 * 10 * FEE * 2
    print(f"  {tag:<28}{r['trades']:>5}{r['win_rate']:>7.1f}{r['return_pct']:>9.2f}"
          f"{r['max_dd_pct']:>8.1f}"
          f"{(r['profit_factor'] if r['profit_factor'] is not None else 0):>7.2f}"
          f"{r['avg_bars']:>7.1f}{fees:>8.0f}{r.get('rest_blocked', 0):>7}"
          f"{r['er_blocked']:>6}")


def main():
    lo = int(dt.datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
    hi = (sqlite3.connect(str(DB))
          .execute("select max(ts) from candles where symbol='BTC-USDT' and tf='15m'")
          .fetchone()[0])
    print(f"窗口 2026-01-01 ~ {dt.datetime.fromtimestamp(hi/1000, tz=UTC):%Y-%m-%d}"
          f"（UTC）｜费率 {FEE*100:.2f}%/边｜仓位 fixed 100U×10x / 本金 1000U")
    print(f"休市规则：美东周六 / 周日 不开新仓（反向平仓照常）")
    print(f"ST 参数：SuperTrend(10, 3.0) hl2 changeATR（生产常量）\n")

    cbtf_4h = [c for c in load_tf("BTC-USDT", "4h")]
    results = {}

    for tf in ("15m", "30m"):
        cs = load_period(tf, lo, hi)
        if len(cs) < 500:
            print(f"!! {tf} 数据不足（{len(cs)} 根）")
            continue
        cbtf = {tf: cs, "4h": cbtf_4h}
        bh = (cs[-1]["c"] - cs[0]["c"]) / cs[0]["c"] * 100
        print("=" * 96)
        print(f"### {tf} · {len(cs)} 根 · "
              f"{dt.datetime.fromtimestamp(cs[0]['ts']/1000, tz=UTC):%Y-%m-%d} ~ "
              f"{dt.datetime.fromtimestamp(cs[-1]['ts']/1000, tz=UTC):%Y-%m-%d}"
              f" ｜买入持有 {bh:+.2f}%")
        print("=" * 96)
        print(HDR)

        for exit_tag, rules in (("生产出场 4.0/50", PROD_EXIT),
                                ("简化出场(翻向反手)", SIMPLE_EXIT)):
            for filt, v3 in (("纯 ST", False), ("ST + V3", True)):
                tag = f"{exit_tag} · {filt}"
                common = dict(init_cash=1000.0, fee_rate=FEE, allow_short=True,
                              sizing="fixed", margin_usdt=100.0, leverage=10,
                              exit_rules=rules, full_trades=True)
                r = run_backtest(cs, ST_P, **common,
                                 v3_filter=v3, gate_tf=tf, candles_by_tf=cbtf,
                                 block_if=is_weekend_et)
                show(tag + " · 周末拦", r)
                results[(tf, exit_tag, filt, "on")] = r
                # 同一配置但不拦周末 —— 用来分离「周末过滤」本身的贡献
                r0 = run_backtest(cs, ST_P, **common,
                                  v3_filter=v3, gate_tf=tf, candles_by_tf=cbtf)
                show(tag + " · 不拦", r0)
                results[(tf, exit_tag, filt, "off")] = r0
            print("  " + "─" * 90)

    # ── 信号级：这段话不来回跑引擎，直接从 flip 序列本身统计 ──
    print("\n" + "=" * 96)
    print("### 信号级统计（不依赖引擎计数）")
    print("=" * 96)
    print(f"  {'周期':<8}{'翻转总数':>10}{'落在休市日':>11}{'工作日翻转':>10}"
          f"{'V3放行':>9}{'V3放行率%':>11}{'对工作日放行率%':>15}")
    for tf in ("15m", "30m"):
        cs = load_period(tf, lo, hi)
        if len(cs) < 500:
            continue
        st = super_trend([c["o"] for c in cs], [c["h"] for c in cs],
                         [c["l"] for c in cs], [c["c"] for c in cs],
                         periods=10, multiplier=3.0, change_atr=True)
        flips = st["flips"]
        n_flip = len(flips)
        n_rest = sum(1 for f in flips if is_weekend_et(cs[f["i"]]["ts"]))
        # 「既要工作日、又要 V3 放行」的笔数直接取主表里那一格，避免口径打架
        rv = results[(tf, "简化出场(翻向反手)", "ST + V3", "on")]
        passed = rv["trades"] if rv and not rv.get("error") else 0
        wk_flip = n_flip - n_rest
        print(f"  {tf:<8}{n_flip:>10}{n_rest:>11}{wk_flip:>10}"
              f"{passed:>9}{passed / (wk_flip or 1) * 100:>15.1f}")


if __name__ == "__main__":
    sys.exit(main())
