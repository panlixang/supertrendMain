# -*- coding: utf-8 -*-
"""真实首页策略重算（用本地缓存 K 线，绕过 OKX 限流）。
INTC/AMD 都在 43 页 /api/trade/symbols：每品种自己的 ST + 评分闸门，不钉 ST、不关闸门。
(A) 当前线上出场 0.8/30 st-trail  → 当前真实状态
(B) 同 ST + 同闸门 + 还原原出场   → 修复出场后表现
"""
import sys, os, json, copy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_home_2026 as B

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "_bt2026_data")
ORIG_EXIT = {
    "INTC": dict(tp1_pct=2.0, tp1_ratio=50.0, sl_pct=3.0, sl_mode="pct",
                 trail_with_st=False, move_sl_to_entry=True, exit_mode="single",
                 tp2_pct=999.0, tp2_ratio=0.0, tp3_pct=999.0, tp3_ratio=0.0, tp3_mode="pct"),
    "AMD":  dict(tp1_pct=4.0, tp1_ratio=50.0, sl_pct=3.0, sl_mode="pct",
                 trail_with_st=True, move_sl_to_entry=True, exit_mode="single",
                 tp2_pct=999.0, tp2_ratio=0.0, tp3_pct=999.0, tp3_ratio=0.0, tp3_mode="pct"),
}
TFS = ["1h", "4h"]


def load_candles(base):
    d = json.load(open(os.path.join(DATA, f"{base}_USDT_SWAP.json")))
    lo = B.y26_lo()
    return {tf: [c for c in (d.get(tf) or []) if c["ts"] >= lo] for tf in TFS}


def run_cached(label, sym, cbtf):
    gate_tf = sym["allow_tfs"][0]
    seg = cbtf.get(gate_tf) or []
    if len(seg) < 100:
        print(f"    [{label}] 数据不足 {len(seg)}"); return
    hi = seg[-1]["ts"]
    cbtf_seg = {tf: [c for c in arr if B.y26_lo() <= c["ts"] <= hi]
                for tf, arr in cbtf.items() if arr}
    ex = B.M.exit_rules(sym)
    cfg = B.M.trade_cfg(sym)
    r = B.run_backtest(seg, sym["params"], init_cash=B.INIT, fee_rate=0.0005,
                       allow_short=True, exit_rules=ex, sizing="fixed",
                       margin_usdt=B.MARGIN, leverage=B.LEV,
                       live_gate=cfg, gate_tf=gate_tf, candles_by_tf=cbtf_seg)
    if "error" in r:
        print(f"    [{label}] ERR {r['error']}"); return
    s = B.stats(r)
    print(f"    [{label}] 笔数={s['n']} 胜率={s['wr']:.1f}% PF={s['pf']:.2f} "
          f"盈亏比={s['payoff']:.2f} Σ收益={s['tot']:.2f}U 回撤={s['mdd_u']:.2f}U "
          f"闸门拦={r.get('er_blocked', 0)} 区间={B.M.ts_fmt(seg[0]['ts'])}~{B.M.ts_fmt(hi)}", flush=True)


def main():
    live = B.load_live()
    for name in ("INTC", "AMD"):
        sym = live["43"][name]
        p = sym["params"]
        print(f"\n===== {name} [43] 真实首页配置 ST={p['periods']}×{p['multiplier']}/{p['src']} "
              f"gate={sym.get('score_engine')!r} f={sym.get('scoring_full_threshold')} "
              f"range={sym.get('range_filter_enabled')} =====", flush=True)
        cbtf = load_candles(name)
        print(f"  缓存: 1h={len(cbtf['1h'])} 4h={len(cbtf['4h'])}", flush=True)
        print("  (A) 当前线上出场 0.8/30 st-trail：", flush=True)
        run_cached("A当前", sym, cbtf)
        s2 = copy.deepcopy(sym)
        er = dict(sym.get("exit_rules") or {})
        er.update(ORIG_EXIT[name])
        s2["exit_rules"] = er
        print(f"  (B) 同ST+同闸门 + 还原原出场 tp1={ORIG_EXIT[name]['tp1_pct']}"
              f"/{ORIG_EXIT[name]['tp1_ratio']} sl={ORIG_EXIT[name]['sl_mode']}：", flush=True)
        run_cached("B还原", s2, cbtf)


if __name__ == "__main__":
    main()
