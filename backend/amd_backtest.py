# -*- coding: utf-8 -*-
"""AMD 当前线上参数回测（engine.run_core 同 ETH 验证口径）。
读 backtest/amd/amd_config.json，跑 2026窗口 + 2025长窗口。"""
import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "backtest"))
from engine import run_core, _extract, _load_tf

CONFIG = "backtest/amd/amd_config.json"
with open(CONFIG, encoding="utf-8") as f:
    BASE = json.load(f)
SC, PR, ER = _extract(BASE)

# 探测本地 db 的 AMD symbol 命名
DB_SYM = None
for cand in ["AMD-USDT", "AMD-USDT-SWAP"]:
    try:
        if _load_tf(cand, "1h"):
            DB_SYM = cand
            break
    except Exception:
        pass
print(f"本地 AMD db symbol = {DB_SYM}")

print("AMD 当前参数: ST %sx%s %s | gates %s/%s/%s | ER %s/%s/%s | "
      "tp1 %s/%s tp2 %s/%s tp3 %s/%s mode=%s | sl=%s %s%% trail=%s mv=%s" % (
    PR.get("periods"), PR.get("multiplier"), PR.get("src"),
    SC.get("scoring_full_threshold"), SC.get("scoring_half_threshold"), SC.get("scoring_alert_threshold"),
    SC.get("er_trend"), SC.get("er_min"), SC.get("er_weak_min"),
    ER.get("tp1_pct"), ER.get("tp1_ratio"), ER.get("tp2_pct"), ER.get("tp2_ratio"),
    ER.get("tp3_pct"), ER.get("tp3_ratio"), ER.get("tp3_mode"),
    ER.get("sl_mode"), ER.get("sl_pct"), ER.get("trail_with_st"), ER.get("move_sl_to_entry")))

for label, TS in [("2026窗口", 1767225600000), ("2025长窗口", 1735689600000)]:
    if not DB_SYM:
        print(f"\n===== {label}: ⚠ 本地无 AMD 数据，跳过 =====")
        continue
    CANDLES = {tf: [c for c in _load_tf(DB_SYM, tf) if c["ts"] >= TS]
               for tf in ["15m", "1h", "4h"]}
    n1h = len(CANDLES["1h"])
    print(f"\n===== {label}: 1h {n1h} 根; 15m {len(CANDLES['15m'])}; 4h {len(CANDLES['4h'])} =====")
    if n1h == 0:
        print("  ⚠ 无 1h 数据，跳过")
        continue
    r = run_core(DB_SYM, SC, PR, ER, tf="1h", candles=CANDLES)
    a, i, o = r["all"], r["is"], r["oos"]
    print(f"  全样本: n={a['n']}  收益={a['ret_pct']:+.2f}%  PF={a['pf']:.2f}  胜率={a['winrate']:.1f}%  DD={r['max_dd']:.1f}%")
    print(f"  IS(前70%): n={i['n']}  收益={i['ret_pct']:+.2f}%")
    print(f"  OOS(后30%): n={o['n']}  收益={o['ret_pct']:+.2f}%  PF_oos={o['pf']:.2f}")
