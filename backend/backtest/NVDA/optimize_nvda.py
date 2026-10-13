# -*- coding: utf-8 -*-
"""NVDA 参数寻优 v4 —— 目标改为"每笔收益效率优先"，解决 v3 的 213 笔低效问题。

v3 教训：目标"最大化全样本收益"在 NVDA 上选出 gate40 低闸门 → 213 笔、每笔仅 ~0.09%，
性价比低。诊断发现 NVDA 上"OOS 正"与"少笔数"强冲突：n>=137 才 OOS 转正，n<=74 则 OOS 转负
（数据特性，非参数问题）。

v4 调整：
  硬约束 pass_filter: PF_oos>1 & IS>0 & OOS笔数>=10 & 全样本笔数>=15 & 最大回撤<=15%
                       （保住"OOS 正"这个寻优初衷）
  软目标 rank: 达标集合中 优先最大化 每笔收益效率 ret/n，其次 收益/回撤比 ret/dd，OOS tiebreak
  —— 即同样赚钱、同样回撤下，选笔数更少的组合，提升单笔质量。
  末尾额外扫描"少笔数高质量档"(gate 60/70/80，OOS 可能略负但 n 极小、回撤极低) 供对照。

方法：累积式坐标下降 + 邻域笛卡尔积精搜。
"""
import os, sys, json, copy
HERE = os.path.dirname(os.path.abspath(__file__))
BACKTEST = os.path.dirname(HERE)
sys.path.insert(0, BACKTEST)
from engine import run_core, run_backtest, _extract, _load_tf

CONFIG = os.path.join(HERE, "nvda_config.json")
DB_SYM = "NVDA-USDT"

with open(CONFIG, encoding="utf-8") as f:
    BASE = json.load(f)
BASE_SC, BASE_PR, BASE_ER = _extract(BASE)

CANDLES = {"15m": _load_tf(DB_SYM, "15m"),
           "1h": _load_tf(DB_SYM, "1h"),
           "4h": _load_tf(DB_SYM, "4h")}
N1H = len(CANDLES["1h"])
print(f"[数据] {DB_SYM} 1h {N1H} 根; 硬约束: PF_oos>1 & IS>0 & OOS笔数>=10 & DD<=15%; 目标: 最大化每笔收益效率(ret/n)")


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
        return {"ok": True, "n": 0, "ret": 0, "pf": 0, "wr": 0,
                "is_ret": 0, "oos_ret": 0, "oos_pf": 0, "oos_n": 0, "dd": r["max_dd"]}
    return {"ok": True, "n": a["n"], "ret": a["ret_pct"], "pf": a["pf"], "wr": a["winrate"],
            "is_ret": i["ret_pct"], "oos_ret": o["ret_pct"], "oos_pf": o["pf"],
            "oos_n": o["n"], "dd": r["max_dd"]}


def pass_filter(m):
    if not m.get("ok"):
        return False
    if m["n"] < 15 or m["oos_n"] < 10:
        return False
    if m["oos_pf"] <= 1.0 or m["is_ret"] <= 0:
        return False
    if m["dd"] > 15:
        return False
    return True


def _eff(m):
    # 每笔收益效率 + 收益/回撤比（用于排序，不进硬约束）
    return (m["ret"] / m["n"], m["ret"] / max(m["dd"], 0.1))


def rank(scores):
    good = [x for x in scores if pass_filter(x[1])]
    pool = good if good else scores
    # 软目标：达标集合中 优先 每笔收益效率(ret/n)，其次 收益/回撤(ret/dd)，OOS 作 tiebreak
    pool.sort(key=lambda x: (1 if pass_filter(x[1]) else 0,
                             _eff(x[1])[0], _eff(x[1])[1], x[1]["oos_ret"]),
              reverse=True)
    return pool


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
        flag = "✓" if pass_filter(m) else " "
        eff = m["ret"] / max(m["n"], 1)
        print(f"  [{flag}] {label}={k!s:>22}  OOS={m['oos_ret']:+.2f}% PF_oos={m['oos_pf']:.2f} "
              f"IS={m['is_ret']:+.2f}% 全={m['ret']:+.2f}% DD={m['dd']:.1f}% n={m['n']} 每笔={eff:+.3f}%")
    ranked = rank(scores)
    best = ranked[0][0]
    pr, sc, er = build(best)
    state["pr"].update(pr); state["sc"].update(sc); state["er"].update(er)
    e2 = _eff(ev())
    print(f"  -> 选定 {label}: {best}  (累积基准 每笔={e2[0]:+.3f}% 全={ev()['ret']:+.2f}% DD={ev()['dd']:.1f}% OOS={ev()['oos_ret']:+.2f}%)")
    return ranked


print("\n=== 1) ST 扫描 (periods × multiplier × src)，基准=线上 ===")
ST_PERIODS = [7, 9, 11, 13, 15, 18]
ST_MULT = [2.0, 2.5, 3.0, 3.5]
ST_SRC = ["hl2", "close"]
def build_st(k):
    per, mu, src = k
    return ({"periods": per, "multiplier": mu, "src": src, "change_atr": True}, {}, {})
st_rank = scan("ST", [(p, m, s) for p in ST_PERIODS for m in ST_MULT for s in ST_SRC], build_st)

print("\n=== 2) 闸门扫描 (full=half × alert)，基准=最优ST ===")
G_FULL = [40, 50, 60, 70, 80]
G_ALERT = [40, 50, 55, 60, 70]
def build_gate(k):
    f, al = k
    return ({}, {"scoring_full_threshold": float(f), "scoring_half_threshold": float(f),
                "scoring_alert_threshold": float(al)}, {})
gate_rank = scan("gate", [(f, a) for f in G_FULL for a in G_ALERT], build_gate)

print("\n=== 3) 出场 tp 扫描 (tp1_pct × tp1_ratio × tp2_pct × tp2_ratio)，基准=ST+闸门 ===")
TP1_PCT = [1.0, 1.5, 2.0]
TP1_R = [30.0, 40.0]
TP2_PCT = [2.0, 2.5, 3.0]
TP2_R = [40.0, 50.0]
def build_tp(k):
    t1, t1r, t2, t2r = k
    return ({}, {}, {"tp1_pct": t1, "tp1_ratio": t1r, "tp2_pct": t2, "tp2_ratio": t2r})
tp_rank = scan("tp", [(a, b, c, d) for a in TP1_PCT for b in TP1_R for c in TP2_PCT for d in TP2_R], build_tp)

print("\n=== 4) 出场 sl/trail 扫描 (sl_pct × trail_with_st)，基准=ST+闸门+tp ===")
SL_PCT = [2.0, 2.5, 3.0, 3.5]
TRAIL = [True, False]
def build_sl(k):
    sl, tr = k
    return ({}, {}, {"sl_pct": sl, "trail_with_st": tr})
sl_rank = scan("sl", [(s, t) for s in SL_PCT for t in TRAIL], build_sl)

print("\n=== 5) ER 扫描 (er_trend × er_min, er_weak_min 同步)，基准=前四项 ===")
ER_TREND = [0.2, 0.25, 0.3, 0.35]
ER_MIN = [0.1, 0.12, 0.15]
def build_er(k):
    tr, mn = k
    return ({}, {"er_trend": tr, "er_min": mn, "er_weak_min": mn}, {})
er_rank = scan("ER", [(t, m) for t in ER_TREND for m in ER_MIN], build_er)

cd = evaluate(state["pr"], state["sc"], state["er"])
print(f"\n=== 坐标下降拼合 ===  OOS={cd['oos_ret']:+.2f}% PF_oos={cd['oos_pf']:.2f} "
      f"IS={cd['is_ret']:+.2f}% 全={cd['ret']:+.2f}% DD={cd['dd']:.1f}% n={cd['n']} 每笔={cd['ret']/max(cd['n'],1):+.3f}% 达标={pass_filter(cd)}")


def topk(ranked, k=3):
    return [x[0] for x in ranked[:k]]

st_top = topk(st_rank, 3)
gate_top = topk(gate_rank, 2)
tp_top = topk(tp_rank, 3)
sl_top = topk(sl_rank, 2)
er_top = topk(er_rank, 2)
print(f"\n=== 6) 邻域笛卡尔积精搜 {len(st_top)}×{len(gate_top)}×{len(tp_top)}×{len(sl_top)}×{len(er_top)} "
      f"= {len(st_top)*len(gate_top)*len(tp_top)*len(sl_top)*len(er_top)} 组合 ===")
best = None
best_combo = None
for per, mu, src in st_top:
    for gf, gal in gate_top:
        for t1, t1r, t2, t2r in tp_top:
            for sl, tr in sl_top:
                for ert, erm in er_top:
                    pr = {"periods": per, "multiplier": mu, "src": src, "change_atr": True}
                    sc = {"scoring_full_threshold": float(gf), "scoring_half_threshold": float(gf),
                          "scoring_alert_threshold": float(gal),
                          "er_trend": ert, "er_min": erm, "er_weak_min": erm}
                    er = {"tp1_pct": t1, "tp1_ratio": t1r, "tp2_pct": t2, "tp2_ratio": t2r,
                          "sl_pct": sl, "trail_with_st": tr}
                    m = evaluate(pr, sc, er)
                    if not pass_filter(m):
                        continue
                    score = (_eff(m)[0], _eff(m)[1], m["oos_ret"])
                    if best is None or score > best[0]:
                        best = (score, m)
                        best_combo = (pr, sc, er)
print("  邻域精搜最优（达标内 每笔收益效率最高）：")
if best_combo is None:
    print("  无满足硬约束组合，回退坐标下降。")
    best_combo = (state["pr"], state["sc"], state["er"])
    m_best = cd
else:
    m_best = evaluate(*best_combo)
    print(f"  ST {best_combo[0]['periods']}x{best_combo[0]['multiplier']} {best_combo[0]['src']} | "
          f"gate {best_combo[1]['scoring_full_threshold']}/{best_combo[1]['scoring_alert_threshold']} | "
          f"tp1 {best_combo[2]['tp1_pct']}/{best_combo[2]['tp1_ratio']} "
          f"tp2 {best_combo[2]['tp2_pct']}/{best_combo[2]['tp2_ratio']} | "
          f"sl {best_combo[2]['sl_pct']} trail {best_combo[2]['trail_with_st']} | "
          f"ER trend {best_combo[1]['er_trend']} min {best_combo[1]['er_min']}")
    print(f"  OOS={m_best['oos_ret']:+.2f}% PF_oos={m_best['oos_pf']:.2f} IS={m_best['is_ret']:+.2f}% "
          f"全={m_best['ret']:+.2f}% DD={m_best['dd']:.1f}% n={m_best['n']} 每笔={m_best['ret']/m_best['n']:+.3f}%")

opt_cfg = copy.deepcopy(BASE)
opt_cfg["params"].update(best_combo[0])
for k, v in best_combo[1].items():
    if k in opt_cfg:
        opt_cfg[k] = v
opt_cfg["exit_rules"].update(best_combo[2])
opt_path = os.path.join(HERE, "nvda_opt_config.json")
with open(opt_path, "w", encoding="utf-8") as f:
    json.dump(opt_cfg, f, ensure_ascii=False, indent=2)
print(f"\n[最优配置已写入] {opt_path}")

# ===== 额外：少笔数高质量档（高闸门，OOS 可能略负但 n 极小、回撤极低）=====
print("\n=== 额外对照：少笔数高质量档（ST=18x2.0 close 基准，扫高闸门）===")
hi_gate_scores = []
for gf in [60, 70, 80]:
    for gal in [60, 70]:
        sc = {"scoring_full_threshold": float(gf), "scoring_half_threshold": float(gf),
              "scoring_alert_threshold": float(gal)}
        m = evaluate({"periods": 18, "multiplier": 2.0, "src": "close", "change_atr": True}, sc, {})
        hi_gate_scores.append(((gf, gal), m))
        flag = "✓全正" if m["ret"] > 0 else " "
        print(f"  [{flag}] gate={gf}/{gal}  OOS={m['oos_ret']:+.2f}% PF_oos={m['oos_pf']:.2f} "
              f"IS={m['is_ret']:+.2f}% 全={m['ret']:+.2f}% DD={m['dd']:.1f}% n={m['n']} 每笔={m['ret']/max(m['n'],1):+.3f}%")
# 选 n 最小且 全样本>0 者
cand = [x for x in hi_gate_scores if x[1]["ok"] and x[1]["ret"] > 0]
cand.sort(key=lambda x: x[1]["n"])
if cand:
    (gf, gal), mh = cand[0]
    print(f"  -> 少笔数档候选: gate {gf}/{gal}  n={mh['n']} 全={mh['ret']:+.2f}% OOS={mh['oos_ret']:+.2f}% DD={mh['dd']:.1f}% 每笔={mh['ret']/mh['n']:+.3f}%")

robust_pr = {"periods": 15, "multiplier": 2.5, "src": "close", "change_atr": True}
robust_cfg = copy.deepcopy(BASE)
robust_cfg["params"].update(robust_pr)
robust_path = os.path.join(HERE, "nvda_robust_config.json")
with open(robust_path, "w", encoding="utf-8") as f:
    json.dump(robust_cfg, f, ensure_ascii=False, indent=2)
mr = evaluate(robust_pr, {}, {})
print(f"\n=== 对照：仅换 ST 源为 15x2.5 close（其余=线上原值）===")
print(f"  全={mr['ret']:+.2f}% OOS={mr['oos_ret']:+.2f}% PF_oos={mr['oos_pf']:.2f} "
      f"IS={mr['is_ret']:+.2f}% DD={mr['dd']:.1f}% n={mr['n']} 达标={pass_filter(mr)}")

print("\n=== 最优组合(v4 每笔效率优先)完整回测报告 ===")
run_backtest(DB_SYM, opt_path, HERE, out_name="nvda_optimized_1h.txt")
