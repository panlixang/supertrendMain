# -*- coding: utf-8 -*-
"""INTC/AMD：原出场 × (基线ST vs 优化ST) 的 2026 回测对比。

复用 bt_home_2026 的 load_snapshot() 拿到 INTC/AMD 各自的「原配」出场
（INTC tp1=2.0/50 + sl=3.0%pct + 不跟踪；AMD tp1=4.0/50 + sl=3.0%pct + 跟踪），
闸门全关、ST 基线 10×3.0/hl2 —— 这正是当初跑出 +74.71 / +38.58 的配置。
本脚本只切换 ST：基线 10×3.0  vs  优化(INTC 7×3.0 / AMD 5×2.0)，共 4 组。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_home_2026 as B

SYMS = {
    "INTC": dict(
        tag="43",
        base_st=dict(periods=10, multiplier=3.0, src="hl2", change_atr=True,
                     fast_len=20, slow_len=50, ma_type="EMA"),
        opt_st=dict(periods=7, multiplier=3.0, src="hl2", change_atr=True,
                    fast_len=20, slow_len=50, ma_type="EMA")),
    "AMD": dict(
        tag="47",
        base_st=dict(periods=10, multiplier=3.0, src="hl2", change_atr=True,
                     fast_len=20, slow_len=50, ma_type="EMA"),
        opt_st=dict(periods=5, multiplier=2.0, src="hl2", change_atr=True,
                    fast_len=20, slow_len=50, ma_type="EMA")),
}


def clone_with_st(sym, st):
    s = dict(sym)
    s["params"] = dict(st)
    return s


def main():
    snap = B.load_snapshot()
    tfs = B.TFS_NO15  # ["1h","4h","1d"]，跳过慢的 15m；闸门关，仅 1h 参与交易
    rows = []
    for name, cfg in SYMS.items():
        base = snap[cfg["tag"]][name]
        print(f"\n##### {name} 原出场: tp1={base['exit_rules']['tp1_pct']}"
              f"/{base['exit_rules']['tp1_ratio']} sl={base['exit_rules']['sl_pct']}"
              f"%({base['exit_rules']['sl_mode']}) trail={base['exit_rules']['trail_with_st']} #####",
              flush=True)
        for label, st in (("基线ST", cfg["base_st"]), ("优化ST", cfg["opt_st"])):
            sym = clone_with_st(base, st)
            print(f"\n===== {name} [{label}] ST={st['periods']}×{st['multiplier']}/{st['src']} =====",
                  flush=True)
            s = B.run_one(name, cfg["tag"] + "*", sym, tfs)
            if "err" in s:
                print("  ERR:", s["err"], flush=True)
                continue
            print(f"  笔数={s['n']} 胜率={s['wr']:.1f}% PF={s['pf']:.2f} "
                  f"盈亏比={s['payoff']:.2f} Σ收益={s['tot']:.2f}U 回撤={s['mdd_u']:.2f}U "
                  f"区间={s['start']}~{s['end']}", flush=True)
            rows.append((name, label, s))

    print("\n" + "=" * 72)
    print("【汇总】原出场下 基线ST vs 优化ST（2026 YTD, 固定100U×1x, 闸门关）")
    print("=" * 72)
    print(f"{'品种':<6}{'ST方案':<14}{'笔数':>6}{'胜率%':>8}{'PF':>7}{'盈亏比':>8}{'Σ收益U':>10}{'回撤U':>9}")
    for name, label, s in rows:
        st = s["params"]
        sts = f"{label}{st['periods']}×{st['multiplier']}"
        print(f"{name:<6}{sts:<16}{s['n']:>6}{s['wr']:>8.1f}{s['pf']:>7.2f}"
              f"{s['payoff']:>8.2f}{s['tot']:>10.2f}{s['mdd_u']:>9.2f}")


if __name__ == "__main__":
    main()
