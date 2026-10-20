# -*- coding: utf-8 -*-
"""形态识别页 BTC 1h 链路 (ST 10x3.0, sl3%pct single) + 宽幅震荡闸门下，
   扫 tp1 止盈档位 2%/3%/4% (tp1_ratio 固定 50%)，看全样本与 2026 YTD 收益。
   闸门：ER<0.22 且 ATR波动率>=1.0 -> 禁止开仓。
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

CFG = os.path.join(BACKEND, "backtest", "BTC", "btc_config.json")
DB, TF = "BTC-USDT", "1H"
ST_P, ST_M = 10, 3.0
ER_CAP, VOL_FLOOR = 0.22, 1.0
DISABLE_PCT = 999.0

with open(CFG, encoding="utf-8") as f:
    C = json.load(f)
sc0, pr, er0 = _extract(C)
INIT = float(sc0["margin_usdt"]) * int(sc0["leverage"])

CAND = {"1H": _load_tf(DB, "1H")}
for t in ("15m", "1h", "4h", "1d"):
    a = _load_tf(DB, t)
    if a:
        CAND[t] = a
print(f"{DB} {TF} {len(CAND['1H'])}根  INIT={INIT:.0f}U", flush=True)


def build(tp1):
    sc = copy.deepcopy(sc0)
    sc["allow_tfs"] = [TF]
    sc["use_scoring"] = False
    sc["min_score"] = 0
    sc["allow_grades"] = ["A", "B", "C"]
    for k in ("er_min", "er_trend", "er_weak_min", "er_hide_below"):
        sc[k] = 0.0
    for k in ("mtf_filter_enabled", "range_filter_enabled",
              "atr_filter_enabled", "adx_filter_enabled", "filter_v3"):
        sc[k] = False
    sc["quick_enabled"] = False
    p = copy.deepcopy(pr)
    p["periods"], p["multiplier"], p["src"], p["change_atr"] = ST_P, ST_M, "hl2", True
    e = copy.deepcopy(er0)
    e.update({"tp1_pct": tp1, "tp1_ratio": 50.0, "sl_mode": "pct", "sl_pct": 3.0,
              "move_sl_to_entry": True, "trail_with_st": True, "reverse_close": False,
              "max_loss_enabled": False,
              "tp2_pct": DISABLE_PCT, "tp2_ratio": 0.0,
              "tp3_pct": DISABLE_PCT, "tp3_ratio": 0.0, "tp3_mode": "pct"})
    return sc, p, e


def churn_gate(window, sig):
    er = efficiency_ratio(window)
    av = atr_volatility(window)
    if er is None or av is None:
        return True
    return not (er < ER_CAP and av >= VOL_FLOOR)


def year_of(ts):
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).year


print(f"形态识别页链路 ST={ST_P}/{ST_M} sl3%pct(single)  闸门 ER<{ER_CAP} & ATR>={VOL_FLOOR}")
print(f"{'组':18s}{'全样本%':>10s}{'2026%':>10s}{'26笔':>6s}{'26胜%':>7s}{'全DD%':>8s}{'全笔':>6s}")
print("-" * 74)

for tp1 in (2.0, 3.0, 4.0):
    for tag, g in [("基线", None), ("闸门", churn_gate)]:
        sc, p, e = build(tp1)
        r = run_core(DB, sc, p, e, tf=TF, candles=CAND, extra_gate=g)
        tr = [t for t in r["trades"] if t.get("entry_ts")]
        full = r["all"]["ret_pct"] if r["all"] else 0.0
        t26 = [t for t in tr if year_of(t["entry_ts"]) == 2026]
        y26 = sum(t["pnl"] for t in t26) / INIT * 100
        wr26 = (sum(1 for t in t26 if t["pnl"] > 0) / len(t26) * 100) if t26 else 0.0
        print(f"tp1={tp1:.0f}% {tag:6s}{full:+10.1f}{y26:+10.1f}{len(t26):6d}{wr26:7.1f}{r['max_dd']:8.1f}{len(tr):6d}")

print("\n(tp1_ratio 固定 50%；2%/3% 止盈更快触达，单笔更小但笔数/胜率通常更高)")
