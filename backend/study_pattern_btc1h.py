# -*- coding: utf-8 -*-
"""关键补验：形态识别页链路（ST 固定 10/3.0）+ BTC 1h，加二维闸门有没有问题

前面的验证缺了最关键一格：
  首页链路      ST 11/4.0 + BTC 1h  -> 闸门救命(+157%)   [已验]
  形态识别页    ST 10/3.0 + BTC 4h  -> 闸门毁灭(-89%)    [已验]
  形态识别页    ST 10/3.0 + BTC 1h  -> ???               [本脚本]

用最长 BTC 1H(2018起,76361根) 数据，无 ER/评分闸门（形态识别页裸奔），
试 3 组出场，跑 基线 vs 加闸门(ER<0.22 & atr>=1.0)。

运行：python study_pattern_btc1h.py
"""
import os, sys, json, copy
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(BACKEND, "backtest"))

from engine import run_core, _extract, _load_tf   # noqa: E402
from regime import efficiency_ratio, atr_volatility  # noqa: E402

CFG = os.path.join(BACKEND, "backtest", "BTC", "btc_config.json")
DB, TF = "BTC-USDT", "1H"
# 形态识别页是单档出场(TP1 + 保本 + ST跟踪)，需用极大值禁用 tp2/tp3。
# 注意：不能设 0.0 —— 那会让 tp2 永久满足、抢在 tp1 前，等于废掉 tp1 参数。
DISABLE_PCT = 999.0
ER_CAP, VOL_FLOOR = 0.22, 1.0
MARGIN, LEV = 100.0, 10
INIT = MARGIN * LEV

with open(CFG, encoding="utf-8") as f:
    BASE = json.load(f)
_sc0, _pr0, _er0 = _extract(BASE)

# 3 组出场变体（形态识别页各品种的出场差异很大，1h 该用哪档不确定，都试）
EXITS = [
    ("BTC现档 tp1 4%/50% sl3%pct", {"tp1_pct": 4.0, "tp1_ratio": 50.0,
                                    "sl_mode": "pct", "sl_pct": 3.0}),
    ("1h统一 tp1 1.5%/70% sl3%pct", {"tp1_pct": 1.5, "tp1_ratio": 70.0,
                                     "sl_mode": "pct", "sl_pct": 3.0}),
    ("1h统一 tp1 1.5%/70% sl=ST", {"tp1_pct": 1.5, "tp1_ratio": 70.0,
                                   "sl_mode": "st", "sl_pct": 2.0}),
]


def make_gate(ec, vf):
    def g(w, sig):
        e = efficiency_ratio(w)
        a = atr_volatility(w)
        if e is None or a is None:
            return True
        return not (e < ec and a >= vf)
    return g


def build(exit_kw):
    sc = copy.deepcopy(_sc0)
    sc["allow_tfs"] = [TF]
    sc["margin_usdt"] = MARGIN
    sc["leverage"] = LEV
    sc["use_scoring"] = False
    sc["min_score"] = 0
    sc["allow_grades"] = ["A", "B", "C"]
    for k in ("er_min", "er_trend", "er_weak_min", "er_hide_below"):
        sc[k] = 0.0
    for k in ("mtf_filter_enabled", "range_filter_enabled",
              "atr_filter_enabled", "adx_filter_enabled", "filter_v3"):
        sc[k] = False
    sc["quick_enabled"] = False
    pr = copy.deepcopy(_pr0)
    pr["periods"] = 10          # 形态识别页固定 ST_PERIODS
    pr["multiplier"] = 3.0      # 形态识别页固定 ST_MULTIPLIER
    pr["src"] = "hl2"
    pr["change_atr"] = True
    er = copy.deepcopy(_er0)
    er.update({
        "tp1_pct": exit_kw["tp1_pct"], "tp1_ratio": exit_kw["tp1_ratio"],
        "sl_mode": exit_kw["sl_mode"], "sl_pct": exit_kw["sl_pct"],
        "move_sl_to_entry": True, "trail_with_st": True,
        "reverse_close": False, "max_loss_enabled": False,
        "tp2_pct": DISABLE_PCT, "tp2_ratio": 0.0,
        "tp3_pct": DISABLE_PCT, "tp3_ratio": 0.0, "tp3_mode": "pct",
    })
    return sc, pr, er


candles = {TF: _load_tf(DB, TF)}
for t in ["15m", "1h", "4h", "1d"]:
    a = _load_tf(DB, t)
    if a:
        candles[t] = a
print(f"数据 {DB} {TF} {len(candles[TF])} 根  ST=10/3.0  无ER闸门  "
      f"闸门 ER<{ER_CAP} & atr>={VOL_FLOOR}", flush=True)


def stats(r):
    tr = r["trades"]
    ordered = sorted([t for t in tr if t.get("entry_ts")], key=lambda t: t["entry_ts"])
    k = int(len(ordered) * 0.7)
    full = r["all"]["ret_pct"] if r["all"] else 0.0
    isr = sum(t["pnl"] for t in ordered[:k]) / INIT * 100
    oos = sum(t["pnl"] for t in ordered[k:]) / INIT * 100
    ext = [t for t in tr if t["reason"] == "极端止损"]
    return full, isr, oos, r["max_dd"], len(tr), len(ext), sum(t["pnl"] for t in ext)


print(f"{'出场变体':30s}{'基全%':>8s}{'基DD':>7s}{'基n':>5s}"
      f"{'闸全%':>8s}{'闸DD':>7s}{'闸n':>5s}{'全Δ':>8s}{'DDΔ':>7s}  判定")
print("-" * 100)
for name, exit_kw in EXITS:
    sc, pr, er = build(exit_kw)
    b = run_core(DB, sc, pr, er, tf=TF, candles=candles, extra_gate=None)
    g = run_core(DB, sc, pr, er, tf=TF, candles=candles,
                 extra_gate=make_gate(ER_CAP, VOL_FLOOR))
    bf, bi, bo, bd, bn, be, bep = stats(b)
    gf, gi, go, gd, gn, ge, gep = stats(g)
    ok = (gf > bf + 3) and (gd <= bd + 3)
    verdict = "有效YES" if ok else ("有害" if gf < bf - 3 else "无改善")
    print(f"{name:30s}{bf:+8.1f}{bd:7.1f}{bn:5d}{gf:+8.1f}{gd:7.1f}{gn:5d}"
          f"{gf - bf:+8.1f}{gd - bd:+7.1f}  {verdict}")
    print(f"{'':30s}  基线: IS{bi:+.1f}% OOS{bo:+.1f}% 极端{be}笔{bep:+.0f}U | "
          f"闸门: IS{gi:+.1f}% OOS{go:+.1f}% 极端{ge}笔{gep:+.0f}U")

print("\n结论读法: 全Δ明显为正且 DD 不涨 = 可加; 全Δ为负或 DD 暴涨 = 不能加。")
