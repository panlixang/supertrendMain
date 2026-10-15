# -*- coding: utf-8 -*-
"""INTC 昨天(2026-10-14)附近为何没开仓诊断：
打印附近 ST 方向 + 翻转信号 + enhanced_signal_handler 的 gate 全貌。"""
import os, sys, json, datetime
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "backtest"))
from engine import (run_core, _extract, _load_tf, super_trend, st_signals,
                    strategy, enhanced_signal_handler, _as_p,
                    SymbolTradeConfig, Params, EnhancedExitRules)

CONFIG = "backtest/intc/intc_config.json"
with open(CONFIG, encoding="utf-8") as f:
    BASE = json.load(f)
SC, PR, ER = _extract(BASE)
cfg = SymbolTradeConfig(**SC)
params = Params(**PR)
exit_rules = EnhancedExitRules(**ER)

DB_SYM = None
for cand in ["INTC-USDT", "INTC-USDT-SWAP"]:
    try:
        if _load_tf(cand, "1h"):
            DB_SYM = cand
            break
    except Exception:
        pass
print(f"本地 INTC db = {DB_SYM}")

TS = 1767225600000
CANDLES = {tf: [c for c in _load_tf(DB_SYM, tf) if c["ts"] >= TS]
           for tf in ["15m", "1h", "4h"]}
klines = CANDLES["1h"]
print(f"INTC 1h {len(klines)} 根  起 {datetime.datetime.utcfromtimestamp(klines[0]['ts']/1000)}  末 {datetime.datetime.utcfromtimestamp(klines[-1]['ts']/1000)}")

st_full = super_trend([c["o"] for c in klines], [c["h"] for c in klines],
                      [c["l"] for c in klines], [c["c"] for c in klines],
                      periods=params.periods, multiplier=params.multiplier,
                      src=params.src, change_atr=params.change_atr)
all_sigs = st_signals(klines, st_full, "1h")
for s in all_sigs:
    s["pnl_pct"] = 0.0
    s["bars_since"] = 0

# 全样本交易多/空分布
r = run_core(DB_SYM, SC, PR, ER, tf="1h", candles=CANDLES)
from collections import Counter
sides = Counter(t["side"] for t in r["trades"])
print(f"全样本交易: 总 {len(r['trades'])} 笔  long={sides.get('long',0)} short={sides.get('short',0)}")
print(f"  全收益={r['all']['ret_pct']:+.2f}%  PF={r['all']['pf']:.2f}  DD={r['max_dd']:.1f}%")
if r["trades"]:
    last = max(r["trades"], key=lambda t: t["entry_ts"])
    print(f"  最近一笔开仓: {datetime.datetime.utcfromtimestamp(last['entry_ts']/1000)} side={last['side']}")

# 昨天附近 ±3 天的信号 + gate
TARGET = datetime.datetime(2026, 10, 14, 0, 0, 0, tzinfo=datetime.timezone.utc)
target_ts = int(TARGET.timestamp() * 1000)
print(f"\n===== 2026-10-14 附近 (±3天) 的 ST 翻转信号与 gate 判定 =====")
for s in all_sigs:
    if abs(s["ts"] - target_ts) <= 3 * 24 * 3600 * 1000:
        window = [c for c in klines if c["ts"] <= s["ts"]]
        full = strategy.evaluate(CANDLES, vars(params), s)
        gate = enhanced_signal_handler(full, window, cfg, candles_by_tf=CANDLES, p=_as_p(params))
        dt = datetime.datetime.utcfromtimestamp(s["ts"] / 1000)
        print(f"\n[{dt}] type={s['type']} price={s['price']}")
        print(f"  gate.trade={gate.get('trade')}  verdict={gate.get('verdict')}  hidden={gate.get('hidden')}")
        print(f"  gate全貌: {json.dumps({k: gate.get(k) for k in gate}, ensure_ascii=False, default=str)[:600]}")
        # ST 方向（信号根）
        idx = next(i for i, c in enumerate(klines) if c["ts"] == s["ts"])
        print(f"  ST方向(信号根)={st_full[idx].get('direction')}  ER trend={full.get('er_trend')} score={full.get('score')}")
