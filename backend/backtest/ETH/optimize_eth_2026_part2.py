# -*- coding: utf-8 -*-
"""ETH 1h线 2026年综合寻优 - 第二部分：精搜和验证"""

# === 坐标下降结果 ===
cd = evaluate(state["pr"], state["sc"], state["er"], CANDLES_2026)
print(f"\n=== 坐标下降拼合结果 ===")
print(f"OOS={cd['oos_ret']:+.2f}% PF_oos={cd['oos_pf']:.2f} IS={cd['is_ret']:+.2f}% "
      f"全={cd['ret']:+.2f}% DD={cd['dd']:.1f}% n={cd['n']} 每笔={cd['ret']/max(cd['n'],1):+.3f}% 达标={pass_filter(cd)}\n")

# === 6) 邻域笛卡尔积精搜 ===
def topk(ranked, k=3):
    return [x[0] for x in ranked[:k]]

st_top = topk(st_rank, 3)
gate_top = topk(gate_rank, 2)
tp_top = topk(tp_rank, 3)
sl_top = topk(sl_rank, 2)
er_top = topk(er_rank, 2)

total_combos = len(st_top)*len(gate_top)*len(tp_top)*len(sl_top)*len(er_top)
print(f"=== 6) 邻域笛卡尔积精搜 {len(st_top)}×{len(gate_top)}×{len(tp_top)}×{len(sl_top)}×{len(er_top)} = {total_combos} 组合 ===")

best = None
best_combo = None
tested = 0
for per, mu, src in st_top:
    for gf, gal in gate_top:
        for t1, t1r, t2, t2r in tp_top:
            for sl, tr in sl_top:
                for ert, erm in er_top:
                    tested += 1
                    pr = {"periods": per, "multiplier": mu, "src": src, "change_atr": True}
                    sc = {"scoring_full_threshold": float(gf), "scoring_half_threshold": float(gf),
                          "scoring_alert_threshold": float(gal),
                          "er_trend": ert, "er_min": erm, "er_weak_min": erm}
                    er = {"tp1_pct": t1, "tp1_ratio": t1r, "tp2_pct": t2, "tp2_ratio": t2r,
                          "sl_pct": sl, "trail_with_st": tr}
                    m = evaluate(pr, sc, er, CANDLES_2026)
                    if not pass_filter(m):
                        continue
                    score = (_eff(m)[0], _eff(m)[1], m["oos_ret"])
                    if best is None or score > best[0]:
                        best = (score, m)
                        best_combo = (pr, sc, er)

print(f"已测试 {tested} 组合")
print("\n邻域精搜最优（2026年达标内每笔收益效率最高）：")
if best_combo is None:
    print(f"  ⚠ 无满足硬约束组合（OOS笔数>={OOS_N} & PF_oos>1 & DD<=15%），回退坐标下降。")
    best_combo = (state["pr"], state["sc"], state["er"])
    m_best = cd
else:
    m_best = evaluate(*best_combo, CANDLES_2026)
    print(f"  ST {best_combo[0]['periods']}×{best_combo[0]['multiplier']} {best_combo[0]['src']} | "
          f"gate {best_combo[1]['scoring_full_threshold']}/{best_combo[1]['scoring_alert_threshold']} | "
          f"tp1 {best_combo[2]['tp1_pct']}%/{best_combo[2]['tp1_ratio']}% "
          f"tp2 {best_combo[2]['tp2_pct']}%/{best_combo[2]['tp2_ratio']}% | "
          f"sl {best_combo[2]['sl_pct']}% trail={best_combo[2]['trail_with_st']} | "
          f"ER trend={best_combo[1]['er_trend']} min={best_combo[1]['er_min']}")
    print(f"  2026 OOS={m_best['oos_ret']:+.2f}% PF_oos={m_best['oos_pf']:.2f} IS={m_best['is_ret']:+.2f}% "
          f"全={m_best['ret']:+.2f}% DD={m_best['dd']:.1f}% n={m_best['n']} 胜率={m_best['wr']:.1f}% 每笔={m_best['ret']/m_best['n']:+.3f}%")
