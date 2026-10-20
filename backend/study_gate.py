# -*- coding: utf-8 -*-
"""前瞻闸门验证：把「平坦ER×波动扩张」二维判定接进回测闸门

与 study_quadrant 的事后砍单不同，本脚本用 engine.run_core 的 extra_gate 钩子，
在每次信号判定时前瞻性地决定是否开仓，跑真实权益曲线。

判定规则（开仓瞬间的窗口）：
  宽幅震荡坟墓 = ER < er_cap 且 ATR波动率 atr_r >= vol_floor  -> 禁开仓

对比几组阈值，确认能否复现象限分析预测的改善（-29.84% -> 约 -1.64%）。

运行：python study_gate.py
"""
import os, sys, json
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


def make_gate(er_cap, vol_floor):
    """返回 extra_gate：平坦(ER<cap)且波动扩张(atr_r>=floor)时禁开仓。"""
    def g(window, sig):
        er = efficiency_ratio(window)
        at = atr_volatility(window)
        if er is None or at is None:
            return True
        return not (er < er_cap and at >= vol_floor)
    return g


VARIANTS = [
    ("基线(无闸门)",        None),
    ("v1 ER<.15 & atr>=1.0", make_gate(0.15, 1.0)),
    ("v2 ER<.15 & atr>=0.9", make_gate(0.15, 0.9)),
    ("v3 ER<.20 & atr>=1.0", make_gate(0.20, 1.0)),
    ("v4 ER<.12 & atr>=1.0", make_gate(0.12, 1.0)),
]

print(f"INIT={float(sc['margin_usdt']) * int(sc['leverage']):.0f}U  "
      f"{DB_SYMBOL} {TF}  K线={len(candles['1h'])}")
print(f"{'闸门':24s}{'全%':>9s}{'IS%':>9s}{'OOS%':>9s}{'DD%':>8s}"
      f"{'成交':>6s}{'极端笔':>7s}{'极端U':>10s}{'胜率%':>7s}")
print("-" * 88)
for name, gate in VARIANTS:
    r = run_core(DB_SYMBOL, sc, pr, er, tf=TF, candles=candles, extra_gate=gate)
    tr = r["trades"]
    ext = [t for t in tr if t["reason"] == "极端止损"]
    A, I, O = r["all"], r["is"], r["oos"]
    print(f"{name:24s}{A['ret_pct']:+9.2f}{I['ret_pct']:+9.2f}{O['ret_pct']:+9.2f}"
          f"{r['max_dd']:8.2f}{len(tr):6d}{len(ext):7d}"
          f"{sum(t['pnl'] for t in ext):+10.2f}{A['winrate']:7.1f}")

print("\n注：成交=平仓记录数；禁开仓会同时减少成交笔数与极端止损来源。")
print("    IS%=前70%(震荡段)  OOS%=后30%(趋势段)")
