# -*- coding: utf-8 -*-
"""BTC 弱档 profile(quick 快出)寻优。

问题：BTC 弱 ER 信号(ER∈[er_weak_min=0.08, er_min=0.12))是否应启用 quick 出场
（tp1 0.8% 一次平光 + 关价格止损）？

方法：用 BTC 线上 V1 配置(full=half=50, alert=55)跑回测，唯一变量是弱 ER 段的
出场规则：
  - 弱档关(OFF)：exit_rules_quick = 标准出场规则 → 弱 ER 信号走标准三级止盈
  - 弱档开(ON) ：exit_rules_quick = quick 规则(tp1 一次性平光) → 弱 ER 走快出
其余(标准 profile 信号、入场闸门)两变体完全一致，因此差异纯来自弱 ER 段出场。

同时对 ON 的 quick tp1_pct 做网格 [0.6,0.8,1.0,1.2,1.5] 寻优。
拆分 profile=="quick" 的子样本，单独看弱 ER 段在 ON/OFF 下的期望(E)。

注：run_backtest 在 live_gate 模式下会按 ER 弱带把信号标为 profile="quick"，
弱档开/关只改变这些信号用的出场规则（rules_by["quick"]）。
"""
from __future__ import annotations
import json
import os
import sys
import time
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest  # noqa: E402
from _live_cfg_backtest import exit_rules, trade_cfg, _get, ts_fmt  # noqa: E402
from position_enhanced import EnhancedExitRules  # noqa: E402

LIVE_URLS = ["http://43.108.10.84:5174/api/trade/symbols",
             "http://47.84.106.154:5174/api/trade/symbols"]
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data")
FILE = "BTC.json"
TF = "1h"
QUICK_TP1 = [0.6, 0.8, 1.0, 1.2, 1.5]


def make_quick(tp1_pct):
    """镜像 state.py 的 exit_rules_quick（V3 全线关闭价格止损，只用信号止损）。"""
    return EnhancedExitRules(
        enabled=False,                 # 关价格止损，只用信号止损
        tp1_pct=tp1_pct, tp1_ratio=100.0,
        tp2_pct=999.0, tp2_ratio=0.0,
        tp3_pct=999.0, tp3_ratio=0.0, tp3_mode="pct",
        move_sl_to_entry=False, trail_with_st=False,
        sl_mode="pct", sl_pct=1.0, sl_buffer_atr=0.3, sl_min_pct=1.0,
        protect_profit_at=999.0, protect_trail_pct=0.0,
        max_loss_enabled=False, max_loss_pct=8.0,
    )


def trades(r):
    return r.get("trades_list") or r.get("trade_list") or []


def metrics(tl):
    if not tl:
        return {"t": 0, "pnl": 0.0, "e": 0.0, "wr": 0.0, "pf": 0.0}
    n = len(tl)
    wins = [t["pnl_pct"] for t in tl if t["pnl_pct"] > 0]
    loss = [t["pnl_pct"] for t in tl if t["pnl_pct"] <= 0]
    wr = len(wins) / n * 100
    aw = sum(wins) / len(wins) if wins else 0.0
    al = sum(loss) / len(loss) if loss else 0.0
    e = wr / 100 * aw - (1 - wr / 100) * abs(al)
    gain = sum(wins)
    loss_abs = abs(sum(loss))
    pf = gain / loss_abs if loss_abs > 0 else (gain if gain else 0.0)
    return {"t": n, "pnl": round(sum(t["pnl_pct"] for t in tl), 2),
            "e": round(e, 3), "wr": round(wr, 1), "pf": round(pf, 2)}


def run_btc(sym, cbtf, cfg, normal_rules, quick_rules):
    gate_tf = sym["allow_tfs"][0]
    candles = cbtf.get(gate_tf)
    common = dict(init_cash=100.0, fee_rate=0.0005, allow_short=True,
                  sizing="fixed", margin_usdt=sym["margin_usdt"],
                  leverage=sym["leverage"], gate_tf=gate_tf,
                  candles_by_tf=cbtf, exit_rules=normal_rules,
                  exit_rules_quick=quick_rules, live_gate=cfg)
    r = run_backtest(candles, sym["params"], **common)
    return r


def main():
    t0 = time.time()
    sym = None
    for url in LIVE_URLS:
        try:
            d = _get(url)
            sym = next((x for x in d["symbols"]
                        if x["symbol"] == "BTC-USDT-SWAP"), None)
            if sym:
                break
        except Exception:
            continue
    if sym is None:
        print("无法获取 BTC 线上配置"); return
    print("BTC 线上: score_engine=%r full/half=%s alert=%s er_weak_min=%s er_min=%s"
          % (sym.get("score_engine"), sym.get("scoring_full_threshold"),
             sym.get("scoring_alert_threshold"), sym.get("er_weak_min"),
             sym.get("er_min")))

    cache = os.path.join(DATA, FILE)
    cbtf = {k: v for k, v in json.load(open(cache, encoding="utf-8")).items()
            if isinstance(v, list) and v and "ts" in (v[0] or {})}
    candles = cbtf.get(TF)
    if not candles or len(candles) < 300:
        print("BTC 数据不足"); return
    print(f"行情 [{ts_fmt(candles[0]['ts'])} ~ {ts_fmt(candles[-1]['ts'])}] "
          f"{len(candles)} 根 {TF}")

    cfg = trade_cfg(sym)            # V1 线上配置
    normal_rules = exit_rules(sym)

    def summarize_run(r, quick_rules):
        if "error" in r:
            return None
        tl = trades(r)
        m = metrics(tl)
        quick = [t for t in tl if t.get("profile") == "quick"]
        mq = metrics(quick)
        return {"overall": m, "quick_seg": mq, "n_quick": len(quick)}

    # 1) 弱档关：弱 ER 走标准出场
    off = summarize_run(run_btc(sym, cbtf, cfg, normal_rules, normal_rules),
                        normal_rules)
    print("\n=== 弱档 关(OFF): 弱ER走标准出场 ===")
    print(f"  整体: T={off['overall']['t']} pnl%={off['overall']['pnl']} "
          f"E%={off['overall']['e']} WR%={off['overall']['wr']} PF={off['overall']['pf']}")
    print(f"  弱ER段(quick): T={off['quick_seg']['t']} pnl%={off['quick_seg']['pnl']} "
          f"E%={off['quick_seg']['e']} WR%={off['quick_seg']['wr']} PF={off['quick_seg']['pf']}")

    # 2) 弱档开：quick tp1 网格
    print("\n=== 弱档 开(ON): 弱ER走 quick 快出, tp1 网格 ===")
    grid = []
    for tp1 in QUICK_TP1:
        qt = make_quick(tp1)
        rr = run_btc(sym, cbtf, cfg, normal_rules, qt)
        s = summarize_run(rr, qt)
        if s is None:
            print(f"  tp1={tp1}: error {rr.get('error')}"); continue
        grid.append({"tp1": tp1, **s})
        print(f"  tp1={tp1:g}: 整体 pnl%={s['overall']['pnl']} E%={s['overall']['e']} "
              f"PF={s['overall']['pf']} n={s['overall']['t']} | "
              f"弱ER段 pnl%={s['quick_seg']['pnl']} E%={s['quick_seg']['e']} "
              f"n={s['quick_seg']['t']}")

    # 3) 结论
    on_best = max(grid, key=lambda g: g["overall"]["pnl"]) if grid else None
    d_overall = (on_best["overall"]["pnl"] - off["overall"]["pnl"]) if on_best else None
    d_quick = (on_best["quick_seg"]["e"] - off["quick_seg"]["e"]) if on_best else None
    print("\n=== 结论 ===")
    print(f"  OFF 整体 pnl%={off['overall']['pnl']}  弱ER段 E%={off['quick_seg']['e']}")
    if on_best:
        print(f"  ON 最优(tp1={on_best['tp1']:g}) 整体 pnl%={on_best['overall']['pnl']} "
              f"Δ={d_overall:+.2f}  弱ER段 E%={on_best['quick_seg']['e']} Δ={d_quick:+.3f}")
        verdict = "建议开弱档" if (on_best["overall"]["pnl"] > off["overall"]["pnl"]
                                   and on_best["quick_seg"]["e"] >= off["quick_seg"]["e"]) \
            else "不建议开弱档"
        print(f"  -> {verdict}")

    out = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "symbol": "BTC", "method": "V1同闸门, 仅切换弱ER段出场规则",
           "off": off, "on_grid": grid,
           "verdict": verdict if on_best else "无可用ON结果"}
    json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
              "_btc_weak_profile.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2, default=str)
    print(f"\n耗时 {time.time()-t0:.0f}s | Wrote _btc_weak_profile.json")


if __name__ == "__main__":
    main()
