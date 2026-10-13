# -*- coding: utf-8 -*-
"""INTC 补充研究: 在原 pct 出场结构(保留OOS正的关键)下, 仅扫 ST源×闸门,
寻找"OOS仍正 + 回撤尽量低"的降回撤方案。不动出场逻辑, 避免触发OOS失效。"""
import os, sys, json, copy
HERE = os.path.dirname(os.path.abspath(__file__))
BACKTEST = os.path.dirname(HERE)
sys.path.insert(0, BACKTEST)
from engine import run_core, _extract, _load_tf

CONFIG = os.path.join(HERE, "intc_config.json")
DB_SYM = "INTC-USDT"
with open(CONFIG, encoding="utf-8") as f:
    BASE = json.load(f)
ORIG_ER = copy.deepcopy(BASE["exit_rules"])   # 原 pct 4% 出场, OOS正的来源
BASE_SC, BASE_PR, BASE_ER = _extract(BASE)
CANDLES = {"15m": _load_tf(DB_SYM, "15m"), "1h": _load_tf(DB_SYM, "1h"), "4h": _load_tf(DB_SYM, "4h")}

def evaluate(pr=None, sc=None, er=None):
    p = copy.deepcopy(BASE_PR); s = copy.deepcopy(BASE_SC); e = copy.deepcopy(BASE_ER)
    if pr: p.update(pr)
    if sc: s.update(sc)
    if er: e.update(er)
    r = run_core(DB_SYM, s, p, e, tf="1h", candles=CANDLES)
    a, i, o = r["all"], r["is"], r["oos"]
    if not a or not o:
        return {"ok": True, "n": 0, "ret": 0, "pf": 0, "wr": 0,
                "is_ret": 0, "oos_ret": 0, "oos_pf": 0, "oos_n": 0, "dd": r["max_dd"]}
    return {"ok": True, "n": a["n"], "ret": a["ret_pct"], "pf": a["pf"], "wr": a["winrate"],
            "is_ret": i["ret_pct"], "oos_ret": o["ret_pct"], "oos_pf": o["pf"],
            "oos_n": o["n"], "dd": r["max_dd"]}

print("=== INTC 原 pct 出场结构下, 扫 ST源×闸门 (目标: OOS正 & 回撤尽量低) ===")
STS = [(15, 3.0, "hl2"), (13, 3.0, "hl2"), (11, 3.0, "hl2"),
       (15, 3.0, "close"), (13, 3.0, "close"), (11, 3.0, "close")]
GATE = [30, 40, 50, 60, 70, 80]
ALERT = [40, 50, 60, 70]
best = None
for st in STS:
    for gf in GATE:
        for gal in ALERT:
            pr = {"periods": st[0], "multiplier": st[1], "src": st[2], "change_atr": True}
            sc = {"scoring_full_threshold": float(gf), "scoring_half_threshold": float(gf),
                  "scoring_alert_threshold": float(gal)}
            m = evaluate(pr, sc, ORIG_ER)
            mark = "✓" if (m["oos_ret"] > 0 and m["dd"] <= 20) else " "
            eff = m["ret"] / max(m["n"], 1)
            print(f"  [{mark}] ST={st} gate={gf}/{gal} OOS={m['oos_ret']:+.2f}% PFo={m['oos_pf']:.2f} "
                  f"IS={m['is_ret']:+.2f}% 全={m['ret']:+.2f}% DD={m['dd']:.1f}% n={m['n']} 每笔={eff:+.3f}%")
            if m["oos_ret"] > 0:
                score = (-m["dd"], eff)   # OOS正前提下, 优先低回撤
                if best is None or score < best[0]:
                    best = (score, st, gf, gal, m)
print("\n=== OOS正前提下回撤最低的方案 ===")
if best:
    sc_, st, gf, gal, m = best
    print(f"  ST {st} gate {gf}/{gal}  OOS={m['oos_ret']:+.2f}% PFo={m['oos_pf']:.2f} "
          f"IS={m['is_ret']:+.2f}% 全={m['ret']:+.2f}% DD={m['dd']:.1f}% n={m['n']} 每笔={m['ret']/m['n']:+.3f}%")
else:
    print("  无 OOS 正的组合")
