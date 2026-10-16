# -*- coding: utf-8 -*-
"""验证 flow 双因子对 43 服务器当前开启品种是否有效。
流程：从 43 实时拉 enabled=True 的品种及其真实线上配置 → 本地 1h 数据 →
     单因子 vs flow补位 vs flow否决，判定每个品种 flow 是否反超单因子。
复用已固化的 flow_gate（与上线同一套逻辑）。
"""
import os
import sys
import json
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "backtest"))
HOST = "http://43.108.10.84:5174"

from backtest_engine import run_backtest
from regime import TradeConfig
from candle_store import load_candles
from flow_gate import make_flow_gate, make_flow_veto

try:
    from position_enhanced import EnhancedExitRules as _ER
except Exception:
    from position import ExitRules as _ER


def swap2local(sym):
    return sym.replace("-USDT-SWAP", "-USDT")


def fetch_43_enabled():
    with urllib.request.urlopen(HOST + "/api/trade/symbols", timeout=25) as r:
        data = json.loads(r.read().decode())
    return [s for s in data.get("symbols", []) if s.get("enabled")]


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


def stats(res):
    tl = res.get("trades_list") or []
    shorts = [t for t in tl if str(t.get("side")) == "short"]
    longs = [t for t in tl if str(t.get("side")) == "long"]
    return dict(ret=res["return_pct"], n=res["trades"], win=res["win_rate"],
                dd=res["max_dd_pct"], pf=res["profit_factor"],
                lo=len(longs), sh=len(shorts))


def run_symbol(sym43, cfgd):
    loc = swap2local(sym43)
    c1h_full = load_tf(loc, "1h")
    if not c1h_full:
        print(f"\n### {sym43}: 本地无 1h 数据，跳过")
        return None
    c1h = c1h_full
    c4h = load_tf(loc, "4h") or agg(c1h_full, 4)
    c1d = load_tf(loc, "1d") or agg(c1h_full, 24)
    cbtf = {"1h": c1h, "4h": c4h, "1d": c1d}

    p = cfgd["params"]
    er = _ER(**{k: cfgd["exit_rules"][k] for k in _ER.__dataclass_fields__
                if k in cfgd.get("exit_rules", {})})
    erq = _ER(**{k: cfgd["exit_rules_quick"][k] for k in _ER.__dataclass_fields__
                 if k in cfgd.get("exit_rules_quick", {})})
    tc = TradeConfig(**{k: cfgd[k] for k in TradeConfig.__dataclass_fields__ if k in cfgd})
    margin = cfgd.get("margin_usdt", 50.0)
    lev = cfgd.get("leverage", 10)

    def _run(gate_fn):
        return run_backtest(
            candles=c1h, p=p, init_cash=10000, fee_rate=0.0005, allow_short=True,
            live_gate=tc, gate_tf="1h", candles_by_tf=cbtf, exit_rules=er,
            exit_rules_quick=erq, margin_usdt=margin, leverage=lev,
            sizing="fixed", gate_fn=gate_fn,
            er_min=cfgd.get("er_min"), er_weak_min=cfgd.get("er_weak_min"),
            range_filter_enabled=cfgd.get("range_filter_enabled", False),
            atr_filter_enabled=cfgd.get("atr_filter_enabled", False),
            adx_filter_enabled=cfgd.get("adx_filter_enabled", False))

    s0 = stats(_run(None))
    print(f"\n########## {sym43}  ST={p.get('periods')}x{p.get('multiplier')}  "
          f"数据={len(c1h)}根 ##########")
    print(f"  单因子      : ret={s0['ret']:6.2f}% n={s0['n']:3d} win={s0['win']:5.1f}% "
          f"dd={s0['dd']:4.2f}% pf={s0['pf']:4.2f} 多={s0['lo']} 空={s0['sh']}  ←基准")

    best = None
    best_way = "单因子"
    for th in [0.30, 0.50]:
        s = stats(_run(make_flow_gate(th)))
        over = s["ret"] > s0["ret"]
        print(f"  补位θ={th}: ret={s['ret']:6.2f}% n={s['n']:3d} win={s['win']:5.1f}% "
              f"dd={s['dd']:4.2f}% pf={s['pf']:4.2f} 多={s['lo']} 空={s['sh']} {'[OK]' if over else ''}")
        if over and (best is None or s["ret"] > best["ret"]):
            best = s; best_way = f"补位θ={th}"
    for v in [0.30, 0.50]:
        g, cnt = make_flow_veto(v)
        s = stats(_run(g))
        over = s["ret"] > s0["ret"]
        print(f"  否决V={v}: ret={s['ret']:6.2f}% n={s['n']:3d} win={s['win']:5.1f}% "
              f"dd={s['dd']:4.2f}% pf={s['pf']:4.2f} 多={s['lo']} 空={s['sh']} 否={cnt['n']} {'[OK]' if over else ''}")
        if over and (best is None or s["ret"] > best["ret"]):
            best = s; best_way = f"否决V={v}"

    if best and best["ret"] > s0["ret"]:
        gain = best["ret"] - s0["ret"]
        print(f"  >>> {sym43}: flow 有效！最优[{best_way}] {best['ret']:.2f}% > 单因子 {s0['ret']:.2f}% (+{gain:.2f}%)")
        return dict(sym=sym43, st=f"{p.get('periods')}x{p.get('multiplier')}",
                    single=s0["ret"], best=best["ret"], way=best_way, gain=gain,
                    n0=s0["n"], nbest=best["n"])
    print(f"  >>> {sym43}: flow 无效（最优≤单因子 {s0['ret']:.2f}%）")
    return dict(sym=sym43, st=f"{p.get('periods')}x{p.get('multiplier')}",
                single=s0["ret"], best=None, way="—", gain=0.0,
                n0=s0["n"], nbest=s0["n"])


def main():
    enabled = fetch_43_enabled()
    print(f"43 当前开启 {len(enabled)} 个品种: {[s['symbol'] for s in enabled]}")
    # 备份 43 真实配置，便于复用/审计
    for s in enabled:
        key = s["symbol"].replace("-USDT-SWAP", "").lower()
        out = os.path.join(HERE, "backtest", key, f"{key}_43live.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)

    rows = []
    for s in enabled:
        try:
            r = run_symbol(s["symbol"], s)
            if r:
                rows.append(r)
        except Exception as e:
            import traceback as _tb
            print(f"!!! {s['symbol']} ERROR: {e}")
            _tb.print_exc()

    print("\n" + "=" * 70)
    print("汇总：flow 双因子对 43 开启品种是否有效")
    print("=" * 70)
    print(f"{'品种':<16}{'ST':<8}{'单因子%':>8}{'最优flow%':>10}{'增益':>7}{'最优方式':>12}{'笔数':>10}")
    eff = []
    for r in rows:
        best = f"{r['best']:.2f}" if r["best"] is not None else "—"
        print(f"{r['sym']:<16}{r['st']:<8}{r['single']:>8.2f}{best:>10}"
              f"{r['gain']:>+7.2f}{r['way']:>12}{r['n0']}->{r['nbest']}")
        if r["gain"] > 0:
            eff.append(r)
    print("-" * 70)
    if eff:
        print(f"有效品种 {len(eff)}/{len(rows)}: " +
              ", ".join(f"{r['sym']}({r['way']} +{r['gain']:.2f}%)" for r in eff))
    else:
        print(f"有效品种 0/{len(rows)}：flow 对 43 当前开启品种均无反超")


if __name__ == "__main__":
    main()
