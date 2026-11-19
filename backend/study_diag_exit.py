# -*- coding: utf-8 -*-
"""诊断：engine 的 tp1_pct / sl_mode 出场参数到底生效没有？

study_pattern_btc1h.py 三组不同出场跑出完全相同的结果，怀疑出场没生效。
本脚本用两个极端值（tp1=0.5% vs tp1=10%）跑同一段数据：
  - 若结果/pnl结构不同 -> 出场生效
  - 若完全相同        -> 出场未生效，前面的结论不成立
并打印 reason 分布，看清交易究竟靠什么平仓。

运行：python study_diag_exit.py
"""
import os, sys, json, copy
from collections import Counter
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(BACKEND, "backtest"))

from engine import run_core, _extract, _load_tf   # noqa: E402

CFG = os.path.join(BACKEND, "backtest", "BTC", "btc_config.json")
DB, TF = "BTC-USDT", "1h"     # 用 1h(41451根) 加速诊断

with open(CFG, encoding="utf-8") as f:
    BASE = json.load(f)
_sc0, _pr0, _er0 = _extract(BASE)
print("[诊断] _er0 原始 exit_rules 字段:", json.dumps(_er0, ensure_ascii=False)[:300])

candles = {"15m": _load_tf(DB, "15m"), "1h": _load_tf(DB, "1h"), "4h": _load_tf(DB, "4h")}


def build(tp1_pct, tp1_ratio, sl_mode, sl_pct):
    sc = copy.deepcopy(_sc0)
    sc["allow_tfs"] = [TF]
    sc["use_scoring"] = False
    sc["min_score"] = 0
    sc["allow_grades"] = ["A", "B", "C"]
    for k in ("er_min", "er_trend", "er_weak_min", "er_hide_below"):
        sc[k] = 0.0
    for k in ("mtf_filter_enabled", "range_filter_enabled",
              "atr_filter_enabled", "adx_filter_enabled", "filter_v3"):
        sc[k] = False
    pr = copy.deepcopy(_pr0)
    pr["periods"] = 10
    pr["multiplier"] = 3.0
    pr["src"] = "hl2"
    pr["change_atr"] = True
    er = copy.deepcopy(_er0)
    er.update({"tp1_pct": tp1_pct, "tp1_ratio": tp1_ratio,
               "sl_mode": sl_mode, "sl_pct": sl_pct,
               "move_sl_to_entry": True, "trail_with_st": True,
               "reverse_close": False, "max_loss_enabled": False})
    return sc, pr, er


for tag, (tp1p, tp1r, slm, slp) in [
    ("A tp1=0.5%/70 sl=pct3", (0.5, 70.0, "pct", 3.0)),
    ("B tp1=10%/70 sl=pct3", (10.0, 70.0, "pct", 3.0)),
    ("C tp1=1.5%/70 sl=ST ", (1.5, 70.0, "st", 2.0)),
]:
    sc, pr, er = build(tp1p, tp1r, slm, slp)
    r = run_core(DB, sc, pr, er, tf=TF, candles=candles, extra_gate=None)
    tr = r["trades"]
    tot = sum(t["pnl"] for t in tr)
    print(f"\n[{tag}]  {len(tr)} 笔  累计 {tot:+.2f}U  全 {r['all']['ret_pct']:+.2f}%  DD {r['max_dd']:.1f}%")
    print("   reason分布:", dict(Counter(t["reason"] for t in tr)))
    tp1_hits = [t for t in tr if t["reason"] == "tp1"]
    if tp1_hits:
        avg = sum(t["pnl"] for t in tp1_hits) / len(tp1_hits)
        print(f"   tp1 均pnl {avg:+.3f}U  (tp1_pct={tp1p}% 名义应≈{tp1p}% 仓位70%的收益)")
