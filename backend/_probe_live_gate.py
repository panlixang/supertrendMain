# -*- coding: utf-8 -*-
"""看线上首页真实配置：INTC/AMD 以及同页参考品种的 ST(全局) 与评分闸门。"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_home_2026 as B


def show(tag, name, sym):
    p = sym.get("params") or {}
    print(f"  [{tag}] {name} ({sym.get('symbol')})")
    print(f"    ST={p.get('periods')}×{p.get('multiplier')}/{p.get('src')} "
          f"enabled={sym.get('enabled')}")
    print(f"    闸门: use_scoring={sym.get('use_scoring')} score_engine={sym.get('score_engine')!r} "
          f"full={sym.get('scoring_full_threshold')} half={sym.get('scoring_half_threshold')} "
          f"alert={sym.get('scoring_alert_threshold')} dyn={sym.get('use_dynamic_threshold')}")
    print(f"    filter: range={sym.get('range_filter_enabled')} mtf={sym.get('mtf_filter_enabled')} "
          f"adx={sym.get('adx_filter_enabled')}")
    er = sym.get("exit_rules") or {}
    print(f"    出场: tp1={er.get('tp1_pct')}/{er.get('tp1_ratio')} sl={er.get('sl_pct')}({er.get('sl_mode')}) "
          f"trail={er.get('trail_with_st')}")


def main():
    live = B.load_live()
    print("===== 43 页参考（首页策略真实闸门） =====")
    for n in ("NVDA", "CL", "INTC"):
        if live.get("43", {}).get(n):
            show("43", n, live["43"][n])
    print("\n===== 47 页参考 =====")
    for n in ("SPCX", "AMD"):
        if live.get("47", {}).get(n):
            show("47", n, live["47"][n])


if __name__ == "__main__":
    main()
