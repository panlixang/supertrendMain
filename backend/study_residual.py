# -*- coding: utf-8 -*-
"""深挖剩余亏损：闸门(ER<0.22 & atr>=1.0)之后，1H 上剩下的亏损残留在哪

闸门砍掉了「平坦ER(<0.22)+波动扩张(atr>=1.0)」象限。放过的交易只有两类：
  A/B: ER>=0.22 (任意波动)        —— 趋势单
  C  : ER<0.22 且 atr<1.0         —— 平坦+平稳(此前认为安全)
本脚本对闸门后 1H 的成交，逐笔标注 ER/atr/reason，回答：
  1) 剩余亏损来自哪种平仓(reason)？
  2) 剩余亏损集中在哪个象限？是坟墓漏网(可再砍) 还是 趋势固有成本(不该砍)？
  3) 剩余极端止损的开仓 ER/atr 画像
  4) 分段(2018-19/2022-23 等)的亏损结构

运行：python study_residual.py
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
from collections import defaultdict, Counter  # noqa: E402

CFG = os.path.join(BACKEND, "backtest", "BTC", "btc_config.json")
DB, TF = "BTC-USDT", "1H"
ER_CAP, VOL = 0.22, 1.0
INIT = 600.0


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

r = run_core(DB, sc, pr, er, tf=TF, candles=candles, extra_gate=make_gate(ER_CAP, VOL))
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

print(f"闸门后 1H: {len(trades)} 笔  全 {r['all']['ret_pct']:+.1f}%  DD {r['max_dd']:.1f}%")


def quad(t):
    e, a = t["er"], t["atr_r"]
    if e is None or a is None:
        return "未知"
    if e >= ER_CAP:
        return "A趋势ER>=.22" + ("(扩张)" if a >= 1.0 else "(平稳)")
    return "C平坦ER<.22(平稳)"


def grp(rows, keyfn):
    d = defaultdict(lambda: [0, 0.0, 0])
    for t in rows:
        b = d[keyfn(t)]
        b[0] += 1; b[1] += t["pnl"]; b[2] += 1 if t["pnl"] > 0 else 0
    return d


print("\n===== 1) 剩余亏损按平仓 reason =====")
for k, (n, p, w) in sorted(grp(trades, lambda t: t["reason"]).items(), key=lambda x: -x[1][1]):
    print(f"  {k:10s} {n:4d} 笔  {p:+9.2f}U  均{p / n:+7.2f}U  胜率 {w / n * 100:3.0f}%")

print("\n===== 2) 剩余亏损按象限（A趋势 vs C平坦平稳）=====")
for k in ["A趋势ER>=.22(扩张)", "A趋势ER>=.22(平稳)", "C平坦ER<.22(平稳)", "未知"]:
    d = grp(trades, quad)
    if k in d:
        n, p, w = d[k]
        ext = [t for t in trades if quad(t) == k and t["reason"] == "极端止损"]
        print(f"  {k:20s} {n:4d} 笔  {p:+9.2f}U ({p / INIT * 100:+6.2f}%)  胜率 {w / n * 100:3.0f}%"
              f"  极端 {len(ext)} 笔 {sum(t['pnl'] for t in ext):+.2f}U")

print("\n===== 3) 剩余极端止损的开仓 ER/atr 画像 =====")
ext = [t for t in trades if t["reason"] == "极端止损"]
print(f"  共 {len(ext)} 笔, 累计 {sum(t['pnl'] for t in ext):+.2f}U")
ers = [t["er"] for t in ext if t["er"] is not None]
ats = [t["atr_r"] for t in ext if t["atr_r"] is not None]
if ers:
    import statistics
    print(f"  开仓ER 均值 {statistics.mean(ers):.3f} 中位 {statistics.median(ers):.3f} "
          f"[{min(ers):.3f},{max(ers):.3f}]")
    print(f"  开仓atr 均值 {statistics.mean(ats):.3f} 中位 {statistics.median(ats):.3f} "
          f"[{min(ats):.3f},{max(ats):.3f}]")
    print(f"  ER分布: {dict(Counter(('趋势>=.22' if e >= ER_CAP else '平坦<.22') for e in ers))}")
    print(f"  atr分布: {dict(Counter(('扩张>=1.0' if a >= 1.0 else '平稳<1.0') for a in ats))}")
print("  最亏 6 笔:")
for t in sorted(ext, key=lambda z: z["pnl"])[:6]:
    print(f"    {t['side']:5s} {t['pnl']:+8.2f}U  开仓ER={t['er']} atr={t['atr_r']}")

print("\n===== 4) 分段亏损结构（定位 2018-19 / 2022-23 为何还亏）=====")
bounds = [(1514764800000, 1577836800000, "2018-19"),
          (1577836800000, 1640995200000, "2020-21"),
          (1640995200000, 1704067200000, "2022-23"),
          (1704067200000, 1790000000000, "2024-26")]
for t0, t1, lab in bounds:
    rows = [t for t in trades if t0 <= t["entry_ts"] < t1]
    p = sum(t["pnl"] for t in rows)
    print(f"  [{lab}] {len(rows)} 笔 {p:+.2f}U ({p / INIT * 100:+.2f}%)")
    for k, (n, pp, w) in sorted(grp(rows, lambda t: t["reason"]).items(), key=lambda x: -x[1][1]):
        if pp < 0 or k == "极端止损":
            print(f"      {k:10s} {n:3d} 笔  {pp:+8.2f}U")
