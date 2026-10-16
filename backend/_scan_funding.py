# -*- coding: utf-8 -*-
"""B严格验证：用真实资金费率历史(来自 fetch_oi.py 存入的 funding_history 表)
做双因子(趋势+资金费率)，在 33 天窗口内回测，与 vol proxy 结论交叉验证。
资金费率语义：正费率=多头拥挤(潜在回调)，负费率=空头拥挤(潜在反弹)，
故 funding_sentiment 与价格趋势反向，是比 vol 更低相关的维度。
"""
import os
import sys
import json
import math
import sqlite3

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

RATE_SCALE = 0.0001  # 典型费率0.01%映射至tanh≈0.76
WINDOW = 33 * 24     # 33天1h根数（funding_history 约覆盖此窗口）


def load_funding(sym):
    con = sqlite3.connect(os.path.join(HERE, "candle_data.db"))
    rows = con.execute(
        "SELECT ts, funding_rate FROM funding_history WHERE symbol=? ORDER BY ts",
        (sym,)).fetchall()
    con.close()
    return [(int(r[0]), float(r[1])) for r in rows]


def funding_at(flist, ts):
    rate = 0.0
    for t, r in flist:
        if t <= ts:
            rate = r
        else:
            break
    return rate


def funding_sentiment(candles, flist):
    if not flist:
        return 0.0
    ts = candles[-1]["ts"]
    rate = funding_at(flist, ts)
    return math.tanh(rate / RATE_SCALE)


def make_fund_gate(THETA, flist, W_TREND=0.5, W_FUND=0.5):
    def gate(full, candles, live_gate, candles_by_tf, p, typ=None, **kw):
        base = enhanced_signal_handler(
            full, candles, live_gate, candles_by_tf=candles_by_tf, p=p,
            use_momentum=True, use_false_filter=True, use_adaptive=True)
        ts = base.get("total_score") or 0
        trend_norm = (ts - 50) / 50.0
        fs = funding_sentiment(candles, flist)
        composite = W_TREND * trend_norm + W_FUND * fs
        if base.get("trade") and not base.get("hidden"):
            return base
        reasons = list(base.get("reasons") or [])
        if typ == "sell" and composite < -THETA:
            base["trade"] = True
            base["hidden"] = False
            reasons.append(f"费率补位空(c={composite:.2f},f={fs:.2f})")
            base["reasons"] = reasons
            return base
        if typ == "buy" and composite > THETA:
            base["trade"] = True
            base["hidden"] = False
            reasons.append(f"费率补位多(c={composite:.2f},f={fs:.2f})")
            base["reasons"] = reasons
            return base
        return base
    return gate


def make_fund_veto(VETO, flist):
    cnt = {"n": 0}

    def gate(full, candles, live_gate, candles_by_tf, p, typ=None, **kw):
        base = enhanced_signal_handler(
            full, candles, live_gate, candles_by_tf=candles_by_tf, p=p,
            use_momentum=True, use_false_filter=True, use_adaptive=True)
        fs = funding_sentiment(candles, flist)
        if base.get("trade") and not base.get("hidden"):
            # 趋势看多但情绪极强看多(费率正拥挤)→否决(背离反转风险)
            if typ == "buy" and fs > VETO:
                base["trade"] = False
                base["hidden"] = True
                cnt["n"] += 1
                return base
            # 趋势看空但情绪极强看空(费率负拥挤)→否决
            if typ == "sell" and fs < -VETO:
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


def run_symbol(name, sym, cfgpath):
    flist = load_funding(sym)
    c1h_full = load_tf(sym, "1h")
    c4h_full = load_tf(sym, "4h")
    c1h = c1h_full[-WINDOW:]
    c4h = c4h_full[-WINDOW // 4:]
    c1d = agg(c1h_full)[-WINDOW // 24:]
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
    print(f"\n########## {name} ({sym}) 窗口=近{WINDOW}根1h 费率点={len(flist)} ##########")
    print(f"  单因子       : ret={s0['ret']:6.2f}% n={s0['n']:3d} win={s0['win']:5.1f}% "
          f"dd={s0['dd']:4.2f}% pf={s0['pf']:4.2f}  ←基准")

    best = None
    for th in [0.3, 0.5]:
        s = stats(_run(make_fund_gate(th, flist)))
        over = s["ret"] > s0["ret"]
        print(f"  费率补位θ={th}: ret={s['ret']:6.2f}% n={s['n']:3d} win={s['win']:5.1f}% "
              f"dd={s['dd']:4.2f}% pf={s['pf']:4.2f} {'[OK]' if over else ''}")
        if over and (best is None or s["ret"] > best[1]["ret"]):
            best = (f"费率补位θ={th}", s)
    for v in [0.3, 0.5]:
        g, cnt = make_fund_veto(v, flist)
        s = stats(_run(g))
        over = s["ret"] > s0["ret"]
        print(f"  费率否决V={v}: ret={s['ret']:6.2f}% n={s['n']:3d} win={s['win']:5.1f}% "
              f"dd={s['dd']:4.2f}% pf={s['pf']:4.2f} 否={cnt['n']} {'[OK]' if over else ''}")
        if over and (best is None or s["ret"] > best[1]["ret"]):
            best = (f"费率否决V={v}", s)

    if best:
        print(f"  >>> {name}: 真实费率双因子最优 [{best[0]}] ret={best[1]['ret']:.2f}% (单因子 {s0['ret']:.2f}%)")
    else:
        print(f"  >>> {name}: 真实费率双因子亦≤单因子")


for name, (sym, cfgpath) in SYMS.items():
    try:
        run_symbol(name, sym, cfgpath)
    except Exception as e:
        import traceback as _tb
        print(f"!!! {name} ERROR: {repr(e)}")
        _tb.print_exc()
