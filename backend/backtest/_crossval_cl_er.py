# -*- coding: utf-8 -*-
"""CL ER 档位细扫：params {19×2.5, 7×6} × er_min {0.15,0.20,0.25,0.30}
× er_weak {0.10,0.12} × er_trend {0.25,0.35}，前/后段稳健性检验。
tp 固定最优 1.2/3/3.5，sl2/ml2，score 50/50/40 m0。"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest  # noqa: E402
from position_enhanced import EnhancedExitRules  # noqa: E402
from regime import TradeConfig  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "_live_data", "cl_half_cache.json")

ER = dict(
    enabled=True, tp1_pct=1.2, tp1_ratio=30.0, tp2_pct=3.0, tp2_ratio=40.0,
    tp3_pct=3.5, tp3_ratio=100.0, tp3_mode="reverse_signal",
    move_sl_to_entry=True, sl_mode="st", sl_pct=2.0, trail_with_st=True,
    sl_buffer_atr=0.5, sl_min_pct=1.2, protect_profit_at=1.5,
    protect_trail_pct=0.8, max_loss_enabled=True, max_loss_pct=2.0,
)

BASE_CFG = dict(
    enabled=True, leverage=10, amount_usdt=10.0, er_hide_below=0.1,
    er_weak_min=0.12, er_min=0.12, er_trend=0.3, quick_enabled=False,
    allow_grades=["A", "B", "C"], min_score=0, allow_tfs=["1h"],
    cooldown_sec=300, atr_filter_enabled=False, atr_vol_min=0.7,
    range_filter_enabled=True, range_size_max=0.15, range_touches_min=2,
    mtf_filter_enabled=False, mtf_consistency_min=0.6, mtf_flip_max=5,
    adx_filter_enabled=False, adx_min=20.0, adx_period=14,
    use_scoring=True, scoring_full_threshold=50.0,
    scoring_half_threshold=50.0, scoring_alert_threshold=40.0,
    use_dynamic_threshold=True,
)


def ts_fmt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def make_exit(**over) -> EnhancedExitRules:
    return EnhancedExitRules(**{**ER, **over})


def make_cfg(**over) -> TradeConfig:
    c = dict(BASE_CFG)
    c.update(over)
    return TradeConfig(**c)


def run_seg(candles, cbtf, p, cfg, er_over=None) -> dict:
    r = run_backtest(
        candles, p, init_cash=100.0, fee_rate=0.0005, allow_short=True,
        exit_rules=make_exit(**(er_over or {})), sizing="fixed",
        margin_usdt=10.0, leverage=10,
        live_gate=cfg, gate_tf="1h", candles_by_tf=cbtf,
    )
    if "error" in r:
        return {"err": r["error"]}
    return {
        "pnl": round(r["final"] - 100.0, 2),
        "trades": r["trades"], "pf": r["profit_factor"], "dd": r["max_dd_pct"],
    }


def main():
    cache = json.load(open(CACHE, encoding="utf-8"))
    cbtf = {t: cache[t] for t in ("15m", "1h", "4h", "1d") if t in cache}
    candles = cbtf["1h"]
    mid = candles[len(candles) // 2]["ts"]
    seg_a = [c for c in candles if c["ts"] < mid]
    seg_b = [c for c in candles if c["ts"] >= mid]
    cbtf_a = {t: [c for c in v if c["ts"] < mid] for t, v in cbtf.items()}
    cbtf_b = {t: [c for c in v if c["ts"] >= mid] for t, v in cbtf.items()}

    P = {"src": "hl2", "change_atr": True, "fast_len": 20, "slow_len": 50,
         "ma_type": "EMA"}
    rows = []
    for per, mul in ((19, 2.5), (7, 6.0)):
        p = dict(P, periods=per, multiplier=mul)
        for er_min in (0.15, 0.20, 0.25, 0.30):
            for er_weak in (0.10, 0.12):
                if er_weak > er_min:
                    continue
                for er_trend in (0.25, 0.35):
                    cfg = make_cfg(er_min=er_min, er_weak_min=er_weak,
                                   er_trend=er_trend)
                    full = run_seg(candles, cbtf, p, cfg)
                    ra = run_seg(seg_a, cbtf_a, p, cfg)
                    rb = run_seg(seg_b, cbtf_b, p, cfg)
                    rows.append({
                        "tag": f"p={per}×{mul:g} er={er_min:g}/{er_weak:g}/{er_trend:g}",
                        "full": full, "seg_a": ra, "seg_b": rb,
                    })
                    print(f"p={per}×{mul:g} er={er_min:g}/{er_weak:g}/{er_trend:g} "
                          f"| 全 {full.get('pnl','-')}U/{full.get('trades','-')}笔/PF{full.get('pf','-')} "
                          f"| A {ra.get('pnl','-')}U/{ra.get('trades','-')}笔/PF{ra.get('pf','-')} "
                          f"| B {rb.get('pnl','-')}U/{rb.get('trades','-')}笔/PF{rb.get('pf','-')}",
                          flush=True)

    # 稳健排序：A、B 两段都盈利且 PF 都 >= 1.5
    def ok(x):
        return (x["seg_a"].get("pnl", -1) > 0 and x["seg_b"].get("pnl", -1) > 0
                and (x["seg_a"].get("pf") or 0) >= 1.5
                and (x["seg_b"].get("pf") or 0) >= 1.5
                and x["seg_a"].get("trades", 0) >= 6
                and x["seg_b"].get("trades", 0) >= 6)
    good = [r for r in rows if ok(r)]
    good.sort(key=lambda r: min(r["seg_a"]["pnl"], r["seg_b"]["pnl"]), reverse=True)
    print("\n=== 两段均盈利 PF>=1.5 笔数>=6 的稳健配置 TOP ===", flush=True)
    for r in good[:15]:
        print(f"  {r['tag']} | 全 {r['full']['pnl']}U/{r['full']['trades']}笔/PF{r['full']['pf']} "
              f"| A {r['seg_a']['pnl']}U/{r['seg_a']['trades']}笔/PF{r['seg_a']['pf']} "
              f"| B {r['seg_b']['pnl']}U/{r['seg_b']['trades']}笔/PF{r['seg_b']['pf']}", flush=True)

    out = os.path.join(BASE, "_crossval_cl_er.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "robust": good[:15]}, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {out}", flush=True)


if __name__ == "__main__":
    main()
