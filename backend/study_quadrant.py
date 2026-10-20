# -*- coding: utf-8 -*-
"""二维象限分析：ER(趋势性) × ATR波动率

study_churn 定位病因=极端止损大出血；study_fix 证明调止损宽窄无效
（亏损由"价格穿越ST线的总幅度"决定）。剩下的唯一出路=开仓前判定震荡象限。

本脚本按 开仓时 [ER 档] × [ATR波动率档] 分象限，统计每象限的
盈亏 / 极端止损笔数 / 极端止损亏损，验证：
  「平坦ER(<0.15) + 波动扩张(atr_r>=1.0)」是不是 SuperTrend 的坟墓？

运行：python study_quadrant.py
"""
import os, sys, json
from collections import defaultdict
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
DB_SYMBOL, TF = "BTC-USDT", "1h"

with open(CFG, encoding="utf-8") as f:
    C = json.load(f)
sc, pr, er = _extract(C)
candles = {"15m": _load_tf(DB_SYMBOL, "15m"),
           "1h": _load_tf(DB_SYMBOL, "1h"),
           "4h": _load_tf(DB_SYMBOL, "4h")}
INIT = float(sc["margin_usdt"]) * int(sc["leverage"])

r = run_core(DB_SYMBOL, sc, pr, er, tf=TF, candles=candles)
klines, trades = r["klines"], r["trades"]
ts2i = {c["ts"]: i for i, c in enumerate(klines)}
for t in trades:
    i = ts2i.get(t["entry_ts"])
    if i is None or i < 60:
        t["er"] = t["atr_r"] = None
        continue
    w = klines[:i + 1]
    t["er"] = efficiency_ratio(w)
    t["atr_r"] = atr_volatility(w)


def quad(t):
    e, a = t["er"], t["atr_r"]
    if e is None or a is None:
        return "未知"
    ee = "平坦ER<.15" if e < 0.15 else ("边缘.15-.3" if e < 0.30 else "趋势>.30")
    # 波动率细分：无波动(<0.9) / 小波动(0.9-1.2) / 波动扩张(>1.2)
    vv = "萎缩<.9" if a < 0.9 else ("波动.9-1.2" if a < 1.2 else "扩张>1.2")
    return f"{ee} | {vv}"


Q = defaultdict(lambda: {"n": 0, "pnl": 0.0, "win": 0, "ext_n": 0, "ext_pnl": 0.0})
for t in trades:
    q = Q[quad(t)]
    q["n"] += 1; q["pnl"] += t["pnl"]; q["win"] += 1 if t["pnl"] > 0 else 0
    if t["reason"] == "极端止损":
        q["ext_n"] += 1; q["ext_pnl"] += t["pnl"]

print(f"INIT={INIT:.0f}U  总记录 {len(trades)}  全样本 {r['all']['ret_pct']:+.2f}%")
print(f"{'象限 (开仓ER | 开仓ATR波动)':32s}{'笔数':>5s}{'盈亏U':>10s}{'收益%':>8s}"
      f"{'胜率%':>6s}{'极端笔':>6s}{'极端U':>10s}")
print("-" * 80)
# 排序：先按ER档，再按波动档
order = ["平坦ER<.15", "边缘.15-.3", "趋势>.30"]
vol = ["萎缩<.9", "波动.9-1.2", "扩张>1.2"]
for ee in order:
    for vv in vol:
        key = f"{ee} | {vv}"
        if key in Q:
            q = Q[key]
            print(f"{key:32s}{q['n']:5d}{q['pnl']:+10.2f}{q['pnl'] / INIT * 100:+8.2f}"
                  f"{q['win'] / q['n'] * 100:6.0f}{q['ext_n']:6d}{q['ext_pnl']:+10.2f}")
    print("-" * 80)

# 汇总：平坦(ER<0.15) 整体 vs 其它
print("\n汇总：平坦 ER<0.15 整体 vs ER>=0.15")
for label, pred in [("平坦 ER<0.15", lambda t: t["er"] is not None and t["er"] < 0.15),
                    ("ER>=0.15", lambda t: t["er"] is None or t["er"] >= 0.15)]:
    rows = [t for t in trades if pred(t)]
    p = sum(t["pnl"] for t in rows)
    ext = [t for t in rows if t["reason"] == "极端止损"]
    print(f"  {label:14s} {len(rows):4d} 笔 {p:+9.2f}U ({p / INIT * 100:+6.2f}%) "
          f"极端 {len(ext)} 笔 {sum(t['pnl'] for t in ext):+.2f}U")

print("\n汇总：平坦(ER<0.15) 且 波动扩张(atr_r>=1.0) —— 疑似坟墓象限")
tomb = [t for t in trades if t["er"] is not None and t["er"] < 0.15
        and t["atr_r"] is not None and t["atr_r"] >= 1.0]
if tomb:
    p = sum(t["pnl"] for t in tomb)
    ext = [t for t in tomb if t["reason"] == "极端止损"]
    print(f"  {len(tomb)} 笔 {p:+.2f}U ({p / INIT * 100:+.2f}%) "
          f"极端 {len(ext)} 笔 {sum(t['pnl'] for t in ext):+.2f}U")
    # 剔除该象限后整体收益
    rest = sum(t["pnl"] for t in trades if t not in tomb)
    print(f"  若整段跳过该象限 → 其余交易合计 {rest:+.2f}U ({rest / INIT * 100:+.2f}%)")
