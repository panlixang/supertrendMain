# -*- coding: utf-8 -*-
"""读取 _v1_v2_trades.json，打印干净的两张表（ASCII 标签，避免编码问题）。"""
import json, os

base = os.path.dirname(os.path.abspath(__file__))
data = json.load(open(os.path.join(base, "_v1_v2_trades.json"), encoding="utf-8"))

for sym in ["MU", "ETH", "SPCX", "SNDK", "BTC"]:
    t1 = data["table1"].get(sym, {})
    t2 = data["table2"].get(sym, [])
    meta = data.get("meta", {}).get(sym, {})
    print(f"\n########## {sym}  (V1_trades={meta.get('v1_trades')} "
          f"V2_trades={meta.get('v2_trades')} "
          f"V2_bypass_no_score={meta.get('v2_bypass_no_score')}) ##########")

    print("  [Table1] V1 vs V2 逐笔分类  (T / PnL_U / E% / PF / WR%)")
    for key, label in [("V1_only", "V1_only(filtered_out_by_V2)"),
                      ("common", "V1_and_V2_common"),
                      ("V2_only", "V2_only(new_added)")]:
        st = t1.get(key, {})
        pf = "inf" if st.get("pf") is None else round(st["pf"], 2)
        print(f"    {label:28s}: T={st.get('trades',0):>3}  "
              f"PnL={st.get('pnl',0):>8.2f}  E={st.get('e',0):>6.3f}  "
              f"PF={pf:>5}  WR={st.get('wr',0):>5.1f}")

    print("  [Table2] V2 Score buckets (V2 traded only)")
    print(f"    {'bin':>7s}: {'T':>3}  {'PnL_U':>8}  {'E%':>7}  {'PF':>5}  {'WR%':>5}")
    for st in t2:
        if st.get("trades", 0) == 0:
            continue
        pf = "inf" if st.get("pf") is None else round(st["pf"], 2)
        print(f"    {st['bin']:>7s}: {st['trades']:>3}  {st['pnl']:>8.2f}  "
              f"{st['e']:>7.3f}  {pf:>5}  {st['wr']:>5.1f}")
