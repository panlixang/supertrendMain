# -*- coding: utf-8 -*-
"""探测 BTC 回测的 res / trades_list 字段结构，为复盘做准备。"""
import os
import sys
import json

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

p = cfgd["params"]
er = _ER(**{k: cfgd["exit_rules"][k] for k in _ER.__dataclass_fields__ if k in cfgd["exit_rules"]})
erq = _ER(**{k: cfgd["exit_rules_quick"][k] for k in _ER.__dataclass_fields__ if k in cfgd["exit_rules_quick"]})
tc = TradeConfig(**{k: cfgd[k] for k in TradeConfig.__dataclass_fields__ if k in cfgd})

res = run_backtest(
    candles=c1h, p=p, init_cash=10000, fee_rate=0.0005, allow_short=True,
    live_gate=tc, gate_tf="1h", candles_by_tf=cbtf, exit_rules=er, exit_rules_quick=erq,
    margin_usdt=cfgd["margin_usdt"], leverage=cfgd["leverage"], sizing="fixed", gate_fn=None,
    er_min=cfgd.get("er_min"), er_weak_min=cfgd.get("er_weak_min"),
    range_filter_enabled=cfgd.get("range_filter_enabled", False),
    atr_filter_enabled=cfgd.get("atr_filter_enabled", False),
    adx_filter_enabled=cfgd.get("adx_filter_enabled", False))

print("res keys:", list(res.keys()))
tl = res.get("trades_list") or []
print("trades_list len:", len(tl))
if tl:
    print("\ntrade[0] keys:", list(tl[0].keys()))
    print("\ntrade[0]:", json.dumps(tl[0], ensure_ascii=False, default=str))
    print("\ntrade[1]:", json.dumps(tl[1], ensure_ascii=False, default=str))
