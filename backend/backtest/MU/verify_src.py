# -*- coding: utf-8 -*-
"""
核实 MU 放宽档下发可行性：API 不支持改 src，故对比
  A) 期望口径  ST 20x3.0 close（回测得出的放宽档最优）
  B) 实际口径  ST 20x3.0 hl2（src 改不了，线上只能保持 hl2）
并给出 period 敏感性（18/20 在 hl2 下的差异）。
"""
import os
import sys
import json
import copy

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
BACKTEST = os.path.dirname(HERE)
BACKEND = os.path.dirname(BACKTEST)
sys.path.insert(0, BACKTEST)
from engine import run_core, _extract, _load_tf

DB_SYM = "MU-USDT"
TS_2026 = 1767225600000
FEE_T, FEE_M = 0.1, 0.04

CANDLES = {tf: [c for c in _load_tf(DB_SYM, tf) if c["ts"] >= TS_2026]
           for tf in ["15m", "1h", "4h"]}

with open(os.path.join(HERE, "mu_live_config_opt_config.json"), encoding="utf-8") as f:
    BASE = json.load(f)          # 严格档（已含 range 闸门 / ER / 出场）
BASE_SC, BASE_PR, BASE_ER = _extract(BASE)


def ev(pr=None, sc=None, er=None):
    p = copy.deepcopy(BASE_PR); s = copy.deepcopy(BASE_SC); e = copy.deepcopy(BASE_ER)
    if pr: p.update(pr)
    if sc: s.update(sc)
    if er: e.update(er)
    r = run_core(DB_SYM, s, p, e, tf="1h", candles=CANDLES)
    a, i, o = r["all"], r["is"], r["oos"]
    if not a or not o:
        return None
    return {"n": a["n"], "ret": a["ret_pct"], "pf": a["pf"], "wr": a["winrate"],
            "is": i["ret_pct"], "oos": o["ret_pct"], "oos_pf": o["pf"], "oos_n": o["n"],
            "dd": r["max_dd"],
            "net_t": a["ret_pct"] - FEE_T * a["n"],
            "net_m": a["ret_pct"] - FEE_M * a["n"]}


def show(tag, m):
    if m is None:
        print(f"  {tag:<28} 无交易")
        return
    ok = "Y" if (m["oos_pf"] > 1 and m["dd"] <= 25 and m["net_t"] > 0) else "n"
    print(f"  [{ok}] {tag:<26} 全={m['ret']:+7.2f}% n={m['n']:>3} DD={m['dd']:5.1f}% "
          f"IS={m['is']:+7.2f}% OOS={m['oos']:+6.2f}% PF_oos={m['oos_pf']:.2f} "
          f"胜率={m['wr']:.1f}% | taker净={m['net_t']:+7.2f}% maker净={m['net_m']:+7.2f}%")


print("=== MU src 敏感性核实（其余参数=放行档最优：range闸门开/ER 0.2-0.1/出场tp1 1.5-40 等）===")
print("\n[放宽档期望组合 periods=20]")
show("ST 20x3.0 close (回测最优)", ev({"periods": 20, "multiplier": 3.0, "src": "close"}))
show("ST 20x3.0 hl2 (src改不了)", ev({"periods": 20, "multiplier": 3.0, "src": "hl2"}))

print("\n[严格档组合 periods=18, src 与线上一致=hl2]")
show("ST 18x3.0 hl2 (严格档)", ev({"periods": 18, "multiplier": 3.0, "src": "hl2"}))
show("ST 18x3.0 close", ev({"periods": 18, "multiplier": 3.0, "src": "close"}))

print("\n[hl2 下 periods 邻域扫描（线上只能 hl2）]")
for p in [16, 17, 18, 19, 20, 21, 22]:
    show(f"ST {p}x3.0 hl2", ev({"periods": p, "multiplier": 3.0, "src": "hl2"}))

print("\n[线上原始基准（未寻优）]")
with open(os.path.join(HERE, "mu_live_config.json"), encoding="utf-8") as f:
    LIVE = json.load(f)
lsc, lpr, ler = _extract(LIVE)
B = dict(BASE_SC); B.update({k: v for k, v in lsc.items()})
E = dict(BASE_ER); E.update(ler)
# 完全按线上原始参数
def ev_raw(sc, pr, er):
    r = run_core(DB_SYM, sc, pr, er, tf="1h", candles=CANDLES)
    a, i, o = r["all"], r["is"], r["oos"]
    return {"n": a["n"], "ret": a["ret_pct"], "pf": a["pf"], "wr": a["winrate"],
            "is": i["ret_pct"], "oos": o["ret_pct"], "oos_pf": o["pf"], "oos_n": o["n"],
            "dd": r["max_dd"], "net_t": a["ret_pct"] - FEE_T * a["n"],
            "net_m": a["ret_pct"] - FEE_M * a["n"]}
show("线上当前 18x3.0 hl2", ev_raw(lsc, lpr, ler))
