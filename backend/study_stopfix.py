# -*- coding: utf-8 -*-
"""最后试探：二维闸门已清场坟墓单后，收紧止损能否救剩余 18 笔极端止损？

study_residual 结论：剩余亏损=18笔极端止损(-396U)，横跨四象限=趋势固有成本。
但那是基线(有坟墓单)时收紧止损无效(study_fix)。现在坟墓单已被闸门切除、
无污染，值得再验一次：闸门固定，叠加不同 sl/max_loss 档，看能否
在不伤 C象限(+918U利润主力) 的前提下，减少这 18 笔的失血。

运行：python study_stopfix.py
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
DB, TF = "BTC-USDT", "1H"
ER_CAP, VOL = 0.22, 1.0
INIT = 600.0

SEGS = [(1514764800000, 1577836800000, "2018-19"),
        (1577836800000, 1640995200000, "2020-21"),
        (1640995200000, 1704067200000, "2022-23"),
        (1704067200000, 1790000000000, "2024-26")]


def make_gate(ec, vf):
    def g(w, sig):
        e = efficiency_ratio(w)
        a = atr_volatility(w)
        if e is None or a is None:
            return True
        return not (e < ec and a >= vf)
    return g


with open(CFG, encoding="utf-8") as f:
    C = json.load(f)
sc, pr, er = _extract(C)
sc["allow_tfs"] = [TF]
candles = {TF: _load_tf(DB, TF)}
for t in ["15m", "1h", "4h", "1d"]:
    arr = _load_tf(DB, t)
    if arr:
        candles[t] = arr

VARIANTS = [
    ("闸门 sl3/max3(现)",  {}),
    ("闸门 max1.5",        {"max_loss_pct": 1.5}),
    ("闸门 sl2/max2",      {"sl_pct": 2.0, "max_loss_pct": 2.0}),
    ("闸门 sl1.5/max1.5",  {"sl_pct": 1.5, "max_loss_pct": 1.5}),
]

print(f"1H 2018-2026  二维闸门 ER<{ER_CAP}&atr>={VOL} 固定, 只改止损档")
print(f"{'变体':20s}{'全%':>8s}{'DD%':>7s}{'极端笔':>6s}{'极端U':>9s}"
      f"{'18-19':>8s}{'20-21':>8s}{'22-23':>8s}{'24-26':>8s}")
print("-" * 84)
for name, over in VARIANTS:
    er2 = dict(er)
    er2.update(over)
    r = run_core(DB, sc, pr, er2, tf=TF, candles=candles, extra_gate=make_gate(ER_CAP, VOL))
    tr = r["trades"]
    ext = [t for t in tr if t["reason"] == "极端止损"]
    ext_pnl = sum(t["pnl"] for t in ext)
    segv = []
    for t0, t1, _ in SEGS:
        p = sum(t["pnl"] for t in tr if t0 <= t["entry_ts"] < t1) / INIT * 100
        segv.append(p)
    print(f"{name:20s}{r['all']['ret_pct']:+8.1f}{r['max_dd']:7.1f}{len(ext):6d}"
          f"{ext_pnl:+9.1f}{segv[0]:+8.1f}{segv[1]:+8.1f}{segv[2]:+8.1f}{segv[3]:+8.1f}")

print("\n说明: 关注 22-23 段(原-17%)能否改善, 且 20-21/24-26/18-19 不被伤。")
print("      若全线无改善或伤及利润主力, 则确认剩余亏损是趋势固有成本, 应收手。")
