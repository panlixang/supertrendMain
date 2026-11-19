# -*- coding: utf-8 -*-
"""减磨损药方对照实验（BTC）

study_churn 已定位病因：全样本 -29.84% 几乎全由 56 笔"极端止损"(-1202U)造成，
其余平仓合计 +1023U。极端止损 = max_loss 硬止损(3%)被击穿且按触发价成交(含滑点)。

本脚本做干净的 A/B：开仓信号由 ST 翻转+闸门决定、与止损参数无关，
故改止损只改变每笔的 exit，不改变 597 次开仓集合 —— 可直接对比。

变体：
  基线   sl=3.0  max_loss=3.0   (线上当前)
  A      sl=3.0  max_loss=1.5   (只收紧硬止损上限)
  B      sl=1.5  max_loss=1.5   (紧 ST 止损 + 紧硬止损)

运行：python study_fix.py
"""
import os, sys, json, sqlite3
from collections import defaultdict
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(BACKEND, "backtest"))

from engine import run_core, _extract, _load_tf   # noqa: E402

CFG = os.path.join(BACKEND, "backtest", "BTC", "btc_config.json")
DB_SYMBOL, TF = "BTC-USDT", "1h"

with open(CFG, encoding="utf-8") as f:
    C = json.load(f)
sc, pr, er = _extract(C)

candles = {"15m": _load_tf(DB_SYMBOL, "15m"),
           "1h": _load_tf(DB_SYMBOL, "1h"),
           "4h": _load_tf(DB_SYMBOL, "4h")}
INIT = float(sc["margin_usdt"]) * int(sc["leverage"])

VARIANTS = [
    ("基线  sl3.0/max3.0", {}),
    ("A max1.5(sl3.0)",    {"max_loss_pct": 1.5}),
    ("B sl1.5/max1.5",     {"sl_pct": 1.5, "max_loss_pct": 1.5}),
    ("C sl2.0/max2.0",     {"sl_pct": 2.0, "max_loss_pct": 2.0}),
]

print(f"INIT={INIT:.0f}U  品种={DB_SYMBOL} {TF}  K线={len(candles['1h'])}")
print(f"{'变体':22s}{'全%':>9s}{'IS%':>9s}{'OOS%':>9s}{'DD%':>8s}"
      f"{'极端止损笔':>10s}{'极端止损U':>12s}{'PF':>7s}{'胜率%':>7s}")
print("-" * 92)
for name, over in VARIANTS:
    er2 = dict(er)
    er2.update(over)
    r = run_core(DB_SYMBOL, sc, pr, er2, tf=TF, candles=candles)
    trades = r["trades"]
    ext = [t for t in trades if t["reason"] == "极端止损"]
    ext_pnl = sum(t["pnl"] for t in ext)
    A, I, O = r["all"], r["is"], r["oos"]
    print(f"{name:22s}{A['ret_pct']:+9.2f}{I['ret_pct']:+9.2f}{O['ret_pct']:+9.2f}"
          f"{r['max_dd']:8.2f}{len(ext):10d}{ext_pnl:+12.2f}{A['pf']:7.2f}{A['winrate']:7.1f}")

print("\n说明：全%=全样本收益  IS%=前70%(震荡段)  OOS%=后30%(趋势段)  DD%=最大回撤")
print("      极端止损U=该变体所有 max_loss 击穿笔的累计盈亏")
