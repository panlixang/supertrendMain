# -*- coding: utf-8 -*-
"""闸门固定(ER<0.22 & atr>=1.0)后，扫 BTC 的 tp1 止盈档位

背景：闸门后利润主力是「让大趋势单跑」——反向信号平仓 7 笔均 +147U。
而线上 tp1 只有 0.8%/30%（小赚就跑，正是当初 +1.44U tp1 磨损的来源）。
放宽 tp1 理论上能让利润奔跑。本脚本验证到底是变好还是变坏。

注意层级：线上 tp1 0.8 < tp2 1.5 < tp3(reverse_signal)。
tp1 放宽后必须同步抬高 tp2（本脚本 tp2 = tp1 + 0.7，模仿线上 0.8→1.5 的间隔），
否则 tp2(1.5%) 会抢在 tp1(3%) 前面触发，层级错乱。

数据：BTC 1H(2018起 76361根) 8年，与 8年验证同口径。
运行：python study_tp1.py
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

from engine import run_core, _extract, _load_tf   # noqa: E402
from regime import efficiency_ratio, atr_volatility  # noqa: E402

CFG = os.path.join(BACKEND, "backtest", "BTC", "btc_config.json")
DB, TF = "BTC-USDT", "1H"
ER_CAP, VOL_FLOOR = 0.22, 1.0

with open(CFG, encoding="utf-8") as f:
    C = json.load(f)
sc0, pr, er0 = _extract(C)
sc0["allow_tfs"] = [TF]      # 关键：否则周期闸门会把 1H 信号全拦掉(配置里是小写1h)
INIT = float(sc0["margin_usdt"]) * int(sc0["leverage"])
candles = {t: _load_tf(DB, t) for t in ("15m", "1h", "4h")}
candles[TF] = _load_tf(DB, TF)


def make_gate(ec, vf):
    def g(window, sig):
        e = efficiency_ratio(window)
        a = atr_volatility(window)
        if e is None or a is None:
            return True
        return not (e < ec and a >= vf)
    return g


GATE = make_gate(ER_CAP, VOL_FLOOR)

# (tp1_pct, tp1_ratio) 网格；tp2 自动 = tp1+0.7 保持层级
GRID = [
    (0.8, 30.0),   # 线上现档（已知 +157.0%）
    (0.8, 50.0),
    (1.2, 30.0),
    (1.2, 50.0),
    (1.5, 30.0),
    (1.5, 50.0),
    (2.0, 30.0),
    (2.0, 50.0),   # 用户问的 tp1 2%/50%
    (3.0, 30.0),
    (3.0, 50.0),   # 用户问的 tp1 3%/50%
]


def year_of(ts):
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).year


def seg_pct(trades, lo, hi):
    rows = [t for t in trades if lo <= year_of(t["entry_ts"]) <= hi]
    return sum(t["pnl"] for t in rows) / INIT * 100, len(rows)


print(f"BTC {TF} {len(candles[TF])}根  闸门 ER<{ER_CAP} & atr>={VOL_FLOOR}  INIT={INIT:.0f}U")
print(f"分段=按开仓年份  (tp2 自动=tp1+0.7 保持层级, tp3=reverse_signal)")
print(f"{'tp1档':>14s}{'全%':>9s}{'18-19':>8s}{'20-21':>8s}{'22-23':>8s}{'24-26':>8s}"
      f"{'DD%':>7s}{'笔数':>6s}{'极端':>5s}{'极端U':>9s}{'胜率%':>6s}{'vs线上':>8s}")
print("-" * 100)

base_full = None
rows = []
for tp1p, tp1r in GRID:
    er = copy.deepcopy(er0)
    er["tp1_pct"] = tp1p
    er["tp1_ratio"] = tp1r
    er["tp2_pct"] = round(tp1p + 0.7, 2)     # 保持 tp1 < tp2 层级
    er["tp2_ratio"] = 40.0
    r = run_core(DB, sc0, pr, er, tf=TF, candles=candles, extra_gate=GATE)
    tr = [t for t in r["trades"] if t.get("entry_ts")]
    ext = [t for t in tr if t["reason"] == "极端止损"]
    full = r["all"]["ret_pct"]
    if base_full is None:
        base_full = full
    s1, _ = seg_pct(tr, 2018, 2019)
    s2, _ = seg_pct(tr, 2020, 2021)
    s3, _ = seg_pct(tr, 2022, 2023)
    s4, _ = seg_pct(tr, 2024, 2026)
    rows.append((tp1p, tp1r, full, s1, s2, s3, s4, r["max_dd"], len(tr),
                 len(ext), sum(t["pnl"] for t in ext), r["all"]["winrate"]))
    print(f"{tp1p:>6.1f}%/{tp1r:>4.0f}%{full:+9.1f}{s1:+8.1f}{s2:+8.1f}{s3:+8.1f}{s4:+8.1f}"
          f"{r['max_dd']:7.1f}{len(tr):6d}{len(ext):5d}"
          f"{sum(t['pnl'] for t in ext):+9.0f}{r['all']['winrate']:6.1f}"
          f"{full - base_full:+8.1f}", flush=True)

print("\n===== 结论 =====")
best = max(rows, key=lambda x: x[2])
print(f"最优 tp1 档 = {best[0]}%/{best[1]:.0f}%  全样本 {best[2]:+.1f}%  "
      f"DD {best[7]:.1f}%  (线上现档 {base_full:+.1f}%, 差 {best[2] - base_full:+.1f}pp)")
print(f"回撤最优 = ", min(rows, key=lambda x: x[7])[0], "%/",
      f"{min(rows, key=lambda x: x[7])[1]:.0f}%", f"DD {min(rows, key=lambda x: x[7])[7]:.1f}%")
print("\n读法: 若 tp1 放宽后全样本和 22-23 段同时变好 -> 该放宽;")
print("      若全样本略升但 22-23 段更深/DD 更大 -> 只是牛市段放大, 不是真改善。")
