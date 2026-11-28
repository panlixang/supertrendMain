# -*- coding: utf-8 -*-
"""NVDA 三周期矩阵寻优（15m / 30m / 1h）—— 按 BTC 流程：ATR ratio → ST → TP/SL → 矩阵验证。

数据：Hyperliquid xyz:NVDA（跟踪 NVDA 股价；Bitget API 本网络不可达，Gate 需要 API key）
      15m 5000根(~52天) / 30m 5000根(~104天) / 1h 5000根(~209天) / 4h 2283根(MTF上下文)
      已入库 backend/candle_data.db，复用 backtest/engine.py 线上同构链路。

流程（每个周期独立跑一遍）：
  0) ATR ratio 特征：ATR14/close 分布 + ATR/近20均值(atr_volatility) + ER 分布 + ST 翻转频率
  1) ST 扫描   periods × multiplier × src
  2) 闸门扫描  scoring full/alert
  3) TP 扫描   tp1_pct × tp1_ratio × tp2_pct × tp2_ratio
  4) SL 扫描   sl_pct × sl_mode × trail_with_st
  5) ER 扫描   er_trend × er_min
  6) 邻域笛卡尔积精搜 top-k 组合
硬约束：PF_oos>1 & IS>0 & OOS笔数达标 & 最大回撤<=15%；软目标：每笔收益效率(ret/n) → ret/dd → OOS。

运行：py nvda_matrix_3tf.py
"""
import copy
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
BACKTEST = os.path.dirname(HERE)
BACKEND = os.path.dirname(BACKTEST)
sys.path.insert(0, BACKTEST)

from engine import run_core, run_backtest, _extract, _load_tf  # noqa: E402
from indicators import super_trend, ta_atr  # noqa: E402

CONFIG = os.path.join(HERE, "nvda_config.json")
DB_SYM = "NVDA-USDT"
DISABLE_PCT = 999.0

with open(CONFIG, encoding="utf-8") as f:
    BASE = json.load(f)
BASE_SC, BASE_PR, BASE_ER = _extract(BASE)

TFS = ["15m", "30m", "1h"]
CANDLES = {t: _load_tf(DB_SYM, t) for t in ("15m", "30m", "1h", "4h")}

# 各周期网格与约束（低周期波动小、笔数少 → 网格更细、约束放宽）
GRID = {
    "15m": dict(st_mult=[1.5, 2.0, 2.5, 3.0, 3.5],
                tp1=[0.8, 1.2, 1.6], tp2=[2.0, 3.0],
                sl=[1.2, 1.6, 2.0, 2.5], dd=15),
    "30m": dict(st_mult=[1.5, 2.0, 2.5, 3.0, 3.5],
                tp1=[1.0, 1.5, 2.0], tp2=[2.5, 3.0],
                sl=[1.5, 2.0, 2.5, 3.0], dd=15),
    "1h": dict(st_mult=[2.0, 2.5, 3.0, 3.5],
               tp1=[1.0, 1.5, 2.0], tp2=[2.5, 3.0, 3.5],
               sl=[1.5, 2.0, 2.5, 3.0], dd=15),
}
ST_PERIODS = [7, 9, 11, 13, 15, 18]
ST_SRC = ["hl2", "close"]
G_FULL = [40, 50, 60, 70]
G_ALERT = [40, 50, 55, 60, 70]
TP1_R = [30.0, 40.0, 50.0]
TP2_R = [40.0, 50.0, 100.0]
SL_MODES = ["st", "pct"]
ER_TREND = [0.2, 0.25, 0.3, 0.35]
ER_MIN = [0.08, 0.10, 0.12, 0.15]
MIN_N = {"15m": 12, "30m": 12, "1h": 15}
MIN_OOS = {"15m": 6, "30m": 8, "1h": 10}

out_lines = []


def log(s=""):
    print(s, flush=True)
    out_lines.append(s)


log("=" * 72)
log("NVDA 三周期矩阵寻优（ATR ratio → ST → TP/SL → 矩阵验证）")
log(f"数据源: Hyperliquid xyz:NVDA  交易参数: {float(BASE_SC['margin_usdt'])}U x {int(BASE_SC['leverage'])}")
for t in TFS:
    a = CANDLES[t]
    log(f"  {t}: {len(a)} 根")
log("=" * 72)


# ───────────────────────── Stage 0: ATR ratio 特征 ─────────────────────────
def pct(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    i = min(len(sorted_vals) - 1, max(0, int(round(p / 100 * (len(sorted_vals) - 1)))))
    return sorted_vals[i]


log("\n#### Stage 0: ATR ratio / ER 特征（按周期）####")
atr_stats = {}
for tf in TFS:
    cs = CANDLES[tf]
    highs = [c["h"] for c in cs]
    lows = [c["l"] for c in cs]
    closes = [c["c"] for c in cs]
    atr = ta_atr(highs, lows, closes, 14)
    ratio = sorted(a / c for a, c in zip(atr, closes) if a)
    # ATR/近20根ATR均值（等价 atr_volatility 口径，滚动一次算完）
    vol = []
    for i in range(33, len(atr)):
        window = [a for a in atr[i - 19:i + 1] if a]
        if window and atr[i]:
            vol.append(atr[i] / (sum(window) / len(window)))
    vol.sort()
    # ER(20) 滚动
    er = []
    for i in range(20, len(closes)):
        seg = closes[i - 19:i + 1]
        path = sum(abs(seg[j] - seg[j - 1]) for j in range(1, 20))
        if path > 0:
            er.append(abs(seg[-1] - seg[0]) / path)
    er.sort()
    st = super_trend([c["o"] for c in cs], highs, lows, closes, 15, 3.0, "hl2", True)
    trend = [t for t in st["trend"] if t is not None]
    flips = sum(1 for i in range(1, len(trend)) if trend[i] != trend[i - 1])
    flip_days = (cs[-1]["ts"] - cs[0]["ts"]) / 86400000
    atr_stats[tf] = {
        "atr_pct_med": pct(ratio, 50) * 100, "atr_pct_p75": pct(ratio, 75) * 100,
        "atr_pct_p90": pct(ratio, 90) * 100, "atr_pct_mean": sum(ratio) / len(ratio) * 100,
        "vol_med": pct(vol, 50), "vol_p90": pct(vol, 90), "vol_ge1": sum(1 for v in vol if v >= 1.0) / len(vol),
        "er_med": pct(er, 50), "er_p75": pct(er, 75), "er_p25": pct(er, 25),
        "flips": flips, "flips_per_day": flips / flip_days,
    }
    s = atr_stats[tf]
    log(f"[{tf}] ATR14/收盘价%: 中位={s['atr_pct_med']:.2f} 均值={s['atr_pct_mean']:.2f} "
        f"P75={s['atr_pct_p75']:.2f} P90={s['atr_pct_p90']:.2f}")
    log(f"       ATR/近20均值: 中位={s['vol_med']:.2f} P90={s['vol_p90']:.2f} 扩张占比(>=1)={s['vol_ge1']:.0%}")
    log(f"       ER(20): P25={s['er_p25']:.2f} 中位={s['er_med']:.2f} P75={s['er_p75']:.2f}")
    log(f"       ST(15x3.0) 翻转: {s['flips']} 次 / {flip_days:.0f} 天 = {s['flips_per_day']:.2f} 次/天")


# ───────────────────────── Stage 1~6: 每周期矩阵寻优 ─────────────────────────
def evaluate(tf, pr=None, sc=None, er=None, state=None):
    p = copy.deepcopy(BASE_PR)
    s = copy.deepcopy(BASE_SC)
    e = copy.deepcopy(BASE_ER)
    s["allow_tfs"] = [tf]  # 线上配置只开 1h，须按回测周期覆盖，否则闸门全拦
    if state:
        p.update(state["pr"]); s.update(state["sc"]); e.update(state["er"])
    if pr:
        p.update(pr)
    if sc:
        s.update(sc)
    if er:
        e.update(er)
    try:
        r = run_core(DB_SYM, s, p, e, tf=tf, candles=CANDLES)
    except Exception as ex:
        return {"ok": False, "err": str(ex), "n": 0, "ret": 0, "pf": 0, "wr": 0,
                "is_ret": 0, "oos_ret": 0, "oos_pf": 0, "oos_n": 0, "dd": 999}
    a, i, o = r["all"], r["is"], r["oos"]
    if not a or not o:
        return {"ok": True, "n": a["n"] if a else 0, "ret": a["ret_pct"] if a else 0,
                "pf": a["pf"] if a else 0, "wr": a["winrate"] if a else 0,
                "is_ret": i["ret_pct"] if i else 0, "oos_ret": o["ret_pct"] if o else 0,
                "oos_pf": o["pf"] if o else 0, "oos_n": o["n"] if o else 0, "dd": r["max_dd"]}
    return {"ok": True, "n": a["n"], "ret": a["ret_pct"], "pf": a["pf"], "wr": a["winrate"],
            "is_ret": i["ret_pct"], "oos_ret": o["ret_pct"], "oos_pf": o["pf"],
            "oos_n": o["n"], "dd": r["max_dd"]}


def pass_filter(tf, m):
    if not m.get("ok") or m["n"] == 0:
        return False
    if m["n"] < MIN_N[tf] or m["oos_n"] < MIN_OOS[tf]:
        return False
    if m["oos_pf"] <= 1.0 or m["is_ret"] <= 0:
        return False
    if m["dd"] > GRID[tf]["dd"]:
        return False
    return True


def eff(m):
    return (m["ret"] / max(m["n"], 1), m["ret"] / max(m["dd"], 0.1))


def scan(tf, state, label, keys, build):
    scores = []
    for k in keys:
        pr, sc, er = build(k)
        m = evaluate(tf, pr, sc, er, state)
        scores.append((k, m))
        flag = "✓" if pass_filter(tf, m) else " "
        e_ = m["ret"] / max(m["n"], 1)
        log(f"  [{flag}] {label}={k!s:>24}  OOS={m['oos_ret']:+7.2f}% PF_oos={m['oos_pf']:5.2f} "
            f"IS={m['is_ret']:+7.2f}% 全={m['ret']:+7.2f}% DD={m['dd']:5.1f}% n={m['n']:3d} 每笔={e_:+.3f}%")
    good = [x for x in scores if pass_filter(tf, x[1])]
    pool = good if good else scores
    pool.sort(key=lambda x: (1 if pass_filter(tf, x[1]) else 0, eff(x[1])[0], eff(x[1])[1], x[1]["oos_ret"]),
              reverse=True)
    best = pool[0][0]
    pr, sc, er = build(best)
    state["pr"].update(pr); state["sc"].update(sc); state["er"].update(er)
    m = evaluate(tf, state=state)
    log(f"  -> 选定 {label}: {best}   (累积基准 全={m['ret']:+.2f}% OOS={m['oos_ret']:+.2f}% "
        f"DD={m['dd']:.1f}% n={m['n']} 每笔={m['ret']/max(m['n'],1):+.3f}%)")
    return pool


results = {}
for tf in TFS:
    g = GRID[tf]
    log("\n" + "#" * 72)
    log(f"#### 周期 {tf} 矩阵寻优 ####")
    state = {"pr": {}, "sc": {}, "er": {}}

    log(f"\n=== 1) ST 扫描（periods × multiplier × src）===")
    def b_st(k):
        per, mu, src = k
        return ({"periods": per, "multiplier": mu, "src": src, "change_atr": True}, {}, {})
    st_rank = scan(tf, state, "ST", [(p, m, s) for p in ST_PERIODS for m in g["st_mult"] for s in ST_SRC], b_st)

    log(f"\n=== 2) 闸门扫描（full=half × alert）===")
    def b_gate(k):
        f, al = k
        return ({}, {"scoring_full_threshold": float(f), "scoring_half_threshold": float(f),
                     "scoring_alert_threshold": float(al)}, {})
    gate_rank = scan(tf, state, "gate", [(f, a) for f in G_FULL for a in G_ALERT], b_gate)

    log(f"\n=== 3) TP 扫描（tp1_pct × tp1_ratio × tp2_pct × tp2_ratio）===")
    def b_tp(k):
        t1, r1, t2, r2 = k
        return ({}, {}, {"tp1_pct": t1, "tp1_ratio": r1, "tp2_pct": t2, "tp2_ratio": r2})
    tp_rank = scan(tf, state, "tp", [(a, b, c, d) for a in g["tp1"] for b in TP1_R
                                     for c in g["tp2"] for d in TP2_R], b_tp)

    log(f"\n=== 4) SL 扫描（sl_pct × sl_mode × trail_with_st）===")
    def b_sl(k):
        sl, mode, tr = k
        return ({}, {}, {"sl_pct": sl, "sl_mode": mode, "trail_with_st": tr})
    sl_rank = scan(tf, state, "sl", [(s, mo, t) for s in g["sl"] for mo in SL_MODES
                                     for t in (True, False)], b_sl)

    log(f"\n=== 5) ER 扫描（er_trend × er_min）===")
    def b_er(k):
        tr, mn = k
        return ({}, {"er_trend": tr, "er_min": mn, "er_weak_min": mn}, {})
    er_rank = scan(tf, state, "ER", [(t, m) for t in ER_TREND for m in ER_MIN], b_er)

    cd = evaluate(tf, state=state)
    log(f"\n=== 坐标下降拼合[{tf}] ===  全={cd['ret']:+.2f}% OOS={cd['oos_ret']:+.2f}% "
        f"PF_oos={cd['oos_pf']:.2f} IS={cd['is_ret']:+.2f}% DD={cd['dd']:.1f}% n={cd['n']} "
        f"每笔={cd['ret']/max(cd['n'],1):+.3f}% 达标={pass_filter(tf, cd)}")

    def topk(ranked, k):
        return [x[0] for x in ranked[:k]]

    log(f"\n=== 6) 邻域笛卡尔积精搜 ===")
    best = None
    best_combo = None
    combos = 0
    for per, mu, src in topk(st_rank, 3):
        for gf, gal in topk(gate_rank, 2):
            for t1, r1, t2, r2 in topk(tp_rank, 3):
                for sl, mode, tr in topk(sl_rank, 2):
                    for ert, erm in topk(er_rank, 2):
                        pr = {"periods": per, "multiplier": mu, "src": src, "change_atr": True}
                        sc = {"scoring_full_threshold": float(gf), "scoring_half_threshold": float(gf),
                              "scoring_alert_threshold": float(gal),
                              "er_trend": ert, "er_min": erm, "er_weak_min": erm}
                        er = {"tp1_pct": t1, "tp1_ratio": r1, "tp2_pct": t2, "tp2_ratio": r2,
                              "sl_pct": sl, "sl_mode": mode, "trail_with_st": tr}
                        combos += 1
                        m = evaluate(tf, pr, sc, er)
                        if not pass_filter(tf, m):
                            continue
                        score = (eff(m)[0], eff(m)[1], m["oos_ret"])
                        if best is None or score > best[0]:
                            best = (score, m)
                            best_combo = (pr, sc, er)
    log(f"  共搜索 {combos} 组合")
    if best_combo is None:
        log("  无满足硬约束组合，回退坐标下降结果。")
        best_combo = (dict(state["pr"]), dict(state["sc"]), dict(state["er"]))
        m_best = cd
    else:
        m_best = evaluate(tf, *best_combo)
    bc_pr, bc_sc, bc_er = best_combo
    log(f"  最优[{tf}]: ST {bc_pr.get('periods')}x{bc_pr.get('multiplier')} {bc_pr.get('src')} | "
        f"gate {bc_sc.get('scoring_full_threshold')}/{bc_sc.get('scoring_alert_threshold')} | "
        f"tp1 {bc_er.get('tp1_pct')}/{bc_er.get('tp1_ratio')} tp2 {bc_er.get('tp2_pct')}/{bc_er.get('tp2_ratio')} | "
        f"sl {bc_er.get('sl_pct')}/{bc_er.get('sl_mode')} trail={bc_er.get('trail_with_st')} | "
        f"ER {bc_sc.get('er_trend')}/{bc_sc.get('er_min')}")
    log(f"  指标: 全={m_best['ret']:+.2f}% OOS={m_best['oos_ret']:+.2f}% PF_oos={m_best['oos_pf']:.2f} "
        f"IS={m_best['is_ret']:+.2f}% DD={m_best['dd']:.1f}% n={m_best['n']} 胜率={m_best['wr']:.1f}% "
        f"每笔={m_best['ret']/max(m_best['n'],1):+.3f}%")

    # 写最优配置 JSON（基于线上 nvda_config.json 修改）
    cfg = copy.deepcopy(BASE)
    cfg["params"].update(bc_pr)
    for k, v in bc_sc.items():
        if k in cfg:
            cfg[k] = v
    cfg["exit_rules"].update(bc_er)
    cfg["allow_tfs"] = [tf]
    opt_path = os.path.join(HERE, f"nvda_best_{tf}.json")
    with open(opt_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    log(f"  [配置已写入] {opt_path}")

    results[tf] = {"metrics": m_best, "combo": {k: v for k, v in best_combo[0].items()},
                   "gate": {k: v for k, v in bc_sc.items()},
                   "exit": {k: v for k, v in bc_er.items()},
                   "atr": atr_stats[tf]}

    # 完整回测报告
    log(f"\n=== {tf} 最优配置完整回测报告 ===")
    run_backtest(DB_SYM, opt_path, HERE, out_name=f"nvda_best_{tf}.txt", tf=tf, candles=CANDLES)


# ───────────────────────── 跨周期汇总 ─────────────────────────
log("\n" + "=" * 72)
log("#### 跨周期汇总对比 ####")
log(f"{'TF':>5} {'全样本%':>9} {'OOS%':>9} {'PF_oos':>7} {'IS%':>9} {'DD%':>6} {'笔数':>5} {'胜率%':>6} {'每笔%':>8}")
for tf in TFS:
    m = results[tf]["metrics"]
    log(f"{tf:>5} {m['ret']:>+9.2f} {m['oos_ret']:>+9.2f} {m['oos_pf']:>7.2f} {m['is_ret']:>+9.2f} "
        f"{m['dd']:>6.1f} {m['n']:>5d} {m['wr']:>6.1f} {m['ret']/max(m['n'],1):>+8.3f}")
cands = [(tf, r["metrics"]) for tf, r in results.items() if pass_filter(tf, r["metrics"])]
if cands:
    cands.sort(key=lambda x: (x[1]["oos_ret"], eff(x[1])[0]), reverse=True)
    log(f"\n[最佳周期] {cands[0][0]}  (OOS 与每笔效率综合最优)")
    m = cands[0][1]
    log(f"  全={m['ret']:+.2f}% OOS={m['oos_ret']:+.2f}% PF_oos={m['oos_pf']:.2f} DD={m['dd']:.1f}% n={m['n']}")
else:
    log("\n[最佳周期] 无周期满足全部硬约束，见各周期明细择优。")

with open(os.path.join(HERE, "nvda_matrix_summary.json"), "w", encoding="utf-8") as f:
    json.dump({tf: {"metrics": r["metrics"], "st": r["combo"], "gate": r["gate"], "exit": r["exit"]}
               for tf, r in results.items()}, f, ensure_ascii=False, indent=2)

with open(os.path.join(HERE, "nvda_matrix_3tf.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(out_lines) + "\n")
print(f"\n[完成] 报告已写入 {HERE}/nvda_matrix_3tf.txt")
