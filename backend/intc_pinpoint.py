# -*- coding: utf-8 -*-
"""INTC 昨天(2026-10-14)为何没开仓：直接从 bitget 公共行情拉最近数据进内存(dict)，
跑逐根 gate 判定，不依赖本地(已污染的) db。修正 st_full 结构访问。"""
import os, sys, json, time, urllib.request, datetime
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "backtest"))
from engine import (super_trend, st_signals, strategy, enhanced_signal_handler,
                    _as_p, _extract, SymbolTradeConfig, Params, EnhancedExitRules)

UTC = datetime.timezone.utc
SYM_BITGET = "INTCUSDT"
with open("backtest/intc/intc_config.json", encoding="utf-8") as f:
    BASE = json.load(f)
SC, PR, ER = _extract(BASE)
cfg = SymbolTradeConfig(**SC)
params = Params(**PR)


def pull(tf, gran, limit=1000):
    url = (f"https://api.bitget.com/api/v2/mix/market/candles"
           f"?symbol={SYM_BITGET}&productType=usdt-futures&granularity={gran}&limit={limit}")
    with urllib.request.urlopen(url, timeout=20) as r:
        rows = json.loads(r.read()).get("data", [])
    return [{"ts": int(x[0]), "o": float(x[1]), "h": float(x[2]),
             "l": float(x[3]), "c": float(x[4]), "vol": float(x[5])} for x in rows]


CANDLES = {tf: pull(tf, g) for tf, g in [("1h", 3600), ("15m", 900), ("4h", 14400)]}
klines = CANDLES["1h"]
print(f"INTC 内存数据: 1h {len(klines)} 根  起 {datetime.datetime.utcfromtimestamp(klines[0]['ts']/1000)}  末 {datetime.datetime.utcfromtimestamp(klines[-1]['ts']/1000)}")

st_full = super_trend([c["o"] for c in klines], [c["h"] for c in klines], [c["l"] for c in klines],
                      [c["c"] for c in klines], periods=params.periods, multiplier=params.multiplier,
                      src=params.src, change_atr=params.change_atr)
ST_DICT = hasattr(st_full, "keys")
print(f"st_full 类型: {'dict' if ST_DICT else 'list'}")


def st_dir_at(ts):
    if ST_DICT:
        e = st_full.get(ts) or st_full.get(str(ts))
        return e.get("direction") if e else None
    i = next((i for i, c in enumerate(klines) if c["ts"] == ts), None)
    return st_full[i].get("direction") if i is not None else None


all_sigs = st_signals(klines, st_full, "1h")
for s in all_sigs:
    s["pnl_pct"] = 0.0
    s["bars_since"] = 0

t13 = int(datetime.datetime(2026, 10, 13, tzinfo=UTC).timestamp() * 1000)
t16 = int(datetime.datetime(2026, 10, 16, tzinfo=UTC).timestamp() * 1000)

print("\n===== 2026-10-13 ~ 10-15 每根 1h: 收盘 + ST方向(+1多/-1空) =====")
for c in klines:
    if t13 <= c["ts"] < t16:
        dt = datetime.datetime.utcfromtimestamp(c["ts"] / 1000)
        print(f"  {dt}  close={c['c']:.2f}  ST_dir={st_dir_at(c['ts'])}")

print("\n===== 该区间所有 ST 翻转信号 + gate 判定 =====")
for s in all_sigs:
    if t13 <= s["ts"] < t16:
        window = [c for c in klines if c["ts"] <= s["ts"]]
        full = strategy.evaluate(CANDLES, vars(params), s)
        gate = enhanced_signal_handler(full, window, cfg, candles_by_tf=CANDLES, p=_as_p(params))
        dt = datetime.datetime.utcfromtimestamp(s["ts"] / 1000)
        print(f"  [{dt}] type={s['type']} price={s['price']}  gate.trade={gate.get('trade')} "
              f"verdict={gate.get('verdict')} hidden={gate.get('hidden')}")
