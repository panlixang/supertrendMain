# -*- coding: utf-8 -*-
"""按【真实首页策略】重算 INTC/AMD：用 load_live() 拉线上真实配置
（每品种自己的 ST + 评分闸门 v2/40 + range 过滤 + 出场状态机），不钉 ST、不关闸门。
这才是 bt_home_2026 的规范口径。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_home_2026 as B

TAGS = {"INTC": "43", "AMD": "47"}


def main():
    live = B.load_live()
    tfs = B.TFS_NO15
    for name, tag in TAGS.items():
        sym = live.get(tag, {}).get(name)
        if not sym:
            print(f"\n##### {name} [{tag}] 不在 /api/trade/symbols 首页配置中（仅存在于 pattern 模块？）")
            continue
        p = sym.get("params") or {}
        print(f"\n===== {name} [{tag}] 首页真实配置（load_live） =====")
        print(f"  ST={p.get('periods')}×{p.get('multiplier')}/{p.get('src')} enabled={sym.get('enabled')}")
        print(f"  闸门: se={sym.get('score_engine')!r} full={sym.get('scoring_full_threshold')} "
              f"half={sym.get('scoring_half_threshold')} alert={sym.get('scoring_alert_threshold')} "
              f"dyn={sym.get('use_dynamic_threshold')}")
        print(f"  filter: range={sym.get('range_filter_enabled')} mtf={sym.get('mtf_filter_enabled')} "
              f"adx={sym.get('adx_filter_enabled')}")
        er = sym.get("exit_rules") or {}
        print(f"  出场: tp1={er.get('tp1_pct')}/{er.get('tp1_ratio')} sl={er.get('sl_pct')}({er.get('sl_mode')}) "
              f"trail={er.get('trail_with_st')}")
        r = B.run_one(name, tag, sym, tfs)
        if "err" in r:
            print("  ERR:", r["err"]); continue
        print(f"  => 笔数={r['n']} 胜率={r['wr']:.1f}% PF={r['pf']:.2f} 盈亏比={r['payoff']:.2f} "
              f"Σ收益={r['tot']:.2f}U 回撤={r['mdd_u']:.2f}U 闸门拦={r.get('blocked')} "
              f"区间={r['start']}~{r['end']}", flush=True)


if __name__ == "__main__":
    main()
