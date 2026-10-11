# -*- coding: utf-8 -*-
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_home_2026 as B

live = B.load_live()
for page in ("43", "47"):
    syms = live.get(page, {})
    for name in ("INTC", "AMD"):
        if name not in syms:
            continue
        s = syms[name]
        print("=" * 70)
        print(f"{name}  [{page}页]  symbol={s.get('symbol')}")
        keys = ["score_engine", "use_scoring", "use_dynamic_threshold",
                "scoring_full_threshold", "scoring_half_threshold", "scoring_alert_threshold",
                "er_hide_below", "er_weak_min", "er_min", "er_trend",
                "quick_enabled", "allow_grades", "min_score",
                "atr_filter_enabled", "atr_vol_min",
                "range_filter_enabled", "range_size_max", "range_touches_min",
                "mtf_filter_enabled", "mtf_consistency_min", "mtf_flip_max",
                "adx_filter_enabled", "adx_min", "adx_period",
                "filter_v3", "allow_tfs", "params"]
        for k in keys:
            if k in s:
                v = s[k]
                if isinstance(v, float):
                    v = round(v, 4)
                print(f"  {k:>24} = {v}")
        er = s.get("exit_rules") or {}
        print(f"  {'exit_rules':>24} = tp1={er.get('tp1_pct')}/{er.get('tp1_ratio')} "
              f"sl={er.get('sl_pct')}({er.get('sl_mode')}) trail={er.get('trail_with_st')}")
