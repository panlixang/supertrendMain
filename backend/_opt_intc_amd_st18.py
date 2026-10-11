# -*- coding: utf-8 -*-
"""测试 INTC/AMD 在【当前页全局 ST 18×3.0】+ 原出场下，直接启用会怎样。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_home_2026 as B

SYMS = {
    "INTC": dict(tag="43", st=dict(periods=18, multiplier=3.0, src="hl2", change_atr=True,
                                   fast_len=20, slow_len=50, ma_type="EMA")),
    "AMD":  dict(tag="47", st=dict(periods=18, multiplier=3.0, src="hl2", change_atr=True,
                                   fast_len=20, slow_len=50, ma_type="EMA")),
}


def main():
    snap = B.load_snapshot()
    tfs = B.TFS_NO15
    for name, cfg in SYMS.items():
        base = snap[cfg["tag"]][name]
        s = dict(base)
        s["params"] = dict(cfg["st"])
        print(f"\n===== {name} [当前页全局ST 18×3.0] + 原出场 =====", flush=True)
        r = B.run_one(name, cfg["tag"] + "*", s, tfs)
        if "err" in r:
            print("  ERR:", r["err"]); continue
        print(f"  笔数={r['n']} 胜率={r['wr']:.1f}% PF={r['pf']:.2f} "
              f"盈亏比={r['payoff']:.2f} Σ收益={r['tot']:.2f}U 回撤={r['mdd_u']:.2f}U "
              f"区间={r['start']}~{r['end']}", flush=True)


if __name__ == "__main__":
    main()
