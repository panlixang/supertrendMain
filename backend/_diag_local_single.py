# -*- coding: utf-8 -*-
"""诊断：本地 OKX 数据下，config vs opt 配置的单因子笔数对比（隔离变量）。"""
import os, sys, json
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

SYM = "SNDK-USDT"


def load_tf(tf):
    return [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol}
            for c in load_candles(SYM, tf, 10_000_000)]


def agg(c1h, step=24):
    out = []
    for i in range(0, len(c1h), step):
        g = c1h[i:i + step]
        if g:
            out.append({"ts": g[0]["ts"], "o": g[0]["o"], "h": max(x["h"] for x in g),
                        "l": min(x["l"] for x in g), "c": g[-1]["c"],
                        "vol": sum(x["vol"] for x in g)})
    return out


c1h = load_tf("1h")
c4h = load_tf("4h")
c1d = agg(c1h)
cbtf = {"1h": c1h, "4h": c4h, "1d": c1d}
print(f"本地数据: 1h={len(c1h)} 末={__import__('datetime').datetime.utcfromtimestamp(c1h[-1]['ts']/1000):%Y-%m-%d}")


def run_single(cfgd):
    tc = TradeConfig(**{k: cfgd[k] for k in TradeConfig.__dataclass_fields__ if k in cfgd})
    p = cfgd["params"]
    er = _ER(**{k: cfgd["exit_rules"][k] for k in _ER.__dataclass_fields__ if k in cfgd["exit_rules"]})
    erq = _ER(**{k: cfgd["exit_rules_quick"][k] for k in _ER.__dataclass_fields__ if k in cfgd["exit_rules_quick"]})
    res = run_backtest(candles=c1h, p=p, init_cash=10000, fee_rate=0.0005,
                       allow_short=True, live_gate=tc, gate_tf="1h", candles_by_tf=cbtf,
                       exit_rules=er, exit_rules_quick=erq, margin_usdt=50, leverage=10,
                       sizing="fixed", er_min=cfgd.get("er_min"),
                       er_weak_min=cfgd.get("er_weak_min"))
    return res


for name in ["sndk_config.json", "sndk_opt_config.json"]:
    cfgd = json.load(open(os.path.join(HERE, "backtest/SNDK", name), encoding="utf-8"))
    res = run_single(cfgd)
    # 统计空/多
    tl = res.get("trades_list") or []
    shorts = [t for t in tl if str(t.get("side")) == "short"]
    longs = [t for t in tl if str(t.get("side")) == "long"]
    print(f"[{name}] trades={res['trades']} ret%={res['return_pct']} "
          f"win%={res['win_rate']} 多={len(longs)} 空={len(shorts)}")
