# -*- coding: utf-8 -*-
"""ETH 过滤层分解（修正版）：不用 V3，只用 v2 评分；扫描阈值 40/60/80。"""
from __future__ import annotations
import datetime as dt, json
from collections import defaultdict
from pathlib import Path
import numpy as np

from backtest_engine import run_backtest
from regime import TradeConfig
import sl2_eth_online as M

def build():
    cfg = json.loads(Path("_live43_eth.json").read_text(encoding="utf-8"))
    sym = "ETH-USDT-SWAP"
    tf = (cfg.get("allow_tfs") or ["1h"])[0]
    cs = M.load_tf(sym, tf)
    p = {"periods": int(cfg["params"]["periods"]),
         "multiplier": float(cfg["params"]["multiplier"]),
         "src": cfg["params"].get("src", "hl2"),
         "change_atr": bool(cfg["params"].get("change_atr", True))}
    from position_enhanced import EnhancedExitRules
    er = cfg["exit_rules"]
    enh = EnhancedExitRules(**{k: er[k] for k in (
        "enabled","tp1_pct","tp1_ratio","tp2_pct","tp2_ratio","tp3_pct",
        "tp3_ratio","tp3_mode","sl_mode","sl_pct","sl_buffer_atr","sl_min_pct",
        "move_sl_to_entry","trail_with_st","reverse_close","protect_profit_at",
        "protect_trail_pct","max_loss_enabled","max_loss_pct") if k in er})
    gate = TradeConfig(
        er_hide_below=cfg.get("er_hide_below",0.10), er_weak_min=cfg.get("er_weak_min",0.12),
        er_min=cfg.get("er_min",0.15), er_trend=cfg.get("er_trend",0.30),
        allow_grades=list(cfg.get("allow_grades",["A","B"])), min_score=int(cfg.get("min_score",2)),
        cooldown_sec=int(cfg.get("cooldown_sec",300)), use_scoring=bool(cfg.get("use_scoring",True)),
        scoring_full_threshold=float(cfg.get("scoring_full_threshold",80.0)),
        scoring_half_threshold=float(cfg.get("scoring_half_threshold",60.0)),
        scoring_alert_threshold=float(cfg.get("scoring_alert_threshold",40.0)),
        use_dynamic_threshold=bool(cfg.get("use_dynamic_threshold",True)),
        quick_enabled=bool(cfg.get("quick_enabled",False)),
        score_engine="v2",
    )
    cs4h = M.resample(cs, 4)
    margin = float(cfg.get("margin_usdt") or 10.0); lev = int(cfg.get("leverage") or 3)
    return cs, p, enh, gate, {"1h": cs, "4h": cs4h}, margin, lev

def yr(trades):
    g = defaultdict(list)
    for t in trades:
        g[dt.datetime.fromtimestamp(t["entry_ts"]/1000, dt.UTC).strftime("%Y")].append(t)
    return g

def run(label, v3, gate, score_only, min_sc):
    cs, p, enh, g, cbt, margin, lev = build()
    r = run_backtest(cs, p, init_cash=10000.0, fee_rate=0.0005,
                     exit_rules=enh, exit_rules_quick=None, full_trades=True,
                     allow_short=True, enhanced_stop=True,
                     v3_filter=v3, live_gate=gate, gate_tf="1h", candles_by_tf=cbt,
                     sizing="fixed", margin_usdt=margin, leverage=lev,
                     score_only_gate=score_only, min_total_score=min_sc)
    if r.get("error"):
        print(f"  [{label}] ERR {r['error']}"); return
    g2 = yr(r["trades_list"])
    n25 = len(g2.get("2025",[])); n26 = len(g2.get("2026",[]))
    u25 = sum(t["pnl"] for t in g2.get("2025",[])); u26 = sum(t["pnl"] for t in g2.get("2026",[]))
    print(f"  [{label:18}] 2025 笔={n25:4d} 净U={u25:9.2f} | 2026 笔={n26:4d} 净U={u26:9.2f} | 总笔={r['trades']:4d} 总收益={r['return_pct']:+.2f}% 胜率={r['win_rate']:.1f}%")

if __name__ == "__main__":
    cs, p, enh, gate, cbt, margin, lev = build()
    print("ETH-USDT-SWAP 1h · ST(15,2.0) · Enhanced 三档 · 【无 V3】· 仅 v2 评分；扫描阈值")
    print("对照目标(用户早跑): 2025=108笔/+28.84%  2026=41笔/+28.43%")
    print("-"*96)
    run("raw(无过滤)", False, None, False, 60)
    run("score 阈值40", False, gate, True, 40.0)
    run("score 阈值60", False, gate, True, 60.0)
    run("score 阈值80", False, gate, True, 80.0)
