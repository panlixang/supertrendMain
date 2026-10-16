# -*- coding: utf-8 -*-
"""BTC 复盘：定位单因子为何胜率高(68%)却亏损(pf<1)。"""
import os
import sys
import json
import datetime
from collections import defaultdict

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


def bt(gate_fn=None, params=None, er_rules=None):
    return run_backtest(
        candles=c1h, p=params or p, init_cash=10000, fee_rate=0.0005, allow_short=True,
        live_gate=tc, gate_tf="1h", candles_by_tf=cbtf,
        exit_rules=er_rules or er, exit_rules_quick=erq,
        margin_usdt=cfgd["margin_usdt"], leverage=cfgd["leverage"],
        sizing="fixed", gate_fn=gate_fn,
        er_min=cfgd.get("er_min"), er_weak_min=cfgd.get("er_weak_min"),
        range_filter_enabled=cfgd.get("range_filter_enabled", False),
        atr_filter_enabled=cfgd.get("atr_filter_enabled", False),
        adx_filter_enabled=cfgd.get("adx_filter_enabled", False))


res = bt()
tl = res["trades_list"]
print(f"BTC ST={p['periods']}x{p['multiplier']}  数据 {len(c1h)}根  "
      f"({datetime.datetime.utcfromtimestamp(res['start_ts']/1000):%Y-%m} ~ "
      f"{datetime.datetime.utcfromtimestamp(res['end_ts']/1000):%Y-%m})")
print(f"总收益 {res['return_pct']:.2f}%  交易 {res['trades']}  胜率 {res['win_rate']:.1f}%  "
      f"盈亏比(pf) {res['profit_factor']:.2f}  回撤 {res['max_dd_pct']:.2f}%  均bars {res['avg_bars']:.1f}")
print(f"出场计数: 止盈1={res['tp1_count']} 止盈2={res['tp2_count']} 止盈3={res['tp3_count']} "
      f"止损={res['stop_count']} 最大亏损={res['max_loss_count']} 反向={res['reverse_count']}")

# 1) 多空分离
print("\n==== 多空分离 ====")
for side in ("long", "short"):
    ts = [t for t in tl if t["side"] == side]
    if not ts:
        continue
    wins = [t for t in ts if t["pnl"] > 0]
    losses = [t for t in ts if t["pnl"] <= 0]
    aw = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    al = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
    print(f"  {side:5s}: n={len(ts):3d} 总pnl={sum(t['pnl'] for t in ts):8.1f}U  "
          f"胜率={len(wins)/len(ts)*100:4.1f}%  均盈={aw:6.1f}U  均亏={al:6.1f}U  "
          f"盈亏比={abs(aw/al) if al else 0:.2f}")

# 2) 出场原因盈亏贡献
print("\n==== 出场原因分布（按总盈亏排序）====")
byr = defaultdict(lambda: [0, 0.0, 0.0])
for t in tl:
    byr[t["reason"]][0] += 1
    byr[t["reason"]][1] += t["pnl"]
    byr[t["reason"]][2] += t["bars"]
for r, (c, tp, tb) in sorted(byr.items(), key=lambda x: x[1][1]):
    print(f"  {r:10s} n={c:3d}  总pnl={tp:8.1f}U  均pnl={tp/c:7.2f}U  均bars={tb/c:5.1f}")

# 3) 盈亏比倒挂证据
wins = [t["pnl"] for t in tl if t["pnl"] > 0]
losses = [t["pnl"] for t in tl if t["pnl"] <= 0]
print(f"\n==== 盈亏比倒挂 ====")
print(f"  avg_win={res['avg_win']:.2f}U  avg_loss={res['avg_loss']:.2f}U  "
      f"比值={abs(res['avg_win']/res['avg_loss']):.2f}  "
      f"(胜率{res['win_rate']:.1f}% 需要盈亏比≥{res['win_rate']/(100-res['win_rate']):.2f} 才保本)")
print(f"  最大单笔盈利={max(wins):.1f}U  最大单笔亏损={min(losses):.1f}U")

# 4) worst / best
s = sorted(tl, key=lambda t: t["pnl"])
print("\n==== 最差 8 笔 ====")
for t in s[:8]:
    d = datetime.datetime.utcfromtimestamp(t["entry_ts"] / 1000)
    print(f"  {t['side']:5s} {t['pnl']:8.1f}U {t['pnl_pct']:6.2f}% bars={t['bars']:3d} "
          f"{t['reason']:8s} {d:%Y-%m}")
print("==== 最佳 8 笔 ====")
for t in s[-8:]:
    d = datetime.datetime.utcfromtimestamp(t["entry_ts"] / 1000)
    print(f"  {t['side']:5s} {t['pnl']:8.1f}U {t['pnl_pct']:6.2f}% bars={t['bars']:3d} "
          f"{t['reason']:8s} {d:%Y-%m}")

# 5) 分年
print("\n==== 分年收益 ====")
yr = defaultdict(lambda: [0, 0.0])
for t in tl:
    y = datetime.datetime.utcfromtimestamp(t["entry_ts"] / 1000).year
    yr[y][0] += 1
    yr[y][1] += t["pnl"]
for y, (c, tp) in sorted(yr.items()):
    print(f"  {y}: n={c:3d}  pnl={tp:8.1f}U")
