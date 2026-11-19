# -*- coding: utf-8 -*-
"""
综合寻优 v5（扩展网格，通用版）

覆盖维度（包括但不限于）:
  1) ST 数值     periods × multiplier × src
  2) 闸门评分     scoring_full/half × alert
  3) 出场 tp      tp1_pct × tp1_ratio × tp2_pct × tp2_ratio
  4) 出场 sl      sl_pct × trail_with_st
  5) tp3          tp3_pct
  6) sl 细节      sl_buffer_atr × sl_min_pct
  7) 保护利润      protect_profit_at × protect_trail_pct
  8) ER          er_trend × (er_min, er_weak_min)
  9) 四大闸门     atr / range / mtf / adx（开关 + 关键参数）

硬约束（严格档）: PF_oos>1 & OOS笔数>=10 & DD<=15% & 扣费后净收益>0
放宽档:          PF_oos>1 & OOS笔数>=6  & DD<=25% & 扣费后净收益>0
手续费: taker 0.05%×2 = 0.1% 名义/笔（同时报告 maker 0.04%/笔）
目标:   达标集合内最大化 扣费后每笔净收益效率 = (ret - 0.1*n)/n

用法: python optimize_ext.py <CONFIG路径> <DB_SYM>
  例: python optimize_ext.py backtest/TSLA/tsla_live_config.json TSLA-USDT
"""
import os
import sys
import json
import copy

sys.stdout.reconfigure(encoding="utf-8")

HERE0 = os.path.dirname(os.path.abspath(__file__))
BACKTEST = os.path.dirname(HERE0)
BACKEND = os.path.dirname(BACKTEST)
sys.path.insert(0, BACKTEST)
from engine import run_core, _extract, _load_tf

if len(sys.argv) < 3:
    print(__doc__)
    sys.exit(1)

CONFIG = os.path.abspath(sys.argv[1])
DB_SYM = sys.argv[2]
TS_2026 = 1767225600000
HERE = os.path.dirname(CONFIG)

FEE_T = 0.1     # taker 双边 0.1% 名义/笔
FEE_M = 0.04    # maker 双边 0.04% 名义/笔
STRICT = dict(oos_n=10, dd=15.0)
RELAX = dict(oos_n=6, dd=25.0)

with open(CONFIG, encoding="utf-8") as f:
    BASE = json.load(f)
BASE_SC, BASE_PR, BASE_ER = _extract(BASE)

CANDLES = {tf: [c for c in _load_tf(DB_SYM, tf) if c["ts"] >= TS_2026]
           for tf in ["15m", "1h", "4h"]}
N1H = len(CANDLES["1h"])
print(f"[数据] {DB_SYM} 1h(2026窗口) {N1H} 根 | 15m {len(CANDLES['15m'])} | 4h {len(CANDLES['4h'])}")
print(f"[约束] 严格档: PF_oos>1 & OOS>={STRICT['oos_n']} & DD<={STRICT['dd']}% & 扣费净>0")
print(f"[约束] 放宽档: PF_oos>1 & OOS>={RELAX['oos_n']} & DD<={RELAX['dd']}% & 扣费净>0")


def evaluate(pr=None, sc=None, er=None):
    p = copy.deepcopy(BASE_PR); s = copy.deepcopy(BASE_SC); e = copy.deepcopy(BASE_ER)
    if pr: p.update(pr)
    if sc: s.update(sc)
    if er: e.update(er)
    try:
        r = run_core(DB_SYM, s, p, e, tf="1h", candles=CANDLES)
    except Exception as ex:
        return {"ok": False, "err": str(ex), "n": 0, "ret": 0, "pf": 0, "wr": 0,
                "is_ret": 0, "oos_ret": 0, "oos_pf": 0, "oos_n": 0, "dd": 999}
    a, i, o = r["all"], r["is"], r["oos"]
    if not a or not o:
        return {"ok": True, "n": 0, "ret": 0, "pf": 0, "wr": 0, "is_ret": 0,
                "oos_ret": 0, "oos_pf": 0, "oos_n": 0, "dd": r["max_dd"]}
    return {"ok": True, "n": a["n"], "ret": a["ret_pct"], "pf": a["pf"], "wr": a["winrate"],
            "is_ret": i["ret_pct"], "oos_ret": o["ret_pct"], "oos_pf": o["pf"],
            "oos_n": o["n"], "dd": r["max_dd"]}


def net_t(m):
    return m["ret"] - FEE_T * m["n"]


def net_m(m):
    return m["ret"] - FEE_M * m["n"]


def pass_filter(m, lv=STRICT):
    if not m.get("ok") or m["n"] < 10:
        return False
    if m["oos_n"] < lv["oos_n"] or m["oos_pf"] <= 1.0:
        return False
    if m["dd"] > lv["dd"]:
        return False
    return net_t(m) > 0


def eff(m):
    return net_t(m) / max(m["n"], 1)


def grade(m):
    return 2 if pass_filter(m, STRICT) else (1 if pass_filter(m, RELAX) else 0)


def rank(scores):
    return sorted(scores, key=lambda x: (grade(x[1]), eff(x[1]),
                                         x[1]["ret"] / max(x[1]["dd"], 0.1)),
                  reverse=True)


state = {"pr": {}, "sc": {}, "er": {}}


def ev(pr=None, sc=None, er=None):
    p = {**state["pr"], **(pr or {})}
    s = {**state["sc"], **(sc or {})}
    e = {**state["er"], **(er or {})}
    return evaluate(p, s, e)


def scan(label, keys, build):
    scores = []
    for k in keys:
        pr, sc, er = build(k)
        m = ev(pr, sc, er)
        scores.append((k, m))
        flag = {2: "Y", 1: "y", 0: " "}[grade(m)]
        print(f"  [{flag}] {label}={str(k):>26}  OOS={m['oos_ret']:+7.2f}% PF={m['oos_pf']:.2f} "
              f"IS={m['is_ret']:+7.2f}% 全={m['ret']:+7.2f}% DD={m['dd']:5.1f}% "
              f"n={m['n']:>3} 净%={net_t(m):+7.2f} 每笔净={eff(m):+.3f}%")
    ranked = rank(scores)
    best = ranked[0][0]
    pr, sc, er = build(best)
    state["pr"].update(pr); state["sc"].update(sc); state["er"].update(er)
    e2 = ev()
    print(f"  -> 选定 {label}: {best}  (累积 全={e2['ret']:+.2f}% 净%={net_t(e2):+.2f} "
          f"DD={e2['dd']:.1f}% OOS={e2['oos_ret']:+.2f}% 档={grade(e2)})")
    return ranked


print("\n=== 1) ST 扫描 (periods × multiplier × src) ===")
def build_st(k):
    p, m, s = k
    return ({"periods": p, "multiplier": m, "src": s, "change_atr": True}, {}, {})
st_rank = scan("ST", [(p, m, s) for p in [7, 9, 11, 13, 15, 18, 20]
                      for m in [2.0, 2.5, 3.0, 3.5, 4.0]
                      for s in ["hl2", "close"]], build_st)

print("\n=== 2) 闸门评分扫描 (full=half × alert) ===")
def build_gate(k):
    f, a = k
    return ({}, {"scoring_full_threshold": float(f), "scoring_half_threshold": float(f),
                 "scoring_alert_threshold": float(a)}, {})
gate_rank = scan("gate", [(f, a) for f in [40, 50, 60, 70, 80, 90]
                          for a in [40, 50, 60, 70]], build_gate)

print("\n=== 3) 出场 tp 扫描 (tp1_pct × tp1_ratio × tp2_pct × tp2_ratio) ===")
def build_tp(k):
    a, b, c, d = k
    return ({}, {}, {"tp1_pct": a, "tp1_ratio": b, "tp2_pct": c, "tp2_ratio": d})
tp_rank = scan("tp", [(a, b, c, d) for a in [0.8, 1.0, 1.2, 1.5]
                      for b in [30.0, 40.0]
                      for c in [1.5, 2.0, 2.5, 3.0]
                      for d in [40.0, 50.0]], build_tp)

print("\n=== 4) 出场 sl 扫描 (sl_pct × trail_with_st) ===")
def build_sl(k):
    s, t = k
    return ({}, {}, {"sl_pct": s, "trail_with_st": t})
sl_rank = scan("sl", [(s, t) for s in [1.5, 2.0, 2.5, 3.0, 3.5]
                      for t in [True, False]], build_sl)

print("\n=== 5) tp3 扫描 ===")
def build_tp3(k):
    return ({}, {}, {"tp3_pct": k})
tp3_rank = scan("tp3", [2.5, 3.0, 3.5, 4.0, 5.0], build_tp3)

print("\n=== 6) sl 细节扫描 (sl_buffer_atr × sl_min_pct) ===")
def build_buf(k):
    b, s = k
    return ({}, {}, {"sl_buffer_atr": b, "sl_min_pct": s})
buf_rank = scan("slbuf", [(b, s) for b in [0.3, 0.5, 0.8]
                          for s in [0.8, 1.2, 1.6]], build_buf)

print("\n=== 7) 保护利润扫描 (protect_profit_at × protect_trail_pct) ===")
def build_prot(k):
    a, t = k
    return ({}, {}, {"protect_profit_at": a, "protect_trail_pct": t})
prot_rank = scan("protect", [(a, t) for a in [1.0, 1.5, 2.0]
                             for t in [0.5, 0.8, 1.2]], build_prot)

print("\n=== 8) ER 扫描 (er_trend × er_min/er_weak_min) ===")
def build_er(k):
    t, m = k
    return ({}, {"er_trend": t, "er_min": m, "er_weak_min": m}, {})
er_rank = scan("ER", [(t, m) for t in [0.2, 0.25, 0.3, 0.35]
                      for m in [0.1, 0.12, 0.15, 0.2, 0.25]], build_er)

print("\n=== 9) 四大闸门扫描 (atr / range / mtf / adx) ===")
def build_atr(k):
    en, v = k
    return ({}, {"atr_filter_enabled": en, "atr_vol_min": v}, {})
atr_rank = scan("atr", [(False, 0.75), (True, 0.6), (True, 0.75), (True, 0.9)], build_atr)

def build_rng(k):
    en, sz, tc = k
    return ({}, {"range_filter_enabled": en, "range_size_max": sz, "range_touches_min": tc}, {})
rng_rank = scan("range", [(False, 0.15, 2), (True, 0.08, 2), (True, 0.12, 2),
                          (True, 0.15, 2), (True, 0.12, 3)], build_rng)

def build_mtf(k):
    en, c = k
    return ({}, {"mtf_filter_enabled": en, "mtf_consistency_min": c}, {})
mtf_rank = scan("mtf", [(False, 0.6), (True, 0.5), (True, 0.6), (True, 0.7)], build_mtf)

def build_adx(k):
    en, v = k
    return ({}, {"adx_filter_enabled": en, "adx_min": v}, {})
adx_rank = scan("adx", [(False, 20.0), (True, 15.0), (True, 20.0), (True, 25.0)], build_adx)

cd = evaluate(state["pr"], state["sc"], state["er"])
print(f"\n=== 坐标下降拼合 ===  OOS={cd['oos_ret']:+.2f}% PF_oos={cd['oos_pf']:.2f} "
      f"IS={cd['is_ret']:+.2f}% 全={cd['ret']:+.2f}% DD={cd['dd']:.1f}% n={cd['n']} "
      f"净%={net_t(cd):+.2f} 每笔净={eff(cd):+.3f}% 档={grade(cd)}")


def topk(ranked, k=3):
    return [x[0] for x in ranked[:k]]


st_top = topk(st_rank, 3)
gate_top = topk(gate_rank, 2)
tp_top = topk(tp_rank, 3)
sl_top = topk(sl_rank, 2)
er_top = topk(er_rank, 2)
NCOMB = len(st_top) * len(gate_top) * len(tp_top) * len(sl_top) * len(er_top)
print(f"\n=== 10) 邻域笛卡尔积精搜 {len(st_top)}×{len(gate_top)}×{len(tp_top)}×{len(sl_top)}×{len(er_top)} "
      f"= {NCOMB} 组合 ===")
best_strict, best_relax = None, None
for per, mu, src in st_top:
    for gf, gal in gate_top:
        for t1, t1r, t2, t2r in tp_top:
            for sl, tr in sl_top:
                for ert, erm in er_top:
                    pr = {"periods": per, "multiplier": mu, "src": src, "change_atr": True}
                    sc = {"scoring_full_threshold": float(gf), "scoring_half_threshold": float(gf),
                          "scoring_alert_threshold": float(gal),
                          "er_trend": ert, "er_min": erm, "er_weak_min": erm}
                    sc.update({k: v for k, v in state["sc"].items()
                               if k.startswith(("atr_", "range_", "mtf_", "adx_"))})
                    er = {"tp1_pct": t1, "tp1_ratio": t1r, "tp2_pct": t2, "tp2_ratio": t2r,
                          "sl_pct": sl, "trail_with_st": tr}
                    er.update({k: v for k, v in state["er"].items()
                               if k in ("tp3_pct", "sl_buffer_atr", "sl_min_pct",
                                        "protect_profit_at", "protect_trail_pct")})
                    m = evaluate(pr, sc, er)
                    g = grade(m)
                    key = (eff(m), m["ret"] / max(m["dd"], 0.1))
                    if g == 2 and (best_strict is None or key > best_strict[0]):
                        best_strict = (key, m, (pr, sc, er))
                    elif g == 1 and (best_relax is None or key > best_relax[0]):
                        best_relax = (key, m, (pr, sc, er))


def show(tag, b):
    if b is None:
        print(f"  [{tag}] 无满足该档约束的组合")
        return None
    _, m, combo = b
    print(f"  [{tag}] ST {combo[0]['periods']}x{combo[0]['multiplier']} {combo[0]['src']} | "
          f"gate {combo[1]['scoring_full_threshold']:.0f}/{combo[1]['scoring_alert_threshold']:.0f} | "
          f"tp1 {combo[2]['tp1_pct']}/{combo[2]['tp1_ratio']:.0f} "
          f"tp2 {combo[2]['tp2_pct']}/{combo[2]['tp2_ratio']:.0f} | "
          f"sl {combo[2]['sl_pct']} trail {combo[2]['trail_with_st']} | "
          f"ER {combo[1]['er_trend']}/{combo[1]['er_min']}")
    print(f"        IS={m['is_ret']:+.2f}% OOS={m['oos_ret']:+.2f}% PF_oos={m['oos_pf']:.2f} "
          f"全={m['ret']:+.2f}% DD={m['dd']:.1f}% n={m['n']} 胜率={m['wr']:.1f}%")
    print(f"        扣费: taker 净={net_t(m):+.2f}% (每笔{eff(m):+.3f}%) | "
          f"maker 净={net_m(m):+.2f}%")
    return combo


print("\n精搜结果:")
c_strict = show("严格档", best_strict)
c_relax = show("放宽档", best_relax)

FINAL = c_strict or c_relax
if FINAL is None:
    print("\n⚠ 两档均无满足约束（含扣费净>0）的组合 —— 该品种在当前数据与策略框架下无净盈利解。")
    m_best = cd
    FINAL = (state["pr"], state["sc"], state["er"])
else:
    m_best = evaluate(*FINAL)

opt_cfg = copy.deepcopy(BASE)
opt_cfg["params"].update(FINAL[0])
for k, v in FINAL[1].items():
    if k in opt_cfg and k != "params":
        opt_cfg[k] = v
opt_cfg["exit_rules"].update(FINAL[2])
opt_path = os.path.join(HERE, os.path.splitext(os.path.basename(CONFIG))[0] + "_opt_config.json")
with open(opt_path, "w", encoding="utf-8") as f:
    json.dump(opt_cfg, f, ensure_ascii=False, indent=2)
print(f"\n[最优配置已写入] {opt_path}")

live = evaluate(BASE_PR, BASE_SC, BASE_ER)
print(f"\n[线上基准] ST {BASE_PR['periods']}x{BASE_PR['multiplier']} {BASE_PR['src']} "
      f"gate {BASE_SC['scoring_full_threshold']:.0f}/{BASE_SC['scoring_alert_threshold']:.0f} "
      f"ER {BASE_SC['er_trend']}/{BASE_SC['er_min']}")
print(f"        IS={live['is_ret']:+.2f}% OOS={live['oos_ret']:+.2f}% 全={live['ret']:+.2f}% "
      f"DD={live['dd']:.1f}% n={live['n']} 胜率={live['wr']:.1f}% | "
      f"taker净={net_t(live):+.2f}% maker净={net_m(live):+.2f}%")

report = [
    f"{DB_SYM} 综合寻优 v5（2026 窗口，扩展网格，扣费后每笔净收益效率优先）",
    f"数据: 1h(2026) {N1H} 根 | 15m {len(CANDLES['15m'])} | 4h {len(CANDLES['4h'])}",
    f"线上基准: 全={live['ret']:+.2f}% n={live['n']} DD={live['dd']:.1f}% "
    f"taker净={net_t(live):+.2f}% maker净={net_m(live):+.2f}%",
    f"坐标下降拼合: 全={cd['ret']:+.2f}% n={cd['n']} DD={cd['dd']:.1f}% 净%={net_t(cd):+.2f} 档={grade(cd)}",
    f"最终最优: 全={m_best['ret']:+.2f}% n={m_best['n']} DD={m_best['dd']:.1f}% "
    f"IS={m_best['is_ret']:+.2f}% OOS={m_best['oos_ret']:+.2f}% PF_oos={m_best['oos_pf']:.2f} "
    f"胜率={m_best['wr']:.1f}% taker净={net_t(m_best):+.2f}% maker净={net_m(m_best):+.2f}%",
    f"选用档位: {'严格档(DD<=15%)' if c_strict else ('放宽档(DD<=25%)' if c_relax else '无解/回退')}",
]
rp = os.path.join(HERE, os.path.splitext(os.path.basename(CONFIG))[0] + "_optimized_1h.txt")
with open(rp, "w", encoding="utf-8") as f:
    f.write("\n".join(report) + "\n")
print(f"[报告已写入] {rp}")
