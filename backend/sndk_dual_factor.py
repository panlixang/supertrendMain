# -*- coding: utf-8 -*-
"""双因子(趋势+动量) vs 单因子：SNDK 本地 OKX 1h 数据 + 线上47配置 2026 全年对比。

- 数据源：candle_data.db（candle_store.load_candles），SNDK-USDT / 1h / 4h
- 配置：sndk_config.json（线上47真值，ST=15x2.5）
- 双因子：趋势分(SuperTrend打分) + 动量分(N根1h收益率)，合成分过θ则动量补位开仓
- 复用 run_backtest 的持仓/止损/止盈（47出场规则），只替换入场闸门(gate_fn)
"""
import os
import sys
import json
import math
import datetime

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


# ── 双因子 gait 函数 ───────────────────────────────────────
MOM_LOOKBACK = 12      # 动量看最近 12 根 1h（≈0.5 天）
MOM_SCALE = 0.04       # 4% 收益率 ≈ 动量满分
W_TREND = 0.5
W_MOM = 0.5
THETA = 0.20           # 合成分绝对值 > θ 才允许动量补位开仓


def dual_factor_gate(full, candles, live_gate, candles_by_tf, p, typ=None, **kw):
    """趋势(ST 打分) + 动量(收益率) 双因子合成入场闸门。"""
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
    base["dual_composite"] = round(composite, 3)
    base["dual_mom"] = round(mom_norm, 3)

    if base.get("trade") and not base.get("hidden"):
        return base

    reasons = list(base.get("reasons") or [])
    if typ == "sell" and composite < -THETA:
        base["trade"] = True
        base["hidden"] = False
        reasons.append(f"动量补位开空(composite={composite:.2f},mom={mom_norm:.2f})")
        base["reasons"] = reasons
        return base
    if typ == "buy" and composite > THETA:
        base["trade"] = True
        base["hidden"] = False
        reasons.append(f"动量补位开多(composite={composite:.2f},mom={mom_norm:.2f})")
        base["reasons"] = reasons
        return base
    return base


# ── 配置 & 本地数据 ───────────────────────────────────────
# 注：sndk_config.json 是 enabled=False 的过时模板，不能复现线上；
# sndk_opt_config.json 的 position=short@1590 与实盘探针一致，才是线上47真值。
cfgd = json.load(open(os.path.join(HERE, "backtest/SNDK/sndk_opt_config.json"), encoding="utf-8"))
print("加载本地 OKX 数据 (candle_data.db) ...")
SYM = "SNDK-USDT"


def load_tf(tf):
    cs = load_candles(SYM, tf, limit=10_000_000)
    return [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol}
            for c in cs]


def agg_1d(c1h, step=24):
    out = []
    for i in range(0, len(c1h), step):
        grp = c1h[i:i + step]
        if not grp:
            continue
        out.append({
            "ts": grp[0]["ts"], "o": grp[0]["o"],
            "h": max(x["h"] for x in grp), "l": min(x["l"] for x in grp),
            "c": grp[-1]["c"], "vol": sum(x["vol"] for x in grp),
        })
    return out


c1h = load_tf("1h")
c4h = load_tf("4h")
c1d = agg_1d(c1h)
print(f"数据: 1h={len(c1h)} 4h={len(c4h)} 1d={len(c1d)}  "
      f"1h范围={datetime.datetime.utcfromtimestamp(c1h[0]['ts']/1000):%Y-%m-%d}~"
      f"{datetime.datetime.utcfromtimestamp(c1h[-1]['ts']/1000):%Y-%m-%d}")

tc = TradeConfig(**{k: cfgd[k] for k in TradeConfig.__dataclass_fields__ if k in cfgd})
p = cfgd["params"]
er = _ER(**{k: cfgd["exit_rules"][k] for k in _ER.__dataclass_fields__ if k in cfgd["exit_rules"]})
erq = _ER(**{k: cfgd["exit_rules_quick"][k] for k in _ER.__dataclass_fields__ if k in cfgd["exit_rules_quick"]})
candles_by_tf = {"1h": c1h, "4h": c4h, "1d": c1d}


def _run(gate_fn):
    return run_backtest(
        candles=c1h, p=p, init_cash=10000.0, fee_rate=0.0005,
        allow_short=True, live_gate=tc, gate_tf="1h",
        candles_by_tf=candles_by_tf, exit_rules=er, exit_rules_quick=erq,
        margin_usdt=50.0, leverage=10, sizing="fixed", gate_fn=gate_fn,
        er_min=cfgd.get("er_min"), er_weak_min=cfgd.get("er_weak_min"),
        range_filter_enabled=cfgd.get("range_filter_enabled", False),
        atr_filter_enabled=cfgd.get("atr_filter_enabled", False),
        adx_filter_enabled=cfgd.get("adx_filter_enabled", False),
    )


def summary(label, res):
    print(f"\n=== {label} ===")
    print(f"  收益率={res['return_pct']}%  交易数={res['trades']}  "
          f"胜率={res['win_rate']}%  最大回撤={res['max_dd_pct']}%  "
          f"盈亏比={res['profit_factor']}")
    print(f"  均盈={res['avg_win']}%  均亏={res['avg_loss']}%  "
          f"平均持仓={res['avg_bars']}根")
    tl = res.get("trades_list") or []
    shorts = [t for t in tl if str(t.get("side")) == "short"]
    longs = [t for t in tl if str(t.get("side")) == "long"]
    print(f"  多单={len(longs)}  空单={len(shorts)}")


print(f"\n=== [双因子] 权重 趋势{W_TREND}/动量{W_MOM} 窗口{MOM_LOOKBACK} θ={THETA} ===")
res_dual = _run(dual_factor_gate)
summary("双因子（趋势+动量）SNDK 2026", res_dual)

res_single = _run(None)
summary("单因子（原 live_gate）SNDK 2026", res_single)


def _print_window(label, res):
    tl = res.get("trades_list") or []
    w0 = int(datetime.datetime(2026, 10, 13, 0, 0).timestamp() * 1000)
    w1 = int(datetime.datetime(2026, 10, 15, 12, 0).timestamp() * 1000)
    print(f"\n--- {label} (10-13~15) ---")
    hit = False
    for t in tl:
        et = t.get("entry_ts") or t.get("entry_time") or 0
        if w0 <= et <= w1:
            hit = True
            xt = t.get("exit_ts") or t.get("exit_time") or 0
            ex = (datetime.datetime.utcfromtimestamp(xt / 1000).strftime("%m-%d %H:%M")
                  if xt else "—")
            print(f"  {datetime.datetime.utcfromtimestamp(et/1000):%m-%d %H:%M} "
                  f"{str(t.get('side')):5s} entry={t.get('entry')} exit@{ex} "
                  f"pnl%={t.get('pnl_pct')} reason={t.get('reason')}")
    if not hit:
        print("  (该窗口无成交)")


_print_window("双因子", res_dual)
_print_window("单因子", res_single)
