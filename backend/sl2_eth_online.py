# -*- coding: utf-8 -*-
"""
ETH 分年回测（2025 / 2026）· 用【线上 43 服务真实参数】
======================================================
参数来源：backend/_live43_eth.json（已从上线的 43 服务 43.108.10.84:5174
GET /api/trade/symbols 拉取并落盘，ETH-USDT-SWAP）。周期 = 1h（用户指定）。

真实线上参数（ETH-USDT-SWAP）：
    ST : periods=15, multiplier=2.0, src=hl2, change_atr=True
    exit_rules: tp1 1.2/30, tp2 2.5/40, tp3 3.5/100(reverse_signal),
                sl_mode="st", sl_pct=2.5, sl_buffer_atr=0.5, sl_min_pct=1.2,
                move_sl_to_entry, trail_with_st, protect_profit_at=1.5, protect_trail_pct=0.8
    exit_rules_quick: enabled, tp1 1.3/100, sl st 1.5
    margin_usdt=60, leverage=10, sizing=fixed, allow_tfs=["1h"]
    filter_v3=true, use_scoring=true（评分闸门）, ER 闸门 er_min=0.1 等

口径：生产出场状态机（run_backtest → position_enhanced.EnhancedExitRules），
与线上同源；费率 0.05%/边；按开仓年拆解。
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
from regime import TradeConfig

DB_PATH = Path("candle_data.db")
CFG_PATH = Path("_live43_eth.json")
TF_HOURS = {"1h": 1, "2h": 2, "4h": 4, "8h": 8, "12h": 12, "1d": 24}
FEE_RATE = 0.0005


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
        groups.setdefault(k, []).append(c)
    out = []
    for k in order:
        g = groups[k]
        if len(g) < hours:
            continue
        out.append({"ts": k * step, "o": g[0]["o"],
                    "h": max(x["h"] for x in g), "l": min(x["l"] for x in g),
                    "c": g[-1]["c"], "vol": sum(x.get("vol") or 0 for x in g)})
    return out


def load_tf(symbol: str, tf: str = "1h"):
    h = TF_HOURS.get(tf)
    if h is None:
        raise ValueError(f"不支持的周期 {tf}")
    db_sym = symbol.replace("-SWAP", "")
    c1h = load_db(db_sym, "1h")
    if not c1h:
        return load_db(db_sym, tf)
    if h == 1:
        return c1h
    return resample(c1h, h)


def fmt_ts(ts):
    return dt.datetime.fromtimestamp(ts / 1000, dt.UTC).strftime("%Y-%m-%d %H:%M")


def main(argv=None):
    ap = argparse.ArgumentParser(description="线上43真实参数 · ETH 1h 分年回测")
    ap.add_argument("--symbol", default="ETH-USDT-SWAP")
    ap.add_argument("--years", default="2025,2026")
    ap.add_argument("--raw", action="store_true",
                    help="关闭打分闸门(live_gate)与 v3 过滤，只用原始 ST 翻向 + 三档出场")
    ap.add_argument("--v3", action="store_true",
                    help="显式开启 V3 趋势过滤(signal_v3)。V3 是【形态识别页】专用过滤，"
                         "通用/新模型回测默认不走，也不再从品种配置的 filter_v3 继承")
    a = ap.parse_args(argv)

    cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))
    sym = a.symbol
    tf = (cfg.get("allow_tfs") or ["1h"])[0]

    cs = load_tf(sym, tf)
    if len(cs) < 200:
        raise SystemExit(f"数据不足：{sym} {tf} 只有 {len(cs)} 根")

    # ── 信号 ──
    p = {"periods": int(cfg["params"]["periods"]),
         "multiplier": float(cfg["params"]["multiplier"]),
         "src": cfg["params"].get("src", "hl2"),
         "change_atr": bool(cfg["params"].get("change_atr", True))}

    # ── 出场（增强三档）──
    from position_enhanced import EnhancedExitRules
    er = cfg["exit_rules"]
    erq = cfg.get("exit_rules_quick") or {}
    enh = EnhancedExitRules(**{k: er[k] for k in (
        "enabled", "tp1_pct", "tp1_ratio", "tp2_pct", "tp2_ratio",
        "tp3_pct", "tp3_ratio", "tp3_mode", "sl_mode", "sl_pct",
        "sl_buffer_atr", "sl_min_pct", "move_sl_to_entry", "trail_with_st",
        "reverse_close", "protect_profit_at", "protect_trail_pct",
        "max_loss_enabled", "max_loss_pct") if k in er})
    enh_q = EnhancedExitRules(**{k: erq[k] for k in (
        "enabled", "tp1_pct", "tp1_ratio", "tp2_pct", "tp2_ratio",
        "tp3_pct", "tp3_ratio", "tp3_mode", "sl_mode", "sl_pct",
        "sl_buffer_atr", "sl_min_pct", "move_sl_to_entry", "trail_with_st",
        "reverse_close", "protect_profit_at", "protect_trail_pct",
        "max_loss_enabled", "max_loss_pct") if k in erq}) if erq else None

    # ── 评分闸门（live_gate）──
    gate = TradeConfig(
        er_hide_below=cfg.get("er_hide_below", 0.10),
        er_weak_min=cfg.get("er_weak_min", 0.12),
        er_min=cfg.get("er_min", 0.15),
        er_trend=cfg.get("er_trend", 0.30),
        allow_grades=list(cfg.get("allow_grades", ["A", "B"])),
        min_score=int(cfg.get("min_score", 2)),
        cooldown_sec=int(cfg.get("cooldown_sec", 300)),
        use_scoring=bool(cfg.get("use_scoring", True)),
        scoring_full_threshold=float(cfg.get("scoring_full_threshold", 80.0)),
        scoring_half_threshold=float(cfg.get("scoring_half_threshold", 60.0)),
        scoring_alert_threshold=float(cfg.get("scoring_alert_threshold", 40.0)),
        use_dynamic_threshold=bool(cfg.get("use_dynamic_threshold", True)),
        quick_enabled=bool(cfg.get("quick_enabled", False)),
    )

    margin = float(cfg.get("margin_usdt") or 10.0)
    lev = int(cfg.get("leverage") or 3)
    sizing = cfg.get("sizing_mode", "fixed")
    fee_pct = FEE_RATE * 100

    cs4h = resample(cs, 4)
    candles_by_tf = {"1h": cs, "4h": cs4h}

    print("=" * 104)
    print(f"线上 43 真实参数 · ETH 1h 分年回测 · {sym}")
    print("=" * 104)
    print(f"  数据    ：{len(cs)} 根 1h · {fmt_ts(cs[0]['ts'])} → {fmt_ts(cs[-1]['ts'])}")
    print(f"  信号    ：SuperTrend({p['periods']}, {p['multiplier']}) src={p['src']} change_atr={p['change_atr']}")
    print(f"  出场    ：Enhanced 三档 tp1 {er['tp1_pct']}/{er['tp1_ratio']} · tp2 {er['tp2_pct']}/{er['tp2_ratio']} · "
          f"tp3 {er['tp3_pct']}/{er['tp3_ratio']}({er.get('tp3_mode')}) · sl_mode={er['sl_mode']} sl_pct={er['sl_pct']}")
    print(f"  仓位    ：sizing={sizing} · {margin:.0f}U × {lev}x = {margin*lev:.0f}U 名义/笔 · 无复利")
    if a.raw:
        live_gate_arg, v3_arg = None, False
        print(f"  闸门    ：【RAW 模式】已关闭 live_gate 与 v3_filter → 仅原始 ST(15,2.0) 翻向 + Enhanced 三档出场")
    else:
        # V3 属形态识别页专用过滤：不继承品种配置的 filter_v3，只认独立开关 --v3
        live_gate_arg, v3_arg = gate, bool(getattr(a, "v3", False))
        print(f"  闸门    ：v3_filter={v3_arg}（独立开关 --v3；形态页 filter_v3={cfg.get('filter_v3')} 不继承） · use_scoring={cfg.get('use_scoring')} · "
              f"er_min={gate.er_min} · 评分 {gate.scoring_full_threshold:.0f}/{gate.scoring_half_threshold:.0f}/{gate.scoring_alert_threshold:.0f}")
    print(f"  费率    ：{fee_pct:.2f}%/边（往返 {2*fee_pct:.2f}%）")

    r = run_backtest(cs, p, init_cash=10000.0, fee_rate=FEE_RATE,
                     exit_rules=enh, exit_rules_quick=enh_q, full_trades=True,
                     allow_short=True, enhanced_stop=True,
                     v3_filter=v3_arg,
                     live_gate=live_gate_arg, gate_tf="1h", candles_by_tf=candles_by_tf,
                     sizing="fixed", margin_usdt=margin, leverage=lev)
    if r.get("error"):
        raise SystemExit(f"回测失败：{r['error']}")

    trades = r["trades_list"]
    years = [y.strip() for y in a.years.split(",") if y.strip()]
    g = defaultdict(list)
    for t in trades:
        g[dt.datetime.fromtimestamp(t["entry_ts"] / 1000, dt.UTC).strftime("%Y")].append(t)

    print("\n" + "=" * 104)
    print("分年结果（按开仓年归属）")
    print("=" * 104)
    print(f"  {'年份':<7}{'笔数':>6}{'胜率%':>8}{'毛期望%':>10}{'净期望%':>10}{'净USDT':>11}{'均持仓根':>10}{'笔/月':>8}")
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
        print(f"  {y:<7}{n:>6}{win:>8.1f}{gross:>10.3f}{net:>10.3f}{usd:>11.2f}{bars:>10.1f}{n/max(span,0.5):>8.2f}")

    print("\n  离场原因分布：")
    for y in years:
        ts = g.get(y, [])
        if not ts:
            continue
        c = Counter(t["reason"] for t in ts)
        print(f"    {y}：" + "  ".join(f"{k}×{v}" for k, v in c.most_common()))

    print("\n" + "=" * 104)
    print(f"整段汇总：总收益 {r['return_pct']:+.2f}% · 回撤 {r['max_dd_pct']:.1f}% · "
          f"笔数 {r['trades']} · 胜率 {r['win_rate']:.1f}% · 均持仓 {r['avg_bars']:.1f} 根")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    sys.exit(main())
