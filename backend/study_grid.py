# -*- coding: utf-8 -*-
"""阈值网格 + 分时段交叉验证：找稳健阈值带，而非过拟合单点

对每个 (ER_cap, atr_floor) 真跑回测（序列会变，必须真跑，不能离线过滤），
再用 3 个切点(0.6/0.7/0.8)分别算震荡段(IS)与趋势段(OOS)收益。
稳健判据 = 最差切点下 IS 明显改善(>-10%) 且 OOS 仍为正。

基准：IS=-46.60%  OOS=+16.76%  全=-29.84%  DD=74.92%

运行：python study_grid.py
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
INIT = float(sc["margin_usdt"]) * int(sc["leverage"])


def make_gate(er_cap, vol_floor):
    def g(window, sig):
        e = efficiency_ratio(window)
        a = atr_volatility(window)
        if e is None or a is None:
            return True
        return not (e < er_cap and a >= vol_floor)
    return g


def seg(trades, cut):
    ordered = sorted([t for t in trades if t.get("entry_ts")], key=lambda t: t["entry_ts"])
    if not ordered:
        return 0.0, 0.0
    k = int(len(ordered) * cut)
    is_r = sum(t["pnl"] for t in ordered[:k]) / INIT * 100
    oos_r = sum(t["pnl"] for t in ordered[k:]) / INIT * 100
    return is_r, oos_r


ER_CAPS = [0.16, 0.18, 0.20, 0.22, 0.25]
ATRS = [0.9, 1.0, 1.1, 1.2]
CUTS = [0.6, 0.7, 0.8]

results = []
print(f"INIT={INIT:.0f}U  {DB_SYMBOL} {TF}  K线={len(candles['1h'])}  "
      f"grid {len(ER_CAPS)}x{len(ATRS)}={len(ER_CAPS)*len(ATRS)} 组", flush=True)

for ec in ER_CAPS:
    for af in ATRS:
        r = run_core(DB_SYMBOL, sc, pr, er, tf=TF, candles=candles,
                     extra_gate=make_gate(ec, af))
        tr = r["trades"]
        ext = [t for t in tr if t["reason"] == "极端止损"]
        segs = {c: seg(tr, c) for c in CUTS}
        results.append({
            "ec": ec, "af": af, "full": r["all"]["ret_pct"], "dd": r["max_dd"],
            "n": len(tr), "ext_n": len(ext), "ext_pnl": sum(t["pnl"] for t in ext),
            "segs": segs, "win": r["all"]["winrate"],
        })
        print(f"  ER<{ec} atr>={af}: 全{r['all']['ret_pct']:+.1f}% "
              f"DD{r['max_dd']:.0f}% n{len(tr)}", flush=True)

print("\n===== 主表（切点0.7）=====")
print(f"{'ER_cap':>7s}{'atr_fl':>7s}{'全%':>8s}{'IS%':>8s}{'OOS%':>8s}"
      f"{'DD%':>7s}{'成交':>5s}{'极端':>5s}{'胜率%':>6s}")
print("-" * 66)
for x in results:
    is7, oos7 = x["segs"][0.7]
    print(f"{x['ec']:7.2f}{x['af']:7.2f}{x['full']:+8.1f}{is7:+8.1f}{oos7:+8.1f}"
          f"{x['dd']:7.1f}{x['n']:5d}{x['ext_n']:5d}{x['win']:6.1f}")

print("\n===== 稳健性：三切点 IS% / OOS%（看最差切点）=====")
print(f"{'ER_cap':>7s}{'atr_fl':>7s} | {'IS@.6':>7s}{'IS@.7':>7s}{'IS@.8':>7s}"
      f"{'worstIS':>9s} | {'OOS@.6':>8s}{'OOS@.7':>8s}{'OOS@.8':>8s}{'worstOOS':>9s}  ok?")
print("-" * 98)
robust = []
for x in results:
    isv = [x["segs"][c][0] for c in CUTS]
    oov = [x["segs"][c][1] for c in CUTS]
    w_is, w_oos = min(isv), min(oov)
    ok = (w_is > -10.0) and (w_oos > 0.0)
    if ok:
        robust.append(x)
    print(f"{x['ec']:7.2f}{x['af']:7.2f} | {isv[0]:+7.1f}{isv[1]:+7.1f}{isv[2]:+7.1f}"
          f"{w_is:+9.1f} | {oov[0]:+8.1f}{oov[1]:+8.1f}{oov[2]:+8.1f}{w_oos:+9.1f}"
          f"  {'YES' if ok else ''}")

print("\n===== 稳健带结论 =====")
print("基准: IS=-46.6% OOS=+16.8% 全=-29.8% DD=74.9%")
print("稳健判据: 最差切点 IS>-10% 且 OOS>0")
if robust:
    print(f"命中 {len(robust)} 组:")
    for x in sorted(robust, key=lambda z: -z["full"]):
        is7, oos7 = x["segs"][0.7]
        print(f"  ER<{x['ec']} atr>={x['af']}: 全{x['full']:+.1f}% "
              f"IS@.7{is7:+.1f}% OOS@.7{oos7:+.1f}% DD{x['dd']:.1f}% n{x['n']} 极端{x['ext_n']}")
else:
    print("无任何阈值同时满足 IS>-10% 且 OOS>0 —— 需放宽判据或换思路")
