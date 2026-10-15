# -*- coding: utf-8 -*-
"""用 47 同源配置 + 主引擎 live_gate 模式，复现 SNDK 10-14 信号判定。"""
import os
import sys
import json
import datetime
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "backtest"))

from backtest_engine import run_backtest
from regime import TradeConfig

try:
    from position_enhanced import EnhancedExitRules as Rules
except Exception:
    from position import ExitRules as Rules


def bitget(symbol, tf, limit=400):
    url = (f"https://api.bitget.com/api/v2/mix/market/candles"
           f"?productType=usdt-futures&symbol={symbol}&granularity={tf}&limit={limit}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = json.loads(urllib.request.urlopen(req, timeout=20).read())
    out = [{"ts": int(r[0]), "o": float(r[1]), "h": float(r[2]),
            "l": float(r[3]), "c": float(r[4]), "vol": float(r[5])}
           for r in raw.get("data", [])]
    out.sort(key=lambda x: x["ts"])
    return out


def mk_rules(d):
    fields = set(Rules.__dataclass_fields__)
    return Rules(**{k: d[k] for k in fields if k in d})


cfgd = json.load(open(os.path.join(HERE, "backtest/SNDK/sndk_opt_config.json"), encoding="utf-8"))

c1h = bitget("SNDKUSDT", "1H", 400)
c4h = bitget("SNDKUSDT", "4H", 300)
c1d = bitget("SNDKUSDT", "1D", 300)
print(f"数据: 1h={len(c1h)} 4h={len(c4h)} 1d={len(c1d)}  "
      f"1h末={datetime.datetime.utcfromtimestamp(c1h[-1]['ts']/1000)}")

tc_fields = set(TradeConfig.__dataclass_fields__)
tc = TradeConfig(**{k: cfgd[k] for k in tc_fields if k in cfgd})
p = cfgd["params"]
er = mk_rules(cfgd["exit_rules"])
erq = mk_rules(cfgd["exit_rules_quick"])

candles_by_tf = {"1h": c1h, "4h": c4h, "1d": c1d}

res = run_backtest(
    candles=c1h, p=p, init_cash=10000.0, fee_rate=0.0005,
    allow_short=True, live_gate=tc, gate_tf="1h",
    candles_by_tf=candles_by_tf, exit_rules=er, exit_rules_quick=erq,
    margin_usdt=50.0, leverage=10, sizing="fixed",
    er_min=cfgd.get("er_min"), er_weak_min=cfgd.get("er_weak_min"),
    range_filter_enabled=cfgd.get("range_filter_enabled", False),
    atr_filter_enabled=cfgd.get("atr_filter_enabled", False),
    adx_filter_enabled=cfgd.get("adx_filter_enabled", False),
)
print("run_backtest keys:", list(res.keys()))
tlist = res.get("trades_list") or res.get("trade_list") or []
print("总笔数:", res.get("trades"), " trades_list长度:", len(tlist))

w0 = int(datetime.datetime(2026, 10, 13, 0, 0).timestamp() * 1000)
w1 = int(datetime.datetime(2026, 10, 15, 12, 0).timestamp() * 1000)

print("\n=== 10-13 00:00 ~ 10-15 12:00 成交 ===")
for t in tlist:
    et = t.get("entry_ts") or t.get("entry_time") or 0
    if w0 <= et <= w1:
        xt = t.get("exit_ts") or t.get("exit_time") or 0
        ex = (datetime.datetime.utcfromtimestamp(xt / 1000).strftime("%m-%d %H:%M")
              if xt else "—")
        print(f"  {datetime.datetime.utcfromtimestamp(et/1000):%m-%d %H:%M} "
              f"{str(t.get('side')):5s} entry={t.get('entry')} exit@{ex} "
              f"pnl={t.get('pnl')} reason={t.get('reason')}")

# ── 直接把 10-14 翻转信号喂给 live_gate 的 gate，看是否被 hidden ──
from indicators import st_signals, super_trend
from strategy import mtf_bias, grade, evaluate
from integration import enhanced_signal_handler

st = super_trend([c["o"] for c in c1h], [c["h"] for c in c1h],
                 [c["l"] for c in c1h], [c["c"] for c in c1h],
                 periods=p["periods"], multiplier=p["multiplier"],
                 src=p.get("src", "close"), change_atr=p.get("change_atr", True))
flips = st.get("flips") or []
print(f"\n=== ST({p['periods']}x{p['multiplier']}) 10-13~15 翻转信号 ===")
for f in flips:
    i = f["i"]
    ts = c1h[i]["ts"]
    if w0 <= ts <= w1:
        print(f"  {datetime.datetime.utcfromtimestamp(ts/1000):%m-%d %H:%M} "
              f"{f['type']} @ {c1h[i]['c']}")

# 反推 47 真实 ST：实盘 10-14 17:00 翻空平多，扫不同参数找翻空≈17:00 的
print("\n=== 反推 47 真实 ST（找 10-14 翻空≈17:00 的参数）===")
import itertools
best = None
for per, mul in itertools.product(range(10, 31, 1), [2.0, 2.5, 3.0, 3.5, 4.0]):
    s = super_trend([c["o"] for c in c1h], [c["h"] for c in c1h],
                    [c["l"] for c in c1h], [c["c"] for c in c1h],
                    periods=per, multiplier=mul,
                    src=p.get("src", "close"), change_atr=p.get("change_atr", True))
    for f in (s.get("flips") or []):
        if f["type"] == "sell" and w0 <= c1h[f["i"]]["ts"] <= w1:
            diff = abs(c1h[f["i"]]["ts"] - 1791968401459)
            if best is None or diff < best[0]:
                best = (diff, per, mul, c1h[f["i"]]["ts"])
if best:
    ts = best[3]
    print(f"  最佳匹配: ST={best[1]}x{best[2]}  翻空@"
          f"{datetime.datetime.utcfromtimestamp(ts/1000):%m-%d %H:%M}  "
          f"误差={best[0]/3.6e6:.1f}h")
else:
    print("  10-13~15 未找到 sell 翻转（参数均过钝）")

verdict = mtf_bias(candles_by_tf, p).get("verdict")
print("MTF verdict:", verdict)
sigs = st_signals(c1h, st, "1h")
for s in sigs:
    s["grade"] = grade(s, verdict)


def cbtf_at(ts):
    return {tf: [c for c in arr if c["ts"] <= ts]
            for tf, arr in candles_by_tf.items()
            if any(c["ts"] <= ts for c in arr)}


print("\n=== 10-14 各 sell 信号 gate 判定（live_gate 口径）===")
found = False
for s in sigs:
    ts = s["ts"]
    if w0 <= ts <= w1 and s["type"] == "sell":
        found = True
        i = next(k for k, c in enumerate(c1h) if c["ts"] == ts)
        full = evaluate(cbtf_at(ts), p, s)
        gate = enhanced_signal_handler(
            full, c1h[:i + 1], tc, candles_by_tf=cbtf_at(ts), p=p,
            use_momentum=True, use_false_filter=True, use_adaptive=True)
        print(f"  {datetime.datetime.utcfromtimestamp(ts/1000):%m-%d %H:%M} "
              f"sell grade={s.get('grade')}")
        print(f"    → trade={gate.get('trade')}  hidden={gate.get('hidden')}  "
              f"profile={gate.get('profile')}")
        import json as _j
        _s = lambda o: _j.dumps(o, ensure_ascii=True, default=str)
        print("    FULL GATE:", _s({k: gate.get(k) for k in
              ('reasons', 'regime', 'trade', 'hidden', 'profile',
               'momentum', 'filters', 'score', 'total_score')}))
if not found:
    print("  ⚠ 10-14 窗口内 ST 没有任何 sell 翻转信号（即连 sell 都没产生）")
