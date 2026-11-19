# -*- coding: utf-8 -*-
"""ETH 1h线 2026年综合寻优，然后用最优参数回测2025年验证稳定性"""
import os, sys, json, copy
HERE = os.path.dirname(os.path.abspath(__file__))
BACKTEST = os.path.dirname(HERE)
sys.path.insert(0, BACKTEST)
from engine import run_core, _extract, _load_tf
from datetime import datetime

CONFIG = os.path.join(HERE, "eth_config.json")
DB_SYM = "ETH-USDT"  # 数据库中的实际名称
TS_2025 = 1735660800000  # 2025-01-01 UTC
TS_2026 = 1767196800000  # 2026-01-01 UTC
OOS_N = 10

with open(CONFIG, encoding="utf-8") as f:
    BASE = json.load(f)
BASE_SC, BASE_PR, BASE_ER = _extract(BASE)

# 加载2026年数据用于寻优
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
                "is_ret": 0, "oos_ret": 0, "oos_pf": 0, "oos_n": 0, "dd": r["max_dd"]}
    return {"ok": True, "n": a["n"], "ret": a["ret_pct"], "pf": a["pf"], "wr": a["winrate"],
            "is_ret": i["ret_pct"], "oos_ret": o["ret_pct"], "oos_pf": o["pf"],
            "oos_n": o["n"], "dd": r["max_dd"]}

def pass_filter(m):
    if not m.get("ok"):
        return False
    if m["n"] < 10:
        return False
    if m["oos_n"] < OOS_N:
        return False
    if m["oos_pf"] <= 1.0:
        return False
    if m["dd"] > 15:
        return False
    return True

def _eff(m):
    n = max(m["n"], 1)  # 避免除零
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
        flag = "✓" if pass_filter(m) else " "
        eff = m["ret"] / max(m["n"], 1)
        print(f"  [{flag}] {label}={str(k):>22}  OOS={m['oos_ret']:+.2f}% PF_oos={m['oos_pf']:.2f} "
              f"IS={m['is_ret']:+.2f}% 全={m['ret']:+.2f}% DD={m['dd']:.1f}% n={m['n']} 每笔={eff:+.3f}%")
    ranked = rank(scores)
    best = ranked[0][0]
    pr, sc, er = build(best)
    state["pr"].update(pr); state["sc"].update(sc); state["er"].update(er)
    e2 = _eff(ev())
    print(f"  -> 选定 {label}: {best}  (累积 每笔={e2[0]:+.3f}% 全={ev()['ret']:+.2f}% DD={ev()['dd']:.1f}% OOS={ev()['oos_ret']:+.2f}%)\n")
    return ranked

# === 1) ST参数扫描 ===
print("=== 1) ST 扫描 (periods × multiplier × src) ===")
ST_PERIODS = [7, 9, 11, 13, 15, 18]
ST_MULT = [2.0, 2.5, 3.0, 3.5]
ST_SRC = ["hl2", "close"]
def build_st(k):
    per, mu, src = k
    return ({"periods": per, "multiplier": mu, "src": src, "change_atr": True}, {}, {})
st_rank = scan("ST", [(p, m, s) for p in ST_PERIODS for m in ST_MULT for s in ST_SRC], build_st)

# === 2) 闸门扫描 ===
print("=== 2) 闸门扫描 (full=half × alert) ===")
G_FULL = [40, 50, 60, 70, 80]
G_ALERT = [40, 50, 55, 60, 70]
def build_gate(k):
    f, al = k
    return ({}, {"scoring_full_threshold": float(f), "scoring_half_threshold": float(f),
                "scoring_alert_threshold": float(al)}, {})
gate_rank = scan("gate", [(f, a) for f in G_FULL for a in G_ALERT], build_gate)

# === 3) 出场 tp 扫描 ===
print("=== 3) 出场 tp 扫描 (tp1_pct × tp1_ratio × tp2_pct × tp2_ratio) ===")
TP1_PCT = [1.0, 1.2, 1.5]
TP1_R = [30.0, 40.0, 50.0]
TP2_PCT = [1.5, 2.0, 2.5, 3.0]
TP2_R = [40.0, 50.0]
def build_tp(k):
    t1, t1r, t2, t2r = k
    return ({}, {}, {"tp1_pct": t1, "tp1_ratio": t1r, "tp2_pct": t2, "tp2_ratio": t2r})
tp_rank = scan("tp", [(a, b, c, d) for a in TP1_PCT for b in TP1_R for c in TP2_PCT for d in TP2_R], build_tp)

# === 4) 出场 sl/trail 扫描 ===
print("=== 4) 出场 sl/trail 扫描 (sl_pct × trail_with_st) ===")
SL_PCT = [2.0, 2.5, 3.0, 3.5]
TRAIL = [True, False]
def build_sl(k):
    sl, tr = k
    return ({}, {}, {"sl_pct": sl, "trail_with_st": tr})
sl_rank = scan("sl", [(s, t) for s in SL_PCT for t in TRAIL], build_sl)

# === 5) ER 扫描 ===
print("=== 5) ER 扫描 (er_trend × er_min) ===")
ER_TREND = sorted(set([0.2, 0.25, 0.3, 0.35, BASE_SC.get("er_trend", 0.25)]))
ER_MIN = sorted(set([0.1, 0.12, 0.15, BASE_SC.get("er_min", 0.25)]))
def build_er(k):
    tr, mn = k
    return ({}, {"er_trend": tr, "er_min": mn, "er_weak_min": mn}, {})
er_rank = scan("ER", [(t, m) for t in ER_TREND for m in ER_MIN], build_er)
