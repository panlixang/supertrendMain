# -*- coding: utf-8 -*-
"""方向1：动量作否决器（非补位）。SuperTrend 想开仓但动量极强反向→否决。
扫 VETO 阈值，看能否滤掉单因子的亏损交易、反超 3.38%。
SNDK 本地 OKX 1h + 线上opt配置。
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


def make_veto_gate(VETO, MOM_LOOKBACK=12, MOM_SCALE=0.04):
    cnt = {"n": 0, "buy": 0, "sell": 0}

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
            # SuperTrend 想开多但动量极强负 → 否决（避免追高被砸）
            if typ == "buy" and mom_norm < -VETO:
                base["trade"] = False
                base["hidden"] = True
                cnt["n"] += 1
                cnt["buy"] += 1
                reasons = list(base.get("reasons") or [])
                reasons.append(f"动量否决开多(mom={mom_norm:.2f})")
                base["reasons"] = reasons
                return base
            # SuperTrend 想开空但动量极强正 → 否决
            if typ == "sell" and mom_norm > VETO:
                base["trade"] = False
                base["hidden"] = True
                cnt["n"] += 1
                cnt["sell"] += 1
                reasons = list(base.get("reasons") or [])
                reasons.append(f"动量否决开空(mom={mom_norm:.2f})")
                base["reasons"] = reasons
                return base
            return base
        return base

    return gate, cnt


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
cfgd = json.load(open(os.path.join(HERE, "backtest/SNDK/sndk_opt_config.json"), encoding="utf-8"))
tc = TradeConfig(**{k: cfgd[k] for k in TradeConfig.__dataclass_fields__ if k in cfgd})
p = cfgd["params"]
er = _ER(**{k: cfgd["exit_rules"][k] for k in _ER.__dataclass_fields__ if k in cfgd["exit_rules"]})
erq = _ER(**{k: cfgd["exit_rules_quick"][k] for k in _ER.__dataclass_fields__ if k in cfgd["exit_rules_quick"]})


def _run(gate_fn):
    return run_backtest(
        candles=c1h, p=p, init_cash=10000, fee_rate=0.0005, allow_short=True,
        live_gate=tc, gate_tf="1h", candles_by_tf=cbtf, exit_rules=er, exit_rules_quick=erq,
        margin_usdt=50, leverage=10, sizing="fixed", gate_fn=gate_fn,
        er_min=cfgd.get("er_min"), er_weak_min=cfgd.get("er_weak_min"),
        range_filter_enabled=cfgd.get("range_filter_enabled", False),
        atr_filter_enabled=cfgd.get("atr_filter_enabled", False),
        adx_filter_enabled=cfgd.get("adx_filter_enabled", False))


def stats(res):
    tl = res.get("trades_list") or []
    shorts = [t for t in tl if str(t.get("side")) == "short"]
    longs = [t for t in tl if str(t.get("side")) == "long"]
    return dict(ret=res["return_pct"], n=res["trades"], win=res["win_rate"],
                dd=res["max_dd_pct"], pf=res["profit_factor"],
                lo=len(longs), sh=len(shorts))


# 单因子基线（无否决）
s0 = stats(_run(None))
print(f"单因子基线: 收益={s0['ret']}% 笔数={s0['n']} 胜率={s0['win']}% "
      f"回撤={s0['dd']}% 盈亏比={s0['pf']} 多={s0['lo']} 空={s0['sh']}")
print(f"\n{'VETO':>5} {'收益%':>7} {'笔数':>5} {'胜率%':>6} {'回撤%':>6} {'盈亏比':>6} "
      f"{'多':>4} {'空':>4} {'否决':>4} {'反超?':>5}")
best = None
for v in [0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]:
    g, cnt = make_veto_gate(v)
    s = stats(_run(g))
    over = s["ret"] > s0["ret"]
    if over and (best is None or s["ret"] > best[1]["ret"]):
        best = (v, s)
    print(f"{v:5.2f} {s['ret']:7.2f} {s['n']:5d} {s['win']:6.1f} {s['dd']:6.2f} "
          f"{s['pf']:6.2f} {s['lo']:4d} {s['sh']:4d} {cnt['n']:4d} {'✓' if over else ''}")

if best:
    v, s = best
    print(f"\n>>> 方向1最优 VETO={v:.2f}: 收益={s['ret']}% 胜率={s['win']}% 回撤={s['dd']}% "
          f"盈亏比={s['pf']} 笔数={s['n']} (基准 {s0['ret']}%)")
else:
    print("\n>>> 所有 VETO 下双因子(否决)均≤单因子：动量否决也无增益。")
