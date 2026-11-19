# -*- coding: utf-8 -*-
"""形态识别页链路验证：二维闸门 ER<0.22 & atr>=1.0 能否套到形态识别页

形态识别页(pattern_trade.py)与首页的关键差异：
  1) 信号源同为 SuperTrend 翻转，但参数是 原始 10/3.0（首页 BTC 是 11/4.0）
  2) 形态识别页几乎没有 ER/评分闸门（BTC: filter_v3=false, block_4h=false）
     -> 基本是「ST 翻转就开仓」，比首页更容易磨损
  3) 出场各自独立（BTC 4h: tp1 4%/50% + sl 3%pct；1h 品种: tp1 1.5%/70%）

本脚本用形态识别页的真实参数构造配置，跑 基线 vs 加二维闸门，
回答「在形态识别页自己的信号源/周期上，这道闸门到底有没有用」。

运行：python study_pattern_gate.py
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
ER_CAP, VOL_FLOOR = 0.22, 1.0
# 形态识别页是单档出场(TP1 + 保本 + ST跟踪)，需用极大值禁用 tp2/tp3。
# 注意：不能设 0.0 —— 那会让 tp2 永久满足、抢在 tp1 前，等于废掉 tp1 参数。
DISABLE_PCT = 999.0

# 形态识别页 pattern_trade.json 的真实配置（symbol 去掉 -SWAP 才是 db 里的名字）
# (展示名, db_symbol, tf, margin, lev, 出场参数)
PATTERN = [
    ("BTC",     "BTC-USDT",     "4h", 100.0, 10,
     {"tp1_pct": 4.0, "tp1_ratio": 50.0, "sl_mode": "pct", "sl_pct": 3.0}),
    ("ETH",     "ETH-USDT",     "4h", 50.0, 10,
     {"tp1_pct": 5.0, "tp1_ratio": 50.0, "sl_mode": "pct", "sl_pct": 5.0}),
    ("NVDA",    "NVDA-USDT",    "1h", 50.0, 10,
     {"tp1_pct": 1.5, "tp1_ratio": 70.0, "sl_mode": "pct", "sl_pct": 3.0}),
    ("CL",      "CL-USDT",      "1h", 50.0, 10,
     {"tp1_pct": 1.5, "tp1_ratio": 70.0, "sl_mode": "st", "sl_pct": 2.0}),
    ("SKHYNIX", "SKHYNIX-USDT", "1h", 5.0, 10,
     {"tp1_pct": 1.5, "tp1_ratio": 70.0, "sl_mode": "pct", "sl_pct": 3.0}),
    ("TSLA",    "TSLA-USDT",    "1h", 50.0, 10,
     {"tp1_pct": 1.5, "tp1_ratio": 70.0, "sl_mode": "pct", "sl_pct": 3.0}),
]

with open(CFG, encoding="utf-8") as f:
    BASE = json.load(f)
_sc0, _pr0, _er0 = _extract(BASE)


def make_gate(ec, vf):
    def g(w, sig):
        e = efficiency_ratio(w)
        a = atr_volatility(w)
        if e is None or a is None:
            return True
        return not (e < ec and a >= vf)
    return g


def build(tf, margin, lev, exit_kw):
    """构造形态识别页风格配置：ST 10/3.0 + 无 ER/评分闸门 + 指定出场。"""
    sc = copy.deepcopy(_sc0)
    sc["allow_tfs"] = [tf]
    sc["margin_usdt"] = margin
    sc["leverage"] = lev
    # 关掉所有 ER / 评分 / 过滤闸门（形态识别页 BTC 就是裸奔 ST 翻转）
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
    pr["periods"] = 10          # 形态识别页 ST_PERIODS
    pr["multiplier"] = 3.0      # 形态识别页 ST_MULTIPLIER
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


def run(db_symbol, tf, sc, pr, er, gate):
    candles = None if tf == "4h" else {"15m": _load_tf(db_symbol, "15m"),
                                       "1h": _load_tf(db_symbol, "1h"),
                                       "4h": _load_tf(db_symbol, "4h")}
    return run_core(db_symbol, sc, pr, er, tf=tf, candles=candles, extra_gate=gate)


def stats(r, INIT):
    tr = r["trades"]
    ordered = sorted([t for t in tr if t.get("entry_ts")], key=lambda t: t["entry_ts"])
    k = int(len(ordered) * 0.7)
    full = r["all"]["ret_pct"] if r["all"] else 0.0
    isr = sum(t["pnl"] for t in ordered[:k]) / INIT * 100 if INIT else 0
    oos = sum(t["pnl"] for t in ordered[k:]) / INIT * 100 if INIT else 0
    return full, isr, oos, r["max_dd"], len(tr)


print(f"形态识别页链路  ST=10/3.0  无ER闸门  闸门阈值 ER<{ER_CAP} & atr>={VOL_FLOOR}")
print(f"{'品种':8s}{'周期':5s}{'基全%':>8s}{'基DD':>7s}{'基n':>5s}"
      f"{'闸全%':>8s}{'闸DD':>7s}{'闸n':>5s}{'全Δ':>8s}{'DDΔ':>7s}  判定")
print("-" * 78)

for name, db_symbol, tf, margin, lev, exit_kw in PATTERN:
    ks1 = _load_tf(db_symbol, "15m")
    ks = _load_tf(db_symbol, tf)
    if not ks:
        print(f"{name:8s}{tf:5s}  (db 无 {tf} 数据，跳过)")
        continue
    sc, pr, er = build(tf, margin, lev, exit_kw)
    INIT = margin * lev
    try:
        b = run(db_symbol, tf, sc, pr, er, None)
        g = run(db_symbol, tf, sc, pr, er, make_gate(ER_CAP, VOL_FLOOR))
    except Exception as e:
        print(f"{name:8s}{tf:5s}  出错: {e}")
        continue
    bf, bi, bo, bd, bn = stats(b, INIT)
    gf, gi, go, gd, gn = stats(g, INIT)
    ok = (gf > bf + 3) and (gd <= bd + 3)
    verdict = "有效YES" if ok else ("有害" if gf < bf - 3 else "无改善")
    print(f"{name:8s}{tf:5s}{bf:+8.1f}{bd:7.1f}{bn:5d}{gf:+8.1f}{gd:7.1f}{gn:5d}"
          f"{gf - bf:+8.1f}{gd - bd:+7.1f}  {verdict}"
          f"   (K线{len(ks)})")

print("\n注: 模拟「形态识别页 ST 信号 + 无 ER/评分闸门」的裸奔基线。")
print("    filter_v3=true 的品种(NVDA/CL/SKHYNIX/TSLA)线上还有 4 条规则额外过滤,")
print("    本脚本未模拟该层, 故其基线笔数偏多; 但不影响「加闸门 vs 不加」的对比方向。")
