# -*- coding: utf-8 -*-
"""AMD: 对比 tp1=3.0%/50%(当前) vs tp1=1.5%/30%(改) 的回测结果。
其余参数不变（tp2/tp3=100/0 无效、sl=st 2%、闸门30/30/30）。"""
import os, sys, json, copy
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "backtest"))
from engine import run_core, _extract, _load_tf

CONFIG = "backtest/amd/amd_config.json"
with open(CONFIG, encoding="utf-8") as f:
    BASE = json.load(f)
SC, PR, ER = _extract(BASE)

DB_SYM = None
for cand in ["AMD-USDT", "AMD-USDT-SWAP"]:
    try:
        if _load_tf(cand, "1h"):
            DB_SYM = cand
            break
    except Exception:
        pass

TS = 1767225600000  # 2026 窗口（本地 AMD 仅此段有数据）
CANDLES = {tf: [c for c in _load_tf(DB_SYM, tf) if c["ts"] >= TS]
           for tf in ["15m", "1h", "4h"]}
print(f"本地 AMD db={DB_SYM}  1h={len(CANDLES['1h'])} 15m={len(CANDLES['15m'])} 4h={len(CANDLES['4h'])} (2026窗口)")


def run(er_override, label):
    e = copy.deepcopy(ER)
    e.update(er_override)
    r = run_core(DB_SYM, SC, PR, e, tf="1h", candles=CANDLES)
    a, i, o = r["all"], r["is"], r["oos"]
    print(f"\n[{label}] tp1={e.get('tp1_pct')}%/{e.get('tp1_ratio')}% "
          f"(tp2/tp3={e.get('tp2_pct')}/{e.get('tp2_ratio')},{e.get('tp3_pct')}/{e.get('tp3_ratio')} mode={e.get('tp3_mode')})")
    print(f"  全样本: n={a['n']}  收益={a['ret_pct']:+.2f}%  PF={a['pf']:.2f}  胜率={a['winrate']:.1f}%  DD={r['max_dd']:.1f}%")
    print(f"  IS(前70%): n={i['n']}  {i['ret_pct']:+.2f}%    OOS(后30%): n={o['n']}  {o['ret_pct']:+.2f}%  PF_oos={o['pf']:.2f}")


run({}, "当前  tp1=3.0%/50%")
run({"tp1_pct": 1.5, "tp1_ratio": 30.0}, "改后  tp1=1.5%/30%")
