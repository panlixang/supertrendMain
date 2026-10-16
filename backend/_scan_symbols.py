# -*- coding: utf-8 -*-
"""多品种验证：单因子 vs 双因子(补位A) vs 双因子(否决方向1)。
品种：SNDK/MU/SKHYNIX/CL，本地 OKX 1h 数据 + 各自线上opt配置。
目的：确认「双因子(趋势+动量)无效」是 SNDK 个案还是普遍结论。
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

MOM_SCALE = 0.04


def make_dual_gate(THETA, MOM_LOOKBACK=12, W_TREND=0.5, W_MOM=0.5):
    def gate(full, candles, live_gate, candles_by_tf, p, typ=None, **kw):
        base = enhanced_signal_handler(
            full, candles, live_gate, candles_by_tf=candles_by_tf, p=p,
            use_momentum=True, use_false_filter=True, use_adaptive=True)
        ts = base.get("total_score") or 0
        trend_norm = (ts - 50) / 50.0
        if len(candles) > MOM_LOOKBACK:
            mom = candles[-1]["c"] / candles[-MOM_LOOKBACK]["c"] - 1.0
        else:
            mom = 0.0
        mom_norm = math.tanh(mom / MOM_SCALE)
        composite = W_TREND * trend_norm + W_MOM * mom_norm
        if base.get("trade") and not base.get("hidden"):
            return base
        reasons = list(base.get("reasons") or [])
        if typ == "sell" and composite < -THETA:
            base["trade"] = True
            base["hidden"] = False
            reasons.append(f"补位空(c={composite:.2f})")
            base["reasons"] = reasons
            return base
        if typ == "buy" and composite > THETA:
            base["trade"] = True
            base["hidden"] = False
            reasons.append(f"补位多(c={composite:.2f})")
            base["reasons"] = reasons
            return base
        return base
    return gate


def make_veto_gate(VETO, MOM_LOOKBACK=12):
    cnt = {"n": 0}

    def gate(full, candles, live_gate, candles_by_tf, p, typ=None, **kw):
        base = enhanced_signal_handler(
            full, candles, live_gate, candles_by_tf=candles_by_tf, p=p,
            use_momentum=True, use_false_filter=True, use_adaptive=True)
        if len(candles) > MOM_LOOKBACK:
            mom = candles[-1]["c"] / candles[-MOM_LOOKBACK]["c"] - 1.0
        else:
            mom = 0.0
        mom_norm = math.tanh(mom / MOM_SCALE)
        if base.get("trade") and not base.get("hidden"):
            if typ == "buy" and mom_norm < -VETO:
                base["trade"] = False
                base["hidden"] = True
                cnt["n"] += 1
                return base
            if typ == "sell" and mom_norm > VETO:
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
    rng = f"{__import__('datetime').datetime.utcfromtimestamp(c1h[0]['ts']/1000):%m-%d}~" \
          f"{__import__('datetime').datetime.utcfromtimestamp(c1h[-1]['ts']/1000):%m-%d}"
    print(f"\n########## {name} ({sym}) ST={p.get('periods')}x{p.get('multiplier')} "
          f"数据={len(c1h)}根1h [{rng}] ##########")
    print(f"  单因子       : ret={s0['ret']:6.2f}% n={s0['n']:3d} win={s0['win']:5.1f}% "
          f"dd={s0['dd']:4.2f}% pf={s0['pf']:4.2f} 多={s0['lo']} 空={s0['sh']}  ←基准")

    best = None
    for th in [0.3, 0.5]:
        s = stats(_run(make_dual_gate(th)))
        over = s["ret"] > s0["ret"]
        tag = "✓反超" if over else ""
        print(f"  双因子补位θ={th}: ret={s['ret']:6.2f}% n={s['n']:3d} win={s['win']:5.1f}% "
              f"dd={s['dd']:4.2f}% pf={s['pf']:4.2f} 多={s['lo']} 空={s['sh']} {tag}")
        if over and (best is None or s["ret"] > best[1]["ret"]):
            best = (f"补位θ={th}", s)
    for v in [0.5, 0.7]:
        g, cnt = make_veto_gate(v)
        s = stats(_run(g))
        over = s["ret"] > s0["ret"]
        tag = "✓反超" if over else ""
        print(f"  双因子否决V={v}: ret={s['ret']:6.2f}% n={s['n']:3d} win={s['win']:5.1f}% "
              f"dd={s['dd']:4.2f}% pf={s['pf']:4.2f} 多={s['lo']} 空={s['sh']} 否={cnt['n']} {tag}")
        if over and (best is None or s["ret"] > best[1]["ret"]):
            best = (f"否决V={v}", s)

    if best:
        print(f"  >>> {name}: 双因子最优 [{best[0]}] ret={best[1]['ret']:.2f}% (单因子 {s0['ret']:.2f}%)")
    else:
        print(f"  >>> {name}: 所有双因子配置均≤单因子（动量因子无增益）")
