# -*- coding: utf-8 -*-
"""
回撤约束下的总净收益寻优（通用）。

与 optimize_ext.py 的区别：
  - 目标改为【最大化 扣费后总净收益】，而不是每笔效率（后者偏向多笔薄利）
  - 回撤上限可配，用于"把某品种 DD 压进风控线、同时尽量保住收益"
  - 搜索空间包含线上常见的"少笔数 + 单笔全平"结构（gate 低至 30、tp2_ratio=0、tp2_pct=100）

约束: PF_oos>1 & OOS笔数>=OOS_N & DD<=MAXDD & 扣费(taker 0.1%/笔)后净>0
用法: python optimize_net.py <CONFIG> <DB_SYM> [MAXDD] [OOS_N]
  例: python optimize_net.py backtest/TSLA/tsla_live_config.json TSLA-USDT 15 10
"""
import os
import sys
import json
import copy

sys.stdout.reconfigure(encoding="utf-8")

HERE0 = os.path.dirname(os.path.abspath(__file__))
BACKTEST = os.path.dirname(HERE0)
sys.path.insert(0, BACKTEST)
from engine import run_core, _extract, _load_tf

if len(sys.argv) < 3:
    print(__doc__)
    sys.exit(1)

CONFIG = os.path.abspath(sys.argv[1])
DB_SYM = sys.argv[2]
MAXDD = float(sys.argv[3]) if len(sys.argv) > 3 else 15.0
OOS_N = int(sys.argv[4]) if len(sys.argv) > 4 else 10
TS_2026 = 1767225600000
HERE = os.path.dirname(CONFIG)
FEE_T, FEE_M = 0.1, 0.04

with open(CONFIG, encoding="utf-8") as f:
    BASE = json.load(f)
BASE_SC, BASE_PR, BASE_ER = _extract(BASE)
CANDLES = {tf: [c for c in _load_tf(DB_SYM, tf) if c["ts"] >= TS_2026]
           for tf in ["15m", "1h", "4h"]}
print(f"[数据] {DB_SYM} 1h(2026) {len(CANDLES['1h'])} | 15m {len(CANDLES['15m'])} | 4h {len(CANDLES['4h'])}")
print(f"[约束] PF_oos>1 & OOS>={OOS_N} & DD<={MAXDD}% & 扣费净>0   目标: 最大化总净收益")


def evaluate(pr=None, sc=None, er=None):
    p = copy.deepcopy(BASE_PR); s = copy.deepcopy(BASE_SC); e = copy.deepcopy(BASE_ER)
    if pr: p.update(pr)
    if sc: s.update(sc)
    if er: e.update(er)
    try:
        r = run_core(DB_SYM, s, p, e, tf="1h", candles=CANDLES)
    except Exception as ex:
        return {"ok": False, "n": 0, "ret": 0, "pf": 0, "wr": 0, "is_ret": 0,
                "oos_ret": 0, "oos_pf": 0, "oos_n": 0, "dd": 999}
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


def ok(m):
    return (m.get("ok") and m["n"] >= 10 and m["oos_n"] >= OOS_N
            and m["oos_pf"] > 1.0 and m["dd"] <= MAXDD and net_t(m) > 0)


def rank(scores):
    """达标优先，达标内按总净收益降序；全不达标则按 DD 升序兜底。"""
    good = [x for x in scores if ok(x[1])]
    if good:
        return sorted(good, key=lambda x: net_t(x[1]), reverse=True), True
    return sorted(scores, key=lambda x: x[1]["dd"]), False


state = {"pr": {}, "sc": {}, "er": {}}


def ev(pr=None, sc=None, er=None):
    return evaluate({**state["pr"], **(pr or {})},
                    {**state["sc"], **(sc or {})},
                    {**state["er"], **(er or {})})


def scan(label, keys, build):
    scores = []
    for k in keys:
        pr, sc, er = build(k)
        m = ev(pr, sc, er)
        scores.append((k, m))
        print(f"  [{'Y' if ok(m) else ' '}] {label}={str(k):>26}  全={m['ret']:+7.2f}% "
              f"DD={m['dd']:5.1f}% n={m['n']:>3} OOS={m['oos_ret']:+6.2f}% PF={m['oos_pf']:.2f} "
              f"净%={net_t(m):+7.2f}")
    ranked, hit = rank(scores)
    best = ranked[0][0]
    pr, sc, er = build(best)
    state["pr"].update(pr); state["sc"].update(sc); state["er"].update(er)
    e2 = ev()
    print(f"  -> 选定 {label}: {best}  (累积 全={e2['ret']:+.2f}% 净%={net_t(e2):+.2f} "
          f"DD={e2['dd']:.1f}% {'达标' if hit else '未达标(兜底最低DD)'})")
    return ranked


# ---- 各阶段（含线上少笔数结构的关键取值）----
print("\n=== 1) sl_pct × trail（压 DD 主旋钮）===")
def b1(k):
    s, t = k
    return ({}, {}, {"sl_pct": s, "trail_with_st": t})
r1 = scan("sl", [(s, t) for s in [1.0, 1.5, 2.0, 2.5, 3.0, 3.5]
                 for t in [True, False]], b1)

print("\n=== 2) ST (periods × multiplier × src) ===")
def b2(k):
    p, m, s = k
    return ({"periods": p, "multiplier": m, "src": s, "change_atr": True}, {}, {})
r2 = scan("ST", [(p, m, s) for p in [11, 13, 15, 18, 20, 22]
                 for m in [2.0, 2.5, 3.0, 3.5, 4.0]
                 for s in ["hl2", "close"]], b2)

print("\n=== 3) 闸门评分 (full=half × alert)，含低阈值 30 ===")
def b3(k):
    f, a = k
    return ({}, {"scoring_full_threshold": float(f), "scoring_half_threshold": float(f),
                 "scoring_alert_threshold": float(a)}, {})
r3 = scan("gate", [(f, a) for f in [30, 35, 40, 45, 50, 60, 70]
                   for a in [30, 35, 40, 50]], b3)

print("\n=== 4) 出场 tp（含线上单笔全平结构 tp2=100/0）===")
def b4(k):
    a, b, c, d = k
    return ({}, {}, {"tp1_pct": a, "tp1_ratio": b, "tp2_pct": c, "tp2_ratio": d})
r4 = scan("tp", [(a, b, c, d) for a in [1.0, 1.5, 2.0, 2.5, 3.0]
                 for b in [30.0, 40.0, 50.0, 100.0]
                 for c in [1.5, 2.0, 2.5, 3.0, 100.0]
                 for d in [0.0, 40.0, 50.0]], b4)

print("\n=== 5) ER (trend × min/weak) ===")
def b5(k):
    t, m = k
    return ({}, {"er_trend": t, "er_min": m, "er_weak_min": m}, {})
r5 = scan("ER", [(t, m) for t in [0.2, 0.25, 0.3, 0.35, 0.4]
                 for m in [0.1, 0.12, 0.15, 0.2, 0.25]], b5)

print("\n=== 6) 保护利润 ===")
def b6(k):
    a, t = k
    return ({}, {}, {"protect_profit_at": a, "protect_trail_pct": t})
r6 = scan("protect", [(a, t) for a in [1.0, 1.5, 2.0, 3.0]
                      for t in [0.5, 0.8, 1.2, 2.0]], b6)

print("\n=== 7) sl 细节 ===")
def b7(k):
    b, s = k
    return ({}, {}, {"sl_buffer_atr": b, "sl_min_pct": s})
r7 = scan("slbuf", [(b, s) for b in [0.2, 0.3, 0.5, 0.8]
                    for s in [0.6, 0.8, 1.0, 1.2, 1.6]], b7)

print("\n=== 8) 四大闸门 ===")
def b8a(k):
    en, v = k
    return ({}, {"atr_filter_enabled": en, "atr_vol_min": v}, {})
r8a = scan("atr", [(False, 0.75), (True, 0.6), (True, 0.75), (True, 0.9)], b8a)
def b8b(k):
    en, sz, tc = k
    return ({}, {"range_filter_enabled": en, "range_size_max": sz, "range_touches_min": tc}, {})
r8b = scan("range", [(False, 0.15, 2), (True, 0.08, 2), (True, 0.12, 2), (True, 0.15, 2)], b8b)
def b8c(k):
    en, c = k
    return ({}, {"mtf_filter_enabled": en, "mtf_consistency_min": c}, {})
r8c = scan("mtf", [(False, 0.6), (True, 0.5), (True, 0.6), (True, 0.7)], b8c)
def b8d(k):
    en, v = k
    return ({}, {"adx_filter_enabled": en, "adx_min": v}, {})
r8d = scan("adx", [(False, 20.0), (True, 15.0), (True, 20.0), (True, 25.0)], b8d)

cd = ev()
print(f"\n=== 坐标下降拼合 ===  全={cd['ret']:+.2f}% DD={cd['dd']:.1f}% n={cd['n']} "
      f"IS={cd['is_ret']:+.2f}% OOS={cd['oos_ret']:+.2f}% 净%={net_t(cd):+.2f} 达标={ok(cd)}")


def topk(r, k=3):
    return [x[0] for x in r[:k]]


sl_top, st_top = topk(r1, 3), topk(r2, 3)
gate_top, tp_top = topk(r3, 3), topk(r4, 3)
er_top = topk(r5, 2)
N = len(sl_top) * len(st_top) * len(gate_top) * len(tp_top) * len(er_top)
print(f"\n=== 9) 邻域精搜 {len(sl_top)}×{len(st_top)}×{len(gate_top)}×{len(tp_top)}×{len(er_top)} "
      f"= {N} 组合 ===")
best = None
for sl, tr in sl_top:
    for per, mu, src in st_top:
        for gf, gal in gate_top:
            for t1, t1r, t2, t2r in tp_top:
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
                    if ok(m) and (best is None or net_t(m) > net_t(best[1])):
                        best = ((pr, sc, er), m)

print("\n最优（DD 约束内 总净收益最高）：")
if best is None:
    print(f"  ⚠ 无满足 DD<={MAXDD}% 且扣费净>0 的组合")
    FINAL, m_best = (state["pr"], state["sc"], state["er"]), cd
else:
    FINAL, m_best = best[0], best[1]
    print(f"  ST {FINAL[0]['periods']}x{FINAL[0]['multiplier']} {FINAL[0]['src']} | "
          f"gate {FINAL[1]['scoring_full_threshold']:.0f}/{FINAL[1]['scoring_alert_threshold']:.0f} | "
          f"tp1 {FINAL[2]['tp1_pct']}/{FINAL[2]['tp1_ratio']:.0f} "
          f"tp2 {FINAL[2]['tp2_pct']}/{FINAL[2]['tp2_ratio']:.0f} | "
          f"sl {FINAL[2]['sl_pct']} trail {FINAL[2]['trail_with_st']} | "
          f"ER {FINAL[1]['er_trend']}/{FINAL[1]['er_min']}")
print(f"  全={m_best['ret']:+.2f}% DD={m_best['dd']:.1f}% n={m_best['n']} 胜率={m_best['wr']:.1f}% "
      f"IS={m_best['is_ret']:+.2f}% OOS={m_best['oos_ret']:+.2f}% PF_oos={m_best['oos_pf']:.2f}")
print(f"  扣费: taker 净={net_t(m_best):+.2f}% | maker 净={net_m(m_best):+.2f}%")

opt_cfg = copy.deepcopy(BASE)
opt_cfg["params"].update(FINAL[0])
for k, v in FINAL[1].items():
    if k in opt_cfg and k != "params":
        opt_cfg[k] = v
opt_cfg["exit_rules"].update(FINAL[2])
op = os.path.join(HERE, os.path.splitext(os.path.basename(CONFIG))[0] + f"_dd{int(MAXDD)}_opt_config.json")
with open(op, "w", encoding="utf-8") as f:
    json.dump(opt_cfg, f, ensure_ascii=False, indent=2)
print(f"\n[配置已写入] {op}")

live = evaluate(BASE_PR, BASE_SC, BASE_ER)
print(f"\n[线上基准] 全={live['ret']:+.2f}% DD={live['dd']:.1f}% n={live['n']} "
      f"OOS={live['oos_ret']:+.2f}% | taker净={net_t(live):+.2f}% maker净={net_m(live):+.2f}%")
print(f"[对比] 新方案 净={net_t(m_best):+.2f}% vs 线上 净={net_t(live):+.2f}% "
      f"差 {net_t(m_best)-net_t(live):+.2f}pp；DD {m_best['dd']:.1f}% vs {live['dd']:.1f}%")
