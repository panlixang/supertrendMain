# -*- coding: utf-8 -*-
"""按真实首页策略重算 INTC/AMD（两者都在 43 页 /api/trade/symbols 模块）。
每个品种用自己线上的 ST + 评分闸门；不钉 ST、不关闸门。
(A) 当前线上出场 0.8/30 st-trail  → 当前真实状态
(B) 同 ST + 同闸门 + 还原原出场   → 修复出场后的首页策略表现
"""
import sys, os, copy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_home_2026 as B

ORIG_EXIT = {
    "INTC": dict(tp1_pct=2.0, tp1_ratio=50.0, sl_pct=3.0, sl_mode="pct",
                 trail_with_st=False, move_sl_to_entry=True, exit_mode="single",
                 tp2_pct=999.0, tp2_ratio=0.0, tp3_pct=999.0, tp3_ratio=0.0, tp3_mode="pct"),
    "AMD":  dict(tp1_pct=4.0, tp1_ratio=50.0, sl_pct=3.0, sl_mode="pct",
                 trail_with_st=True, move_sl_to_entry=True, exit_mode="single",
                 tp2_pct=999.0, tp2_ratio=0.0, tp3_pct=999.0, tp3_ratio=0.0, tp3_mode="pct"),
}


def run(label, sym):
    r = B.run_one(sym["symbol"].split("-")[0], "43", sym, B.TFS_NO15)
    if "err" in r:
        print(f"    [{label}] ERR: {r['err']}")
        return
    print(f"    [{label}] 笔数={r['n']} 胜率={r['wr']:.1f}% PF={r['pf']:.2f} "
          f"盈亏比={r['payoff']:.2f} Σ收益={r['tot']:.2f}U 回撤={r['mdd_u']:.2f}U "
          f"闸门拦={r.get('blocked')} 区间={r['start']}~{r['end']}", flush=True)


def main():
    live = B.load_live()
    for name in ("INTC", "AMD"):
        sym = live["43"][name]
        p = sym["params"]
        print(f"\n===== {name} [43] 真实首页配置 ST={p['periods']}×{p['multiplier']}/{p['src']} "
              f"gate={sym.get('score_engine')!r} f={sym.get('scoring_full_threshold')} "
              f"range={sym.get('range_filter_enabled')} =====", flush=True)
        print("  (A) 当前线上出场 0.8/30 st-trail：", flush=True)
        run("A当前", sym)
        s2 = copy.deepcopy(sym)
        er = dict(sym.get("exit_rules") or {})
        er.update(ORIG_EXIT[name])
        s2["exit_rules"] = er
        print(f"  (B) 同ST+同闸门 + 还原原出场 tp1={ORIG_EXIT[name]['tp1_pct']}"
              f"/{ORIG_EXIT[name]['tp1_ratio']} sl={ORIG_EXIT[name]['sl_mode']}：", flush=True)
        run("B还原", s2)


if __name__ == "__main__":
    main()
