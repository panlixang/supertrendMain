# -*- coding: utf-8 -*-
"""扫 θ：双因子(趋势+动量) vs 单因子，SNDK 本地 OKX 1h + 线上opt配置。

目标：找让双因子反超单因子(3.38%)的最优 θ，并观察噪声空单如何随 θ 变化。
- 趋势权重0.5 / 动量权重0.5 / 动量窗口12根1h / MOM_SCALE=0.04 固定
- θ 从 0.10(松) 扫到 0.50(仅极端动量才补位)
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


def make_dual_gate(THETA, MOM_LOOKBACK=12, W_TREND=0.5, W_MOM=0.5,
                   require_trend_sign=False):
    """双因子 gate。

    require_trend_sign=False：初版，动量强就逆势补位（噪声多，已证失败）。
    require_trend_sign=True ：改进版，补位仅当趋势分已转向且同向
                              （趋势已翻空+被拦+动量确认才补空；过滤纯动量噪声）。
    """
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
            if require_trend_sign and not (trend_norm < 0):
                return base  # 趋势未转空，纯动量噪声，不补位
            base["trade"] = True
            base["hidden"] = False
            reasons.append(f"动量补位开空(c={composite:.2f},t={trend_norm:.2f})")
            base["reasons"] = reasons
            return base
        if typ == "buy" and composite > THETA:
            if require_trend_sign and not (trend_norm > 0):
                return base
            base["trade"] = True
            base["hidden"] = False
            reasons.append(f"动量补位开多(c={composite:.2f},t={trend_norm:.2f})")
            base["reasons"] = reasons
            return base
        return base
    return gate


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


s0 = stats(_run(None))
print(f"单因子基线: 收益={s0['ret']}% 笔数={s0['n']} 胜率={s0['win']}% "
      f"回撤={s0['dd']}% 盈亏比={s0['pf']} 多={s0['lo']} 空={s0['sh']}")
print(f"\n{'θ':>5} {'收益%':>7} {'笔数':>5} {'胜率%':>6} {'回撤%':>6} {'盈亏比':>6} "
      f"{'多':>4} {'空':>4} {'额外空':>6} {'反超?':>5}")
best = None
for th in [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]:
    s = stats(_run(make_dual_gate(th)))
    extra = s["sh"] - s0["sh"]
    over = s["ret"] > s0["ret"]
    if over and (best is None or s["ret"] > best[1]["ret"]):
        best = (th, s)
    print(f"{th:5.2f} {s['ret']:7.2f} {s['n']:5d} {s['win']:6.1f} {s['dd']:6.2f} "
          f"{s['pf']:6.2f} {s['lo']:4d} {s['sh']:4d} {extra:6d} {'✓' if over else ''}")

if best:
    th, s = best
    print(f"\n>>> A方案最优 θ={th:.2f}: 收益={s['ret']}% 胜率={s['win']}% 回撤={s['dd']}% "
          f"盈亏比={s['pf']} 笔数={s['n']} 空={s['sh']}")
else:
    print("\n>>> A方案：所有 θ 下双因子均≤单因子，单纯收紧 θ 不足以反超。")

# ── B方案：动量补位仅当趋势已转向(趋势分同向) ──
print("\n=== B方案（动量补位仅当趋势已转向+动量确认）===")
print(f"{'θ':>5} {'收益%':>7} {'笔数':>5} {'胜率%':>6} {'回撤%':>6} {'盈亏比':>6} "
      f"{'多':>4} {'空':>4} {'额外空':>6} {'反超?':>5}")
bestB = None
for th in [0.20, 0.25, 0.30, 0.35, 0.40, 0.50]:
    s = stats(_run(make_dual_gate(th, require_trend_sign=True)))
    extra = s["sh"] - s0["sh"]
    over = s["ret"] > s0["ret"]
    if over and (bestB is None or s["ret"] > bestB[1]["ret"]):
        bestB = (th, s)
    print(f"{th:5.2f} {s['ret']:7.2f} {s['n']:5d} {s['win']:6.1f} {s['dd']:6.2f} "
          f"{s['pf']:6.2f} {s['lo']:4d} {s['sh']:4d} {extra:6d} {'✓' if over else ''}")

if bestB:
    th, s = bestB
    print(f"\n>>> B方案最优 θ={th:.2f}: 收益={s['ret']}% 胜率={s['win']}% 回撤={s['dd']}% "
          f"盈亏比={s['pf']} 笔数={s['n']} 多={s['lo']} 空={s['sh']} "
          f"(单因子基准 {s0['ret']}%)")
else:
    print("\n>>> B方案仍无法反超单因子。")
