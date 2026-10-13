# -*- coding: utf-8 -*-
"""终检：拉取 47 线上 MU 真实配置，用同一口径回测，确认实盘 == 寻优放宽档结果。"""
import os
import sys
import json
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(BACKEND, "backtest"))
from engine import run_core, _extract, _load_tf

HOST = os.environ.get("HOST47", "http://47.84.106.154:5174")
SYM = "MU-USDT-SWAP"
DB_SYM = "MU-USDT"
TS_2026 = 1767225600000
NOTIONAL = 1000.0    # 100U x 10x
FEE_T, FEE_M = 0.1, 0.04

live = {s["symbol"]: s for s in json.loads(
    urllib.request.urlopen(HOST + "/api/trade/symbols", timeout=25).read().decode())["symbols"]}[SYM]
sc, pr, er = _extract(live)

CANDLES = {tf: [c for c in _load_tf(DB_SYM, tf) if c["ts"] >= TS_2026]
           for tf in ["15m", "1h", "4h"]}
r = run_core(DB_SYM, sc, pr, er, tf="1h", candles=CANDLES)
a, i, o = r["all"], r["is"], r["oos"]

print("=== 47 线上 MU 实际配置终检 ===")
print(f"  ST        {pr['periods']}x{pr['multiplier']} src={pr['src']} change_atr={pr['change_atr']}")
print(f"  range闸门  enabled={live['range_filter_enabled']} size_max={live['range_size_max']} "
      f"touches={live['range_touches_min']}")
print(f"  评分闸门   full={live['scoring_full_threshold']} alert={live['scoring_alert_threshold']}")
print(f"  ER        trend={live['er_trend']} min={live['er_min']} weak={live['er_weak_min']}")
print(f"  出场      tp1={er['tp1_pct']}/{er['tp1_ratio']} tp2={er['tp2_pct']}/{er['tp2_ratio']} "
      f"tp3={er['tp3_pct']} sl={er['sl_pct']} trail={er['trail_with_st']}")
print(f"  仓位      margin={live['margin_usdt']}U lev={live['leverage']}x enabled={live['enabled']}")

n, ret = a["n"], a["ret_pct"]
print(f"\n  回测(2026窗口): IS={i['ret_pct']:+.2f}% OOS={o['ret_pct']:+.2f}% PF_oos={o['pf']:.2f} "
      f"全={ret:+.2f}% n={n} 胜率={a['winrate']:.1f}% DD={r['max_dd']:.2f}%")
print(f"  扣费(taker): 净={ret-FEE_T*n:+.2f}%   扣费(maker): 净={ret-FEE_M*n:+.2f}%")
print(f"\n  按 100U x 10x = 名义{NOTIONAL:.0f}U:")
print(f"    毛 {NOTIONAL*ret/100:>8.1f} U")
print(f"    费(taker) {n*NOTIONAL*FEE_T/100:>8.1f} U  ({n} 笔)")
print(f"    净(taker) {NOTIONAL*(ret-FEE_T*n)/100:>8.1f} U")
print(f"    净(maker) {NOTIONAL*(ret-FEE_M*n)/100:>8.1f} U")

exp = dict(n=222, ret=59.40, dd=18.9, net=37.20)
ok = (n == exp["n"] and abs(ret - exp["ret"]) < 0.5
      and abs((ret - FEE_T * n) - exp["net"]) < 0.5)
print(f"\n  期望(放宽档寻优): 全=+59.40% n=222 DD=18.9% taker净=+37.20%")
print(f"  结论: {'✅ 线上口径与回测完全一致' if ok else '❌ 与寻优结果不符，需排查'}")
