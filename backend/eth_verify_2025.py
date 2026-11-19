# -*- coding: utf-8 -*-
"""ETH 寻优最优 vs 线上当前 · 2025 长窗口(2025-01-01起) 交叉验证。
复用 optimize_any.py 同引擎(engine.run_core, 走 enhanced_signal_handler 闸门链路)。"""
import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "backtest"))
from engine import run_core, _extract, _load_tf

DB_SYM = "ETH-USDT"
TS_2025 = 1735689600000  # 2025-01-01

CANDLES = {tf: [c for c in _load_tf(DB_SYM, tf) if c["ts"] >= TS_2025]
           for tf in ["15m", "1h", "4h"]}
N1H = len(CANDLES["1h"])
print(f"[数据] {DB_SYM} 1h(2025长窗口, 2025-01起) {N1H} 根\n")

CONFIGS = {
    "寻优最优(13x2.5 close/80-40/ER0.25-0.1)": "backtest/eth/eth_config_opt_config.json",
    "线上当前(15x2.0 hl2/80-80-40/ER0.3-0.1)": "backtest/eth/eth_config.json",
}

for name, cfgp in CONFIGS.items():
    with open(cfgp, encoding="utf-8") as f:
        BASE = json.load(f)
    SC, PR, ER = _extract(BASE)
    print(f"===== {name} =====")
    print(f"  params={PR.get('periods')}x{PR.get('multiplier')} {PR.get('src')} | "
          f"gates {SC.get('scoring_full_threshold')}/{SC.get('scoring_alert_threshold')} | "
          f"ER {SC.get('er_trend')}/{SC.get('er_min')} | "
          f"tp1 {ER.get('tp1_pct')}/{ER.get('tp1_ratio')} tp2 {ER.get('tp2_pct')}/{ER.get('tp2_ratio')} sl {ER.get('sl_pct')}")
    r = run_core(DB_SYM, SC, PR, ER, tf="1h", candles=CANDLES)
    a, i, o = r["all"], r["is"], r["oos"]
    print(f"  全样本: n={a['n']}  收益={a['ret_pct']:+.2f}%  PF={a['pf']:.2f}  胜率={a['winrate']:.1f}%  DD={r['max_dd']:.1f}%")
    print(f"  IS(前70%): n={i['n']}  收益={i['ret_pct']:+.2f}%")
    print(f"  OOS(后30%): n={o['n']}  收益={o['ret_pct']:+.2f}%  PF_oos={o['pf']:.2f}\n")
