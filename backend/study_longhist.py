# -*- coding: utf-8 -*-
"""更长历史稳健验证：BTC 1H(2018起) + 4h 周期

study_grid 在 1h(2022-2026) 上定出稳健带 ER_cap∈[0.20,0.25]×atr=1.0。
本脚本用未参与调参的更早历史检验阈值 0.22/1.0 是否稳健：
  - 1H 大写: 76361根, 2018-01~2026-09 (含 2018熊市/2019震荡/2020-21牛市)
  - 4h 周期: 8905根, 2022-09~2026-09
对每个周期跑 基线 vs 闸门；1H 再按 2 年分段，看未调参的 2018-2021 段是否也改善。

运行：python study_longhist.py
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
DB = "BTC-USDT"
ER_CAP, VOL = 0.22, 1.0
INIT = 600.0   # BTC margin60U x lev10


def make_gate(ec, vf):
    def g(w, sig):
        e = efficiency_ratio(w)
        a = atr_volatility(w)
        if e is None or a is None:
            return True
        return not (e < ec and a >= vf)
    return g


def run(tf, gate):
    with open(CFG, encoding="utf-8") as f:
        C = json.load(f)
    sc, pr, er = _extract(C)
    sc["allow_tfs"] = [tf]          # 让目标周期的信号不被 tf 闸门拦掉
    if tf == "1H":
        candles = {"1H": _load_tf(DB, "1H")}
        for t in ["15m", "1h", "4h", "1d"]:
            arr = _load_tf(DB, t)
            if arr:
                candles[t] = arr
    else:
        candles = None               # 4h 让 run_core 自动构造
    return run_core(DB, sc, pr, er, tf=tf, candles=candles, extra_gate=gate)


def summarize(r):
    tr = r["trades"]
    ordered = sorted([t for t in tr if t.get("entry_ts")], key=lambda t: t["entry_ts"])
    k = int(len(ordered) * 0.7)
    full = r["all"]["ret_pct"] if r["all"] else 0.0
    isr = sum(t["pnl"] for t in ordered[:k]) / INIT * 100
    oos = sum(t["pnl"] for t in ordered[k:]) / INIT * 100
    return full, isr, oos, r["max_dd"], len(tr), ordered


for tf, desc in [("1H", "2018-01~2026-09 (8年,76361根)"),
                 ("4h", "2022-09~2026-09 (4年,8905根)")]:
    print(f"\n########## {tf}  {desc}  阈值 ER<{ER_CAP} & atr>={VOL} ##########", flush=True)
    b = run(tf, None)
    g = run(tf, make_gate(ER_CAP, VOL))
    bf, bi, bo, bd, bn, bord = summarize(b)
    gf, gi, go, gd, gn, gord = summarize(g)
    print(f"{'':6s}{'全%':>9s}{'IS%':>9s}{'OOS%':>9s}{'DD%':>8s}{'笔数':>7s}")
    print(f"{'基线':6s}{bf:+9.1f}{bi:+9.1f}{bo:+9.1f}{bd:8.1f}{bn:7d}")
    print(f"{'闸门':6s}{gf:+9.1f}{gi:+9.1f}{go:+9.1f}{gd:8.1f}{gn:7d}")
    print(f"  -> 全{'+' if gf>bf else ''}{gf-bf:.1f}pp  IS{gf and ''}{gi-bi:+.1f}pp  "
          f"OOS{go-bo:+.1f}pp  DD{gd-bd:+.1f}pp")

    if tf == "1H":
        bounds = [(1514764800000, 1577836800000, "2018-19"),
                  (1577836800000, 1640995200000, "2020-21"),
                  (1640995200000, 1704067200000, "2022-23"),
                  (1704067200000, 1790000000000, "2024-26")]
        print(f"  分2年段 (基线% -> 闸门% , 未调参段=前两段):")
        for t0, t1, lab in bounds:
            bp = sum(t["pnl"] for t in bord if t0 <= t["entry_ts"] < t1) / INIT * 100
            gp = sum(t["pnl"] for t in gord if t0 <= t["entry_ts"] < t1) / INIT * 100
            bn_seg = sum(1 for t in bord if t0 <= t["entry_ts"] < t1)
            gn_seg = sum(1 for t in gord if t0 <= t["entry_ts"] < t1)
            flag = "  <-- 未调参" if lab in ("2018-19", "2020-21") else ""
            print(f"    {lab}: {bp:+7.1f}% (n{bn_seg:3d}) -> {gp:+7.1f}% (n{gn_seg:3d})"
                  f"   Δ{gp-bp:+.1f}pp{flag}")

print("\n说明: 阈值 0.22/1.0 是在 1h(2022-2026) 上调的; 1H 的 2018-2021 段完全未参与调参,")
print("      若该段闸门也改善(或至少不伤), 则证明规则不是 2022-2026 的巧合。")
