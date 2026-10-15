# -*- coding: utf-8 -*-
"""AMD 10-14 多止损后为何没反手开空：从 bitget 拉实时数据，
用 engine.run_core 复现持仓流 + 逐根信号 gate 判定。"""
import os, sys, datetime, json, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "backtest"))
from engine import (run_core, super_trend, st_signals, strategy,
                    enhanced_signal_handler, _as_p, _extract, SymbolTradeConfig,
                    Params, EnhancedExitRules)

UTC = datetime.timezone.utc
BASE43 = "http://43.108.10.84:5174"


def get43(ep):
    return json.loads(urllib.request.urlopen(BASE43 + ep, timeout=15).read())


def get_config(sym):
    """从 43 页拉该品种当前配置"""
    d = get43("/api/trade/symbols")
    for s in d["symbols"]:
        if s.get("symbol") == sym:
            return s
    raise RuntimeError(f"43页未找到 {sym}")


def pull_bitget(symbol, gran, limit=500):
    url = (f"https://api.bitget.com/api/v2/mix/market/candles"
           f"?symbol={symbol}&productType=usdt-futures&granularity={gran}&limit={limit}")
    rows = json.loads(urllib.request.urlopen(url, timeout=20).read()).get("data", [])
    return [{"ts": int(x[0]), "o": float(x[1]), "h": float(x[2]),
             "l": float(x[3]), "c": float(x[4]), "vol": float(x[5])} for x in rows]


SYM43 = "AMD-USDT-SWAP"
SYM_BIT = "AMDUSDT"

cfg_dict = get_config(SYM43)
print("=== AMD 43页当前配置 ===")
print(json.dumps({k: cfg_dict.get(k) for k in [
    "enabled", "periods", "multiplier", "src", "change_atr",
    "full_threshold", "half_threshold", "alert_threshold",
    "tp1_pct", "tp1_ratio", "tp2_pct", "tp2_ratio", "tp3_pct", "tp3_ratio",
    "sl_mode", "sl_pct", "trail_with_st", "move_sl_to_entry",
    "use_scoring", "score_engine", "er_trend", "er_min", "er_weak", "er_hide_below",
    "range_filter_enabled", "mtf_filter_enabled", "atr_filter_enabled", "adx_filter_enabled",
    "cooldown_sec", "leverage", "margin_usdt"]}, indent=2, ensure_ascii=False))

# 构造引擎对象（_extract 与 43 页返回结构一致）
SC, PR, ER = _extract(cfg_dict)
cfg = SymbolTradeConfig(**SC)
params = Params(**PR)
exit_rules = EnhancedExitRules(**ER)

# 拉数据
CANDLES = {tf: pull_bitget(SYM_BIT, g) for tf, g in [("1h", 3600), ("15m", 900), ("4h", 14400)]}
klines = CANDLES["1h"]
print(f"\nbitget AMD 1h: {len(klines)} 根, 时间 {datetime.datetime.utcfromtimestamp(klines[0]['ts']/1000)} ~ {datetime.datetime.utcfromtimestamp(klines[-1]['ts']/1000)}")

# run_core 复现持仓流
r = run_core(SYM43, SC, PR, ER, tf="1h", candles=CANDLES)
print("\n=== 10-14 附近 run_core 交易明细 ===")
t14 = int(datetime.datetime(2026, 10, 14, tzinfo=UTC).timestamp() * 1000)
t15 = int(datetime.datetime(2026, 10, 15, tzinfo=UTC).timestamp() * 1000)
for t in r["trades"]:
    if t14 <= t["entry_ts"] < t15 or t14 <= (t.get("exit_ts") or 0) < t15:
        et = datetime.datetime.utcfromtimestamp(t["entry_ts"] / 1000)
        xt = datetime.datetime.utcfromtimestamp((t.get("exit_ts") or 0) / 1000)
        print(f"  [{et} -> {xt}] side={t['side']} entry={t['entry']:.2f} exit={t.get('exit',0):.2f} "
              f"pnl={t.get('pnl',0):.2f} reason={t.get('reason','')}")

# 逐根信号 gate 判定
st_full = super_trend([c["o"] for c in klines], [c["h"] for c in klines],
                      [c["l"] for c in klines], [c["c"] for c in klines],
                      periods=params.periods, multiplier=params.multiplier,
                      src=params.src, change_atr=params.change_atr)
all_sigs = st_signals(klines, st_full, "1h")
for s in all_sigs:
    s["pnl_pct"] = 0.0
    s["bars_since"] = 0

print("\n=== 10-14 全天 ST 翻转信号 + gate 判定 ===")
for s in all_sigs:
    if t14 <= s["ts"] < t15:
        dt = datetime.datetime.utcfromtimestamp(s["ts"] / 1000)
        window = [c for c in klines if c["ts"] <= s["ts"]]
        full = strategy.evaluate(CANDLES, vars(params), s)
        gate = enhanced_signal_handler(full, window, cfg, candles_by_tf=CANDLES, p=_as_p(params))
        print(f"  [{dt}] type={s['type']} price={s['price']:.2f} "
              f"trade={gate.get('trade')} verdict={gate.get('verdict')} hidden={gate.get('hidden')} "
              f"reasons={gate.get('gate_reasons')}")
