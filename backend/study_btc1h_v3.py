# -*- coding: utf-8 -*-
"""BTC改1h后：V3过滤 与 宽幅震荡闸门 是什么关系？（替代 or 互补）

背景：用户确认形态识别页走1h。已验证（8年1H口径）BTC-1h 形态识别页链路
      裸奔 +51.6% → 加闸门 +180.1%（+128.5pp，DD 46.7%→33.7%）。
      但该结论是「无V3」的裸奔 —— 而 BTC 线上 filter_v3=false，正好吻合。

新问题：若给 BTC 开 V3，闸门会不会变多余？
      V3 的 Fuse 二级本就是「极端震荡硬禁」(flip50>8 且 er20<0.15 直接 ban)，
      与本闸门功能可能重叠。必须实测，不能假设。

四组对比（同期同数据，1h 2022-2026，4h上下文完整）：
  A 裸奔（无V3无闸门）  B 闸门only  C V3only  D V3+闸门

V3 模拟完全照 pattern_trade._v3_allow 的口径：
  super_trend(10/3.0) → flips[-1] → 对上信号根 → features_from_candles(cs,i,sd,cs4)
  → v3_decide → execute。4h 上下文按信号 ts 裁剪，杜绝未来函数。

运行：python study_btc1h_v3.py
"""
import os, sys, json, copy
from datetime import datetime, timezone
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(BACKEND, "backtest"))

from engine import run_core, _extract, _load_tf          # noqa: E402
from regime import efficiency_ratio, atr_volatility      # noqa: E402
from indicators import super_trend                       # noqa: E402
import signal_v3                                         # noqa: E402

CFG = os.path.join(BACKEND, "backtest", "BTC", "btc_config.json")
DB, TF = "BTC-USDT", "1h"
ST_P, ST_M = 10, 3.0          # 形态识别页固定
ER_CAP, VOL_FLOOR = 0.22, 1.0
V3_MIN, V3_MIN4 = 120, 60     # V3 线上门槛: cs>=120, cs4>=60
DISABLE_PCT = 999.0

with open(CFG, encoding="utf-8") as f:
    C = json.load(f)
sc0, pr, er0 = _extract(C)
INIT = float(sc0["margin_usdt"]) * int(sc0["leverage"])

CAND = {t: _load_tf(DB, t) for t in ("15m", "1h", "4h")}
ALL4H = _load_tf(DB, "4h") or []
print(f"{DB} {TF} {len(CAND['1h'])}根   4h上下文 {len(ALL4H)}根   INIT={INIT:.0f}U", flush=True)


def build():
    """形态识别页链路：ST 10/3.0 + BTC真实出场(single档 tp1 4%/50% sl3%pct) + 无ER/评分闸门"""
    sc = copy.deepcopy(sc0)
    sc["allow_tfs"] = [TF]
    sc["use_scoring"] = False
    sc["min_score"] = 0
    sc["allow_grades"] = ["A", "B", "C"]
    for k in ("er_min", "er_trend", "er_weak_min", "er_hide_below"):
        sc[k] = 0.0
    for k in ("mtf_filter_enabled", "range_filter_enabled", "atr_filter_enabled",
              "adx_filter_enabled", "filter_v3"):
        sc[k] = False
    sc["quick_enabled"] = False
    p = copy.deepcopy(pr)
    p["periods"], p["multiplier"], p["src"], p["change_atr"] = ST_P, ST_M, "hl2", True
    e = copy.deepcopy(er0)
    e.update({"tp1_pct": 4.0, "tp1_ratio": 50.0, "sl_mode": "pct", "sl_pct": 3.0,
              "move_sl_to_entry": True, "trail_with_st": True, "reverse_close": False,
              "max_loss_enabled": False,
              "tp2_pct": DISABLE_PCT, "tp2_ratio": 0.0,
              "tp3_pct": DISABLE_PCT, "tp3_ratio": 0.0, "tp3_mode": "pct"})
    return sc, p, e


def churn_gate(window, sig):
    """宽幅震荡闸门：ER<0.22 且 ATR波动>=1.0 → 拦"""
    e = efficiency_ratio(window)
    a = atr_volatility(window)
    if e is None or a is None:
        return True
    return not (e < ER_CAP and a >= VOL_FLOOR)


def make_v3_gate(use_v3):
    """照 pattern_trade._v3_allow 口径模拟 V3。4h 按 ts 裁剪，杜绝未来函数。"""
    def g(window, sig):
        if not use_v3:
            return True
        if len(window) < V3_MIN:
            return True
        i = len(window) - 1
        ts = window[i]["ts"]
        cs4 = [x for x in ALL4H if x["ts"] <= ts][-300:]
        if len(cs4) < V3_MIN4:
            return True                      # 数据不足放行（同线上）
        st = super_trend([x["o"] for x in window], [x["h"] for x in window],
                         [x["l"] for x in window], [x["c"] for x in window],
                         periods=ST_P, multiplier=ST_M, change_atr=True)
        flips = st.get("flips") or []
        if not flips:
            return True
        fd = flips[-1]
        if fd["i"] != i:
            return True                      # 信号根对不上放行（同线上）
        sd = 1 if fd["type"] == "buy" else -1
        feats = signal_v3.features_from_candles(window, i, sd, cs4,
                                                st_periods=ST_P, st_mult=ST_M)
        if not feats:
            return True                      # 特征不足放行（同线上）
        v = signal_v3.v3_decide(sd, feats)
        return bool(v.get("execute"))
    return g


def make_gate(use_v3, use_churn):
    v3 = make_v3_gate(use_v3)

    def g(window, sig):
        if not v3(window, sig):
            return False
        if use_churn and not churn_gate(window, sig):
            return False
        return True
    return g


def year_of(ts):
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).year


def seg(trades, lo, hi):
    return sum(t["pnl"] for t in trades if lo <= year_of(t["entry_ts"]) <= hi) / INIT * 100


sc, p, e = build()
print(f"形态识别页链路 ST={ST_P}/{ST_M}  BTC出场 tp1 4%/50% sl3%pct(single)  闸门 {ER_CAP}/{VOL_FLOOR}")
print(f"{'组':22s}{'全%':>9s}{'22-23':>8s}{'24-26':>8s}{'DD%':>7s}{'笔数':>6s}{'胜率%':>7s}")
print("-" * 70)

out = {}
for tag, uv, uc in [("A 裸奔(无V3无闸门)", False, False),
                    ("B 闸门only", False, True),
                    ("C V3only", True, False),
                    ("D V3+闸门", True, True)]:
    g = None if (not uv and not uc) else make_gate(uv, uc)
    r = run_core(DB, sc, p, e, tf=TF, candles=CAND, extra_gate=g)
    tr = [t for t in r["trades"] if t.get("entry_ts")]
    full = r["all"]["ret_pct"] if r["all"] else 0.0
    out[tag] = full
    print(f"{tag:22s}{full:+9.1f}{seg(tr,2022,2023):+8.1f}{seg(tr,2024,2026):+8.1f}"
          f"{r['max_dd']:7.1f}{len(tr):6d}{r['all']['winrate']:7.1f}", flush=True)

print("\n===== 关系判定 =====")
a = out["A 裸奔(无V3无闸门)"]
b, c, d = out["B 闸门only"], out["C V3only"], out["D V3+闸门"]
print(f"  闸门单独贡献: {b - a:+.1f}pp   (裸奔{a:+.1f}% -> {b:+.1f}%)")
print(f"  V3  单独贡献: {c - a:+.1f}pp   (裸奔{a:+.1f}% -> {c:+.1f}%)")
print(f"  两者叠加    : {d - a:+.1f}pp   (裸奔{a:+.1f}% -> {d:+.1f}%)")
print(f"  叠加后闸门还能再加多少: {d - c:+.1f}pp  (V3only{c:+.1f}% -> V3+闸门{d:+.1f}%)")
if abs(d - c) < 5:
    print("  => 闸门在 V3 之上几乎无增益：二者高度重叠，开V3后闸门可不开")
elif d > c + 10:
    print("  => 闸门在 V3 之上仍有明显增益：互补，建议 V3+闸门 同开")
else:
    print("  => 增益有限，按品种取舍")
