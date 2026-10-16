# -*- coding: utf-8 -*-
"""方向E(proxy版)：把动量因子换成「量价配合因子(flow)」作为订单流/持仓量的代理。
flow = tanh(vol_z/SCALE) * sign(price_chg)  ∈[-1,1]
  - 放量+涨价→强多(+)，缩量+跌→强空(-)，量价背离→弱
本地无 OI/订单流数据，OKX API 当前环境403不可达，故用 vol 代理。
目的：验证「引入量的低相关维度」能否让双因子反超单因子(动量已证全无效)。
"""
import os
import sys
import json
import math

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "backtest"))

from backtest_engine import run_backtest
from regime import TradeConfig
from integration import enhanced_signal_handler
from candle_store import load_candles

try:
    from position_enhanced import EnhancedExitRules as _ER
except Exception:
    from position import ExitRules as _ER

FLOW_LOOKBACK = 24
FLOW_SCALE = 2.0


def flow_factor(candles):
    if len(candles) <= FLOW_LOOKBACK:
        return 0.0
    vs = [c["vol"] for c in candles[-FLOW_LOOKBACK:]]
    m = sum(vs) / len(vs)
    s = (sum((x - m) ** 2 for x in vs) / len(vs)) ** 0.5
    if s <= 1e-9:
        return 0.0
    vz = (candles[-1]["vol"] - m) / s
    pc = candles[-1]["c"] / candles[-FLOW_LOOKBACK]["c"] - 1.0
    sign = 1.0 if pc >= 0 else -1.0
    return math.tanh(vz / FLOW_SCALE) * sign


def make_flow_gate(THETA, W_TREND=0.5, W_FLOW=0.5):
    def gate(full, candles, live_gate, candles_by_tf, p, typ=None, **kw):
        base = enhanced_signal_handler(
            full, candles, live_gate, candles_by_tf=candles_by_tf, p=p,
            use_momentum=True, use_false_filter=True, use_adaptive=True)
        ts = base.get("total_score") or 0
        trend_norm = (ts - 50) / 50.0
        flow = flow_factor(candles)
        composite = W_TREND * trend_norm + W_FLOW * flow
        if base.get("trade") and not base.get("hidden"):
            return base
        reasons = list(base.get("reasons") or [])
        if typ == "sell" and composite < -THETA:
            base["trade"] = True
            base["hidden"] = False
            reasons.append(f"量能补位空(c={composite:.2f},f={flow:.2f})")
            base["reasons"] = reasons
            return base
        if typ == "buy" and composite > THETA:
            base["trade"] = True
            base["hidden"] = False
            reasons.append(f"量能补位多(c={composite:.2f},f={flow:.2f})")
            base["reasons"] = reasons
            return base
        return base
    return gate


def make_flow_veto(VETO):
    cnt = {"n": 0}

    def gate(full, candles, live_gate, candles_by_tf, p, typ=None, **kw):
        base = enhanced_signal_handler(
            full, candles, live_gate, candles_by_tf=candles_by_tf, p=p,
            use_momentum=True, use_false_filter=True, use_adaptive=True)
        flow = flow_factor(candles)
        if base.get("trade") and not base.get("hidden"):
            # 趋势开多但量能因子极强负(缩量涨/放量跌)→否决；开空但量能极强正→否决
            if typ == "buy" and flow < -VETO:
                base["trade"] = False
                base["hidden"] = True
                cnt["n"] += 1
                return base
            if typ == "sell" and flow > VETO:
                base["trade"] = False
                base["hidden"] = True
                cnt["n"] += 1
                return base
            return base
        return base
    return gate, cnt


def load_tf(SYM, tf):
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


def stats(res):
    tl = res.get("trades_list") or []
    shorts = [t for t in tl if str(t.get("side")) == "short"]
    longs = [t for t in tl if str(t.get("side")) == "long"]
    return dict(ret=res["return_pct"], n=res["trades"], win=res["win_rate"],
                dd=res["max_dd_pct"], pf=res["profit_factor"],
                lo=len(longs), sh=len(shorts))


SYMS = {
    "SNDK":    ("SNDK-USDT",    "backtest/SNDK/sndk_opt_config.json"),
    "MU":      ("MU-USDT",      "backtest/MU/mu_live_config_opt_config.json"),
    "SKHYNIX": ("SKHYNIX-USDT", "backtest/SKHYNIX/skhynix_live_config_opt_config.json"),
    "CL":      ("CL-USDT",      "backtest/CL/cl_config_opt_config.json"),
}

for name, (sym, cfgpath) in SYMS.items():
 try:
    c1h = load_tf(sym, "1h")
    c4h = load_tf(sym, "4h")
    c1d = agg(c1h)
    cbtf = {"1h": c1h, "4h": c4h, "1d": c1d}
    cfgd = json.load(open(os.path.join(HERE, cfgpath), encoding="utf-8"))
    tc = TradeConfig(**{k: cfgd[k] for k in TradeConfig.__dataclass_fields__ if k in cfgd})
    p = cfgd["params"]
    er = _ER(**{k: cfgd["exit_rules"][k] for k in _ER.__dataclass_fields__ if k in cfgd["exit_rules"]})
    erq = _ER(**{k: cfgd["exit_rules_quick"][k] for k in _ER.__dataclass_fields__ if k in cfgd["exit_rules_quick"]})
    margin = cfgd.get("margin_usdt", 50.0)
    lev = cfgd.get("leverage", 10)

    def _run(gate_fn):
        return run_backtest(
            candles=c1h, p=p, init_cash=10000, fee_rate=0.0005, allow_short=True,
            live_gate=tc, gate_tf="1h", candles_by_tf=cbtf, exit_rules=er, exit_rules_quick=erq,
            margin_usdt=margin, leverage=lev, sizing="fixed", gate_fn=gate_fn,
            er_min=cfgd.get("er_min"), er_weak_min=cfgd.get("er_weak_min"),
            range_filter_enabled=cfgd.get("range_filter_enabled", False),
            atr_filter_enabled=cfgd.get("atr_filter_enabled", False),
            adx_filter_enabled=cfgd.get("adx_filter_enabled", False))

    s0 = stats(_run(None))
    print(f"\n########## {name} ({sym}) ST={p.get('periods')}x{p.get('multiplier')} 数据={len(c1h)}根 ##########")
    print(f"  单因子        : ret={s0['ret']:6.2f}% n={s0['n']:3d} win={s0['win']:5.1f}% "
          f"dd={s0['dd']:4.2f}% pf={s0['pf']:4.2f} 多={s0['lo']} 空={s0['sh']}  ←基准")

    best = None
    for th in [0.3, 0.5]:
        s = stats(_run(make_flow_gate(th)))
        over = s["ret"] > s0["ret"]
        print(f"  flow补位θ={th}: ret={s['ret']:6.2f}% n={s['n']:3d} win={s['win']:5.1f}% "
              f"dd={s['dd']:4.2f}% pf={s['pf']:4.2f} 多={s['lo']} 空={s['sh']} {'[OK]' if over else ''}")
        if over and (best is None or s["ret"] > best[1]["ret"]):
            best = (f"flow补位θ={th}", s)
    for v in [0.5, 0.7]:
        g, cnt = make_flow_veto(v)
        s = stats(_run(g))
        over = s["ret"] > s0["ret"]
        print(f"  flow否决V={v}: ret={s['ret']:6.2f}% n={s['n']:3d} win={s['win']:5.1f}% "
              f"dd={s['dd']:4.2f}% pf={s['pf']:4.2f} 多={s['lo']} 空={s['sh']} 否={cnt['n']} {'[OK]' if over else ''}")
        if over and (best is None or s["ret"] > best[1]["ret"]):
            best = (f"flow否决V={v}", s)

    if best:
        print(f"  >>> {name}: flow双因子最优 [{best[0]}] ret={best[1]['ret']:.2f}% (单因子 {s0['ret']:.2f}%)")
    else:
        print(f"  >>> {name}: flow双因子仍≤单因子")
 except Exception as e:
    import traceback as _tb
    print(f"!!! {name} ERROR: {repr(e)}")
    _tb.print_exc()
