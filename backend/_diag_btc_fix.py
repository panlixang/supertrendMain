# -*- coding: utf-8 -*-
"""BTC 改进方向验证：修盈亏比(放大止盈/收紧止损/延后保本) + 调 ST 倍数(轨道更近)。"""
import os
import sys
import json
import dataclasses

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "backtest"))

from backtest_engine import run_backtest
from regime import TradeConfig
from candle_store import load_candles
try:
    from position_enhanced import EnhancedExitRules as _ER
except Exception:
    from position import ExitRules as _ER

cfgd = json.load(open(os.path.join(HERE, "backtest/btc/btc_43live.json"), encoding="utf-8"))


def load_tf(sym, tf):
    return [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol}
            for c in load_candles(sym, tf, 10_000_000)]


def agg(c1h, step):
    out = []
    for i in range(0, len(c1h), step):
        g = c1h[i:i + step]
        if g:
            out.append({"ts": g[0]["ts"], "o": g[0]["o"], "h": max(x["h"] for x in g),
                        "l": min(x["l"] for x in g), "c": g[-1]["c"],
                        "vol": sum(x["vol"] for x in g)})
    return out


c1h = load_tf("BTC-USDT", "1h")
c4h = load_tf("BTC-USDT", "4h") or agg(c1h, 4)
c1d = load_tf("BTC-USDT", "1d") or agg(c1h, 24)
cbtf = {"1h": c1h, "4h": c4h, "1d": c1d}
p0 = cfgd["params"]
er0 = _ER(**{k: cfgd["exit_rules"][k] for k in _ER.__dataclass_fields__ if k in cfgd["exit_rules"]})
erq = _ER(**{k: cfgd["exit_rules_quick"][k] for k in _ER.__dataclass_fields__ if k in cfgd["exit_rules_quick"]})
tc = TradeConfig(**{k: cfgd[k] for k in TradeConfig.__dataclass_fields__ if k in cfgd})


def bt(params, er_rules):
    return run_backtest(
        candles=c1h, p=params, init_cash=10000, fee_rate=0.0005, allow_short=True,
        live_gate=tc, gate_tf="1h", candles_by_tf=cbtf,
        exit_rules=er_rules, exit_rules_quick=erq,
        margin_usdt=cfgd["margin_usdt"], leverage=cfgd["leverage"],
        sizing="fixed", gate_fn=None,
        er_min=cfgd.get("er_min"), er_weak_min=cfgd.get("er_weak_min"),
        range_filter_enabled=cfgd.get("range_filter_enabled", False),
        atr_filter_enabled=cfgd.get("atr_filter_enabled", False),
        adx_filter_enabled=cfgd.get("adx_filter_enabled", False))


CASES = [
    ("基准(43线上)      ", p0, er0),
    # A 修盈亏比：放大止盈，让盈利单跑起来
    ("A 放大止盈tp1.5/3/6", p0, dataclasses.replace(er0, tp1_pct=1.5, tp2_pct=3.0, tp3_pct=6.0)),
    # B 收紧止损 + 延后保本(不过早move_sl_to_entry)
    ("B 止损2%+不保本    ", p0, dataclasses.replace(er0, sl_pct=2.0, move_sl_to_entry=False)),
    # C 调 ST 倍数(轨道更近→止损更近、信号更灵)
    ("C 倍数4.0→3.0      ", {**p0, "multiplier": 3.0}, er0),
    ("C 倍数4.0→2.5      ", {**p0, "multiplier": 2.5}, er0),
    # D 组合：倍数2.5 + 放大止盈 + 收紧止损
    ("D 组合(2.5+tp放大+sl2)", {**p0, "multiplier": 2.5},
     dataclasses.replace(er0, tp1_pct=1.5, tp2_pct=3.0, tp3_pct=6.0, sl_pct=2.0, move_sl_to_entry=False)),
]

print(f"{'方案':<24}{'收益%':>8}{'笔数':>6}{'胜率%':>7}{'盈亏比':>7}{'回撤%':>7}{'均盈U':>7}{'均亏U':>7}")
print("-" * 78)
for name, pp, ee in CASES:
    r = bt(pp, ee)
    print(f"{name:<24}{r['return_pct']:>8.2f}{r['trades']:>6}{r['win_rate']:>7.1f}"
          f"{r['profit_factor']:>7.2f}{r['max_dd_pct']:>7.2f}{r['avg_win']:>7.2f}{r['avg_loss']:>7.2f}")
