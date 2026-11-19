# -*- coding: utf-8 -*-
"""ETH 1h线 2026年综合寻优，然后用最优参数回测2025年验证稳定性"""
import os, sys, json, copy
HERE = os.path.dirname(os.path.abspath(__file__))
BACKTEST = os.path.dirname(HERE)
sys.path.insert(0, BACKTEST)
from engine import run_core, _extract, _load_tf
from datetime import datetime

CONFIG = os.path.join(HERE, "eth_config.json")
DB_SYM = "ETH-USDT"
TS_2025 = 1735660800000
TS_2026 = 1767196800000
OOS_N = 10

with open(CONFIG, encoding="utf-8") as f:
    BASE = json.load(f)
BASE_SC, BASE_PR, BASE_ER = _extract(BASE)

print("正在加载数据...")
try:
    CANDLES_2026 = {tf: [c for c in _load_tf(DB_SYM, tf) if c["ts"] >= TS_2026]
                    for tf in ["15m", "1h", "4h"]}
    CANDLES_2025 = {tf: [c for c in _load_tf(DB_SYM, tf) if TS_2025 <= c["ts"] < TS_2026]
                    for tf in ["15m", "1h", "4h"]}
except Exception as ex:
    print(f"[警告] 加载多周期失败: {ex}，仅用1h")
    CANDLES_2026 = {"1h": [c for c in _load_tf(DB_SYM, "1h") if c["ts"] >= TS_2026]}
    CANDLES_2025 = {"1h": [c for c in _load_tf(DB_SYM, "1h") if TS_2025 <= c["ts"] < TS_2026]}

N_2026 = len(CANDLES_2026["1h"])
N_2025 = len(CANDLES_2025["1h"])
print(f"[数据] {DB_SYM} 1h | 2026: {N_2026}根 | 2025: {N_2025}根")
print(f"[目标] 硬约束: PF_oos>1 & OOS笔数>={OOS_N} & DD<=15%; 软目标: 最大化每笔收益效率(ret/n)")
print()

def evaluate(pr=None, sc=None, er=None, candles=None):
    p = copy.deepcopy(BASE_PR); s = copy.deepcopy(BASE_SC); e = copy.deepcopy(BASE_ER)
    if pr: p.update(pr)
    if sc: s.update(sc)
    if er: e.update(er)
    try:
        r = run_core(DB_SYM, s, p, e, tf="1h", candles=candles)
    except Exception as ex:
        return {"ok": False, "err": str(ex), "n": 0, "ret": 0, "pf": 0, "wr": 0,
                "is_ret": 0, "oos_ret": 0, "oos_pf": 0, "oos_n": 0, "dd": 999}
    a, i, o = r["all"], r["is"], r["oos"]
    if not a or not o:
        return {"ok": True, "n": 0, "ret": 0, "pf": 0, "wr": 0,
                "is_ret": 0, "oos_ret": 0, "oos_pf": 0, "oos_n": 0, "dd": r.get("max_dd", 0)}
    return {"ok": True, "n": a["n"], "ret": a["ret_pct"], "pf": a["pf"], "wr": a["winrate"],
            "is_ret": i["ret_pct"], "oos_ret": o["ret_pct"], "oos_pf": o["pf"],
            "oos_n": o["n"], "dd": r.get("max_dd", 0)}

def pass_filter(m):
    return (m.get("ok") and m["n"] >= 10 and m["oos_n"] >= OOS_N and 
            m["oos_pf"] > 1.0 and m["dd"] <= 15)

def _eff(m):
    n = max(m["n"], 1)
    return (m["ret"] / n, m["ret"] / max(m["dd"], 0.1))

def rank(scores):
    good = [x for x in scores if pass_filter(x[1])]
    pool = good if good else scores
    pool.sort(key=lambda x: (1 if pass_filter(x[1]) else 0,
                             x[1]["oos_ret"], _eff(x[1])[0], _eff(x[1])[1]),
              reverse=True)
    return pool

state = {"pr": {}, "sc": {}, "er": {}}

def ev(pr=None, sc=None, er=None, candles=CANDLES_2026):
    p = {**state["pr"], **(pr or {})}
    s = {**state["sc"], **(sc or {})}
    e = {**state["er"], **(er or {})}
    return evaluate(p, s, e, candles)

def scan(label, keys, build):
    scores = []
    for k in keys:
        pr, sc, er = build(k)
        m = ev(pr, sc, er)
        scores.append((k, m))
        flag = "Y" if pass_filter(m) else " "
        eff = m["ret"] / max(m["n"], 1)
        print(f"  [{flag}] {label}={str(k):>22}  OOS={m['oos_ret']:+.2f}% PF_oos={m['oos_pf']:.2f} "
              f"IS={m['is_ret']:+.2f}% 全={m['ret']:+.2f}% DD={m['dd']:.1f}% n={m['n']} 每笔={eff:+.3f}%")
    ranked = rank(scores)
    best = ranked[0][0]
    pr, sc, er = build(best)
    state["pr"].update(pr); state["sc"].update(sc); state["er"].update(er)
    e2 = _eff(ev())
    print(f"  -> 选定 {label}: {best}  (累积 每笔={e2[0]:+.3f}% 全={ev()['ret']:+.2f}% DD={ev()['dd']:.1f}% OOS={ev()['oos_ret']:+.2f}%)" + chr(10))
    return ranked
