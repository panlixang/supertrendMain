# -*- coding: utf-8 -*-
"""NVDA 形态识别页链路寻优（30m / 1h）—— 无闸门裸奔版。

口径完全对齐 study_btc2026.py 的形态识别页链路（复用 backtest/engine.py 线上同构回测）：
  sc: use_scoring=False, min_score=0, ER 阈值全 0, mtf/range/atr/adx/filter_v3 全关, quick 关
      （即：无评分闸门、无 ER 闸门 —— 首页策略的闸门不在形态识别页链路里）
  扫描维度：
    1) ST        periods × multiplier × src（形态识别页固定默认 10×3.0，一并纳入扫描）
    2) TP1       tp1_pct × tp1_ratio（单档，tp2/tp3 禁用）
    3) 保本×SL   move_sl_to_entry × sl_mode × sl_pct（10x 杠杆自适应：实际止损=sl_pct×2.5，
                 最小距离=sl_min_pct×2.5=3% 兜底；引擎回测 trail_with_st 不生效，固定 True）
    4) 反向平    reverse_close：True → tp3_pct=0（引擎中=所有价格出场失效，仅同周期反向信号平仓）
  ATR ratio 特征（ATR14/收盘价）作为 TP/SL 选档依据，随报告输出。

约束/目标与 BTC 流程一致：
  硬约束 PF_oos>1 & IS>0 & OOS笔数达标 & DD<=15%；软目标每笔收益效率(ret/n) → ret/dd → OOS。

运行：py nvda_pattern_opt.py
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
sys.path.insert(0, BACKTEST)

from engine import run_core, run_backtest, _extract, _load_tf  # noqa: E402
from indicators import ta_atr  # noqa: E402

CONFIG = os.path.join(HERE, "nvda_config.json")
DB_SYM = "NVDA-USDT"
TFS = ["30m", "1h"]
DISABLE_PCT = 999.0

with open(CONFIG, encoding="utf-8") as f:
    BASE = json.load(f)
BASE_SC, BASE_PR, BASE_ER = _extract(BASE)
CANDLES = {t: _load_tf(DB_SYM, t) for t in ("15m", "30m", "1h", "4h")}

GRID = {
    "30m": dict(tp1=[1.0, 1.5, 2.0, 2.5], sl=[0.4, 0.6, 0.8, 1.0, 1.2]),
    "1h": dict(tp1=[1.5, 2.0, 2.5, 3.0, 4.0], sl=[0.5, 0.8, 1.0, 1.2, 1.6]),
}
TP1_RATIO = [30.0, 40.0, 50.0, 100.0]
ST_PERIODS = [7, 9, 10, 11, 13, 15, 18]
ST_MULT = [2.0, 2.5, 3.0, 3.5]
ST_SRC = ["hl2", "close"]
MIN_N = {"30m": 12, "1h": 15}
MIN_OOS = {"30m": 8, "1h": 10}


out_lines = []


def log(s=""):
    print(s, flush=True)
    out_lines.append(s)


log("=" * 72)
log("NVDA 形态识别页链路寻优（无闸门裸奔）：ST + ATR ratio + TP1/保本/反向平/SL")
log(f"数据源: Hyperliquid xyz:NVDA  交易参数: {float(BASE_SC['margin_usdt'])}U x {int(BASE_SC['leverage'])}x")
for t in TFS:
    log(f"  {t}: {len(CANDLES[t])} 根")
log("=" * 72)

# ── ATR ratio 特征（TP/SL 选档依据）──
log("\n#### ATR ratio 特征 ####")
for tf in TFS:
    cs = CANDLES[tf]
    atr = ta_atr([c["h"] for c in cs], [c["l"] for c in cs], [c["c"] for c in cs], 14)
    ratio = sorted(a / c for a, c in zip(atr, [c["c"] for c in cs]) if a)
    n = len(ratio)
    log(f"[{tf}] ATR14/收盘价%: 中位={ratio[n//2]*100:.2f} P75={ratio[int(n*0.75)]*100:.2f} "
        f"P90={ratio[int(n*0.9)]*100:.2f}  → TP1/SL 档位应覆盖 1~4×中位ATR")


def build_sc(tf):
    """形态识别页裸奔口径（照搬 study_btc2026.build）。"""
    sc = copy.deepcopy(BASE_SC)
    sc["allow_tfs"] = [tf]
    sc["use_scoring"] = False
    sc["min_score"] = 0
    sc["allow_grades"] = ["A", "B", "C"]
    for k in ("er_min", "er_trend", "er_weak_min", "er_hide_below"):
        sc[k] = 0.0
    for k in ("mtf_filter_enabled", "range_filter_enabled", "atr_filter_enabled",
              "adx_filter_enabled"):
        sc[k] = False
    # filter_v3 是 pattern_trade 管线字段，engine.run_core 的 SymbolTradeConfig 无此键，引擎闸门不含 V3
    sc["quick_enabled"] = False
    return sc


def evaluate(tf, pr=None, er=None, state=None):
    p = copy.deepcopy(BASE_PR)
    s = build_sc(tf)
    e = copy.deepcopy(BASE_ER)
    # 单档 TP1：tp2/tp3 禁用；引擎中 tp3_pct<=0 即反向平模式
    e.update({"tp2_pct": DISABLE_PCT, "tp2_ratio": 0.0,
              "tp3_pct": DISABLE_PCT, "tp3_ratio": 0.0, "tp3_mode": "pct",
              "max_loss_enabled": False})
    if state:
        p.update(state["pr"]); e.update(state["er"])
    if pr:
        p.update(pr)
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
    if m["dd"] > 15:
        return False
    return True


def eff(m):
    return (m["ret"] / max(m["n"], 1), m["ret"] / max(m["dd"], 0.1))


def scan(tf, state, label, keys, build):
    scores = []
    for k in keys:
        pr, er = build(k)
        m = evaluate(tf, pr, er, state)
        scores.append((k, m))
        flag = "✓" if pass_filter(tf, m) else " "
        log(f"  [{flag}] {label}={k!s:>28}  OOS={m['oos_ret']:+7.2f}% PF_oos={m['oos_pf']:5.2f} "
            f"IS={m['is_ret']:+7.2f}% 全={m['ret']:+7.2f}% DD={m['dd']:5.1f}% n={m['n']:3d} "
            f"每笔={m['ret']/max(m['n'],1):+.3f}%")
    good = [x for x in scores if pass_filter(tf, x[1])]
    pool = good if good else scores
    pool.sort(key=lambda x: (1 if pass_filter(tf, x[1]) else 0, eff(x[1])[0], eff(x[1])[1], x[1]["oos_ret"]),
              reverse=True)
    best = pool[0][0]
    pr, er = build(best)
    state["pr"].update(pr); state["er"].update(er)
    m = evaluate(tf, state=state)
    log(f"  -> 选定 {label}: {best}   (累积基准 全={m['ret']:+.2f}% OOS={m['oos_ret']:+.2f}% "
        f"DD={m['dd']:.1f}% n={m['n']} 每笔={m['ret']/max(m['n'],1):+.3f}%)")
    return pool


def topk(ranked, k):
    return [x[0] for x in ranked[:k]]


results = {}
for tf in TFS:
    g = GRID[tf]
    log("\n" + "#" * 72)
    log(f"#### 周期 {tf}（形态识别页裸奔）####")
    state = {"pr": {}, "er": {}}

    log("\n=== 1) ST 扫描（periods × multiplier × src，含形态页默认 10×3.0）===")
    def b_st(k):
        per, mu, src = k
        return ({"periods": per, "multiplier": mu, "src": src, "change_atr": True}, {})
    st_rank = scan(tf, state, "ST", [(p, m, s) for p in ST_PERIODS for m in ST_MULT for s in ST_SRC], b_st)

    log("\n=== 2) TP1 扫描（tp1_pct × tp1_ratio，单档）===")
    def b_tp(k):
        t1, r1 = k
        return ({}, {"tp1_pct": t1, "tp1_ratio": r1})
    tp_rank = scan(tf, state, "tp1", [(a, b) for a in g["tp1"] for b in TP1_RATIO], b_tp)

    log("\n=== 3) 保本×SL 扫描（move_sl_to_entry × sl_mode × sl_pct）===")
    def b_sl(k):
        mv, mode, sl = k
        return ({}, {"move_sl_to_entry": mv, "sl_mode": mode, "sl_pct": sl})
    sl_rank = scan(tf, state, "sl", [(mv, mo, sl) for mv in (True, False)
                                     for mo in ("pct", "st") for sl in g["sl"]], b_sl)

    log("\n=== 4) 反向平 扫描（reverse_close: False=正常TP1/保本/SL出场, True=仅反向信号平仓）===")
    def b_rev(k):
        rev = k
        if rev:
            return ({}, {"tp3_pct": 0.0})  # 引擎口径：tp3_pct<=0 → 价格出场全失效，仅反向信号平
        return ({}, {})
    rev_rank = scan(tf, state, "rev", [False, True], b_rev)

    cd = evaluate(tf, state=state)
    log(f"\n=== 坐标下降拼合[{tf}] ===  全={cd['ret']:+.2f}% OOS={cd['oos_ret']:+.2f}% "
        f"PF_oos={cd['oos_pf']:.2f} IS={cd['is_ret']:+.2f}% DD={cd['dd']:.1f}% n={cd['n']} "
        f"每笔={cd['ret']/max(cd['n'],1):+.3f}% 达标={pass_filter(tf, cd)}")

    log("\n=== 5) 邻域笛卡尔积精搜 ===")
    best = None
    best_combo = None
    combos = 0
    for per, mu, src in topk(st_rank, 3):
        for t1, r1 in topk(tp_rank, 3):
            for mv, mo, sl in topk(sl_rank, 3):
                for rev in (False, True):
                    pr = {"periods": per, "multiplier": mu, "src": src, "change_atr": True}
                    er = {"tp1_pct": t1, "tp1_ratio": r1,
                          "move_sl_to_entry": mv, "sl_mode": mo, "sl_pct": sl}
                    if rev:
                        er["tp3_pct"] = 0.0
                    combos += 1
                    m = evaluate(tf, pr, er)
                    if not pass_filter(tf, m):
                        continue
                    score = (eff(m)[0], eff(m)[1], m["oos_ret"])
                    if best is None or score > best[0]:
                        best = (score, m)
                        best_combo = (pr, er, rev)
    log(f"  共搜索 {combos} 组合")
    if best_combo is None:
        log("  无满足硬约束组合，回退坐标下降结果。")
        best_combo = (dict(state["pr"]), dict(state["er"]), state["er"].get("tp3_pct") == 0.0)
        m_best = cd
    else:
        m_best = evaluate(tf, best_combo[0], best_combo[1])
    bc_pr, bc_er, bc_rev = best_combo
    rev_desc = "开（仅反向信号平仓）" if bc_rev else "关（TP1/保本/SL 正常出场）"
    log(f"  最优[{tf}]: ST {bc_pr.get('periods')}x{bc_pr.get('multiplier')} {bc_pr.get('src')} | "
        f"tp1 {bc_er.get('tp1_pct')}%平{bc_er.get('tp1_ratio')}% | 保本={bc_er.get('move_sl_to_entry')} | "
        f"sl {bc_er.get('sl_mode')} {bc_er.get('sl_pct')}% | 反向平: {rev_desc}")
    log(f"  指标: 全={m_best['ret']:+.2f}% OOS={m_best['oos_ret']:+.2f}% PF_oos={m_best['oos_pf']:.2f} "
        f"IS={m_best['is_ret']:+.2f}% DD={m_best['dd']:.1f}% n={m_best['n']} 胜率={m_best['wr']:.1f}% "
        f"每笔={m_best['ret']/max(m_best['n'],1):+.3f}%")

    cfg = copy.deepcopy(BASE)
    cfg["params"].update(bc_pr)
    cfg["exit_rules"].update(bc_er)
    cfg["exit_rules"]["tp2_pct"] = DISABLE_PCT
    cfg["exit_rules"]["tp2_ratio"] = 0.0
    if bc_rev:
        cfg["exit_rules"]["tp3_pct"] = 0.0
        cfg["exit_rules"]["tp3_ratio"] = 0.0
        cfg["exit_rules"]["reverse_close"] = True
    else:
        cfg["exit_rules"]["tp3_pct"] = DISABLE_PCT
        cfg["exit_rules"]["tp3_ratio"] = 0.0
        cfg["exit_rules"]["reverse_close"] = False
    cfg["exit_rules"]["max_loss_enabled"] = False
    cfg["allow_tfs"] = [tf]
    # 形态识别页无闸门
    bare_sc = build_sc(tf)
    for k, v in bare_sc.items():
        if k in cfg:
            cfg[k] = v
    opt_path = os.path.join(HERE, f"nvda_pattern_{tf}.json")
    with open(opt_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    log(f"  [配置已写入] {opt_path}")

    results[tf] = {"metrics": m_best, "st": dict(bc_pr), "exit": dict(bc_er), "reverse": bc_rev}

    log(f"\n=== {tf} 最优配置完整回测报告（形态识别页口径）===")
    run_backtest(DB_SYM, opt_path, HERE, out_name=f"nvda_pattern_{tf}.txt", tf=tf, candles=CANDLES)

log("\n" + "=" * 72)
log("#### 跨周期汇总（形态识别页裸奔）####")
log(f"{'TF':>5} {'全样本%':>9} {'OOS%':>9} {'PF_oos':>7} {'IS%':>9} {'DD%':>6} {'笔数':>5} {'胜率%':>6} {'每笔%':>8}")
for tf in TFS:
    m = results[tf]["metrics"]
    log(f"{tf:>5} {m['ret']:>+9.2f} {m['oos_ret']:>+9.2f} {m['oos_pf']:>7.2f} {m['is_ret']:>+9.2f} "
        f"{m['dd']:>6.1f} {m['n']:>5d} {m['wr']:>6.1f} {m['ret']/max(m['n'],1):>+8.3f}")
cands = [(tf, r["metrics"]) for tf, r in results.items() if pass_filter(tf, r["metrics"])]
if cands:
    cands.sort(key=lambda x: (x[1]["oos_ret"], eff(x[1])[0]), reverse=True)
    log(f"\n[最佳周期] {cands[0][0]}")
else:
    log("\n[最佳周期] 无周期满足全部硬约束，见明细择优。")

with open(os.path.join(HERE, "nvda_pattern_summary.json"), "w", encoding="utf-8") as f:
    json.dump({tf: {"metrics": r["metrics"], "st": r["st"], "exit": r["exit"], "reverse": r["reverse"]}
               for tf, r in results.items()}, f, ensure_ascii=False, indent=2)

with open(os.path.join(HERE, "nvda_pattern_opt.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(out_lines) + "\n")
print(f"\n[完成] 报告已写入 {HERE}/nvda_pattern_opt.txt")
