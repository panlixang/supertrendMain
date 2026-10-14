# -*- coding: utf-8 -*-
"""
当前线上（43 版）参数 · ETH 分年回测（2025 / 2026）
================================================================================

参数来源
--------
直接解析 `pattern_trade.json` 里 ETH-USDT-SWAP 那一项，不手抄 ——
以后线上改配置，这个脚本自动跟着变。

当前值：
    tf=4h · SuperTrend(15, 2.0) · tp1_pct=5.0 · tp1_ratio=50.0
    sl_pct=5.0 / sl_mode=pct · move_sl_to_entry · trail_with_st
    exit_mode=single · reverse_close=false · filter_v3=false
    sizing=fixed · margin=50U · leverage=10x · allow_short=True（反手）

口径
----
- 出场用 `backtest_engine.run_backtest` → 内部就是 `position.py` 状态机，与生产同源。
- 费率 0.05%/边（OKX taker），往返 0.10%。
- 按**开仓年**拆解。净% = 毛收益/开仓名义价值 − 2×费率（名义价值口径，不含复利）；
  净USDT 是 fixed 口径下真实的钱（$50 保证金 × 10 杠杆 = $500 名义/笔，无复利）。
- ⚠️ 2026 只到 9-30，是不完整年（约 9 个月），与整年不可直接比大小。

用法
----
    cd backend
    python sl2_eth43.py
    python sl2_eth43.py --symbol BTC-USDT      # 换成别的品种对照
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from backtest_engine import run_backtest
from position import ExitRules

DB_PATH = Path("candle_data.db")
CFG_PATH = Path("pattern_trade.json")
TF_HOURS = {"1h": 1, "2h": 2, "4h": 4, "8h": 8, "12h": 12, "1d": 24}

FEE_PCT = 0.05          # 单边费率 %
ST_PERIODS = 15         # 线上信号参数（用户确认：真实生产 ST 是 15/2.0，不是 pattern_trade.py 的 10/3.0）
ST_MULTIPLIER = 2.0


# ──────────────────────────────────────────────────────────────
# 取数：优先 1h 重采样（与既有研究口径一致：同窗口、同 UTC 对齐，跨资产可比）
# ──────────────────────────────────────────────────────────────
def load_db(symbol: str, tf: str = "1h"):
    con = sqlite3.connect(str(DB_PATH))
    try:
        rows = con.execute(
            "select ts,o,h,l,c,vol from candles where symbol=? and tf=? order by ts",
            (symbol, tf)).fetchall()
    finally:
        con.close()
    return [{"ts": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "vol": r[5]}
            for r in rows]


def resample(candles, hours: int):
    if hours <= 1:
        return candles
    step = hours * 3600_000
    groups, order = {}, []
    for c in candles:
        k = c["ts"] // step
        g = groups.get(k)
        if g is None:
            groups[k] = [c]
            order.append(k)
        else:
            g.append(c)
    out = []
    for k in order:
        g = groups[k]
        if len(g) < hours:
            continue
        out.append({"ts": k * step, "o": g[0]["o"],
                    "h": max(x["h"] for x in g), "l": min(x["l"] for x in g),
                    "c": g[-1]["c"], "vol": sum(x.get("vol") or 0 for x in g),
                    "_n": len(g)})
    while out and out[0]["_n"] < hours:
        out.pop(0)
    return out


def load_tf(symbol: str, tf: str = "4h"):
    h = TF_HOURS.get(tf)
    if h is None:
        raise ValueError(f"不支持的周期 {tf}")
    # 配置里是 ETH-USDT-SWAP（永续），DB 里是 ETH-USDT（去 -SWAP）
    db_sym = symbol.replace("-SWAP", "")
    c1h = load_db(db_sym, "1h")
    if not c1h:
        return load_db(db_sym, tf)
    return resample(c1h, h)


# ──────────────────────────────────────────────────────────────
# 线上配置
# ──────────────────────────────────────────────────────────────
def live_cfg(symbol: str) -> dict:
    doc = json.loads(CFG_PATH.read_text(encoding="utf-8"))
    for s in doc.get("symbols", []):
        if s.get("symbol") == symbol:
            return s
    raise SystemExit(f"pattern_trade.json 里没有 {symbol}")


def live_rules(c: dict) -> ExitRules:
    """线上那套出场规则。exit_mode=single 时 tp2/tp3 为 null，不启用。"""
    return ExitRules(
        enabled=True,
        tp1_pct=float(c["tp1_pct"]),
        tp1_ratio=float(c["tp1_ratio"]),
        move_sl_to_entry=bool(c.get("move_sl_to_entry", True)),
        sl_mode=c.get("sl_mode", "pct"),
        sl_pct=float(c.get("sl_pct") or 2.0),
        trail_with_st=bool(c.get("trail_with_st", True)),
        reverse_close=bool(c.get("reverse_close", False)),
    )


# ──────────────────────────────────────────────────────────────
def fmt_ts(ts):
    return dt.datetime.fromtimestamp(ts / 1000, dt.UTC).strftime("%Y-%m-%d %H:%M")


def buy_hold(candles):
    cl = np.asarray([c["c"] for c in candles], float)
    eq = cl / cl[0]
    pk = np.maximum.accumulate(eq)
    bars_per_year = 365 * 24 / 4              # 4h
    years = len(cl) / bars_per_year
    return {"total": float((eq[-1] - 1) * 100),
            "cagr": float(((eq[-1]) ** (1 / years) - 1) * 100),
            "dd": float(np.min(eq / pk - 1)) * 100, "years": years}


def main(argv=None):
    ap = argparse.ArgumentParser(description="线上43参数 · ETH 分年回测")
    ap.add_argument("--symbol", default="ETH-USDT-SWAP")
    ap.add_argument("--years", default="2025,2026")
    ap.add_argument("--fee", type=float, default=0.0005)
    a = ap.parse_args(argv)

    sym = a.symbol
    cfg = live_cfg(sym)
    tf = (cfg.get("allow_tfs") or ["4h"])[0]
    cs = load_tf(sym, tf)
    if len(cs) < 200:
        raise SystemExit(f"数据不足：{sym} {tf} 只有 {len(cs)} 根")

    bh = buy_hold(cs)
    fee_pct = a.fee * 100
    margin = float(cfg.get("margin_usdt") or 50.0)
    lev = int(cfg.get("leverage") or 10)
    rules = live_rules(cfg)
    sizing = cfg.get("sizing_mode", "fixed")

    print("=" * 112)
    print(f"线上（43 版）参数分年回测 · {sym} · {tf}")
    print("=" * 112)
    print(f"  数据      ：{len(cs)} 根 {tf} · {fmt_ts(cs[0]['ts'])} → {fmt_ts(cs[-1]['ts'])}"
          f"（≈ {bh['years']:.2f} 年）")
    print(f"  信号      ：原始 SuperTrend({ST_PERIODS}, {ST_MULTIPLIER})，翻向即开仓，反向反手")
    print(f"  出场      ：TP1 {cfg['tp1_pct']}% 平 {cfg['tp1_ratio']:.0f}% → 止损抬保本 → "
          f"剩余 {100 - cfg['tp1_ratio']:.0f}% 跟超趋线")
    print(f"  止损      ：sl_mode={cfg.get('sl_mode')} · {cfg.get('sl_pct')}% · "
          f"exit_mode={cfg.get('exit_mode')} · filter_v3={cfg.get('filter_v3')}")
    print(f"  仓位      ：sizing={sizing} · {margin:.0f}U × {lev}x = "
          f"{margin * lev:.0f}U 名义/笔 · 无复利")
    print(f"  费率      ：{fee_pct:.2f}%/边（往返 {2 * fee_pct:.2f}%）")
    print(f"  买入持有  ：总收益 {bh['total']:+.1f}% · 年化 {bh['cagr']:.1f}% · 最大回撤 {bh['dd']:.1f}%")

    p = {"periods": ST_PERIODS, "multiplier": ST_MULTIPLIER, "src": "hl2", "change_atr": True}
    kw = dict(init_cash=10000.0, fee_rate=a.fee, exit_rules=rules, full_trades=True,
              allow_short=True)
    if sizing == "fixed":
        kw.update(sizing="fixed", margin_usdt=margin, leverage=lev)
    else:
        kw.update(sizing="equity")

    r = run_backtest(cs, p, **kw)
    if r.get("error"):
        raise SystemExit(f"回测失败：{r['error']}")

    trades = r["trades_list"]
    years = [y.strip() for y in a.years.split(",") if y.strip()]
    g = defaultdict(list)
    for t in trades:
        g[dt.datetime.fromtimestamp(t["entry_ts"] / 1000, dt.UTC).strftime("%Y")].append(t)

    print("\n" + "=" * 112)
    print("分年结果（按开仓年归属）")
    print("=" * 112)
    print(f"  {'年份':<7}{'笔数':>6}{'胜率%':>8}{'毛期望%':>10}{'净期望%':>10}{'净USDT':>11}"
          f"{'均持仓根':>10}{'笔/月':>8}")
    for y in years:
        ts = g.get(y, [])
        if not ts:
            print(f"  {y:<7}{'0':>6}{'—':>8}{'—':>10}{'—':>10}{'—':>11}{'—':>10}{'—':>8}")
            continue
        n = len(ts)
        gross = float(np.mean([t["pnl_pct"] for t in ts]))
        net = gross - 2 * fee_pct
        win = float(np.mean([1.0 if t["pnl"] > 0 else 0.0 for t in ts])) * 100
        usd = float(np.sum([t["pnl"] for t in ts]))
        bars = float(np.mean([t["bars"] for t in ts]))
        span = (ts[-1]["entry_ts"] - ts[0]["entry_ts"]) / 1000 / 86400 / 30.44
        print(f"  {y:<7}{n:>6}{win:>8.1f}{gross:>10.3f}{net:>10.3f}{usd:>11.2f}"
              f"{bars:>10.1f}{n / max(span, 0.5):>8.2f}")

    # 该年买入持有（同年区间）
    cl = np.asarray([c["c"] for c in cs], float)
    tss = np.asarray([c["ts"] for c in cs], float)
    print(f"\n  同年买入持有基准（按该年首尾收盘价）：")
    for y in years:
        m = (tss >= dt.datetime(int(y), 1, 1, tzinfo=dt.UTC).timestamp() * 1000) & \
            (tss < dt.datetime(int(y) + 1, 1, 1, tzinfo=dt.UTC).timestamp() * 1000)
        if m.sum() > 1:
            r_bh = (cl[m][-1] / cl[m][0] - 1) * 100
            print(f"    {y}：{r_bh:+.1f}%")

    # 离场原因分布
    print(f"\n  离场原因分布：")
    for y in years:
        ts = g.get(y, [])
        if not ts:
            continue
        c = Counter(t["reason"] for t in ts)
        print(f"    {y}：" + "  ".join(f"{k}×{v}" for k, v in c.most_common()))

    # 整段汇总
    print("\n" + "=" * 112)
    print("整段汇总（同一套参数、同一段数据，用于对照）")
    print("=" * 112)
    print(f"  总收益 {r['return_pct']:+.2f}% · 回撤 {r['max_dd_pct']:.1f}% · "
          f"笔数 {r['trades']} · 胜率 {r['win_rate']:.1f}% · 均持仓 {r['avg_bars']:.1f} 根")
    print(f"  TP1 触发 {r['tp1_count']} · 止损 {r['stop_count']} · 反向平 {r['reverse_count']} · "
          f"保本止损见上方分布")
    print(f"  vs 买入持有（同期 {bh['total']:+.1f}%）：{r['return_pct'] - bh['total']:+.1f} pp")

    # 逐年净 USDT 累计，看钱是怎么来的
    print(f"\n  分年净 USDT 累计（fixed 口径，无复利）：")
    tot = 0.0
    for y in sorted(g):
        u = float(np.sum([t["pnl"] for t in g[y]]))
        tot += u
        print(f"    {y}：{u:>+10.2f} U   累计 {tot:>+10.2f} U")
    print("=" * 112)
    print("注：2026 数据止于 9-30，是不完整年；笔/月按该年实际有交易的月份跨度折算。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
