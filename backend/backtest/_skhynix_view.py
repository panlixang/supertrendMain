"""SKHYNIX 收益曲面 + 候选档位明细（临时分析脚本）。"""
import json
import os

p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_skhynix_tp_opt.json")
d = json.load(open(p, encoding="utf-8"))
rows = d["all"]

print(f"bars={d['bars']} {d['start']}~{d['end']}")
print(f"current: {d['current']['tp']} pnl={d['current']['pnl_u']}U dd={d['current']['max_dd_pct']}% "
      f"wr={d['current']['win_rate']}% PF={d['current']['profit_factor']} trades={d['current']['trades']}")

print("\n=== tp1 固定 1.2，tp2 变化 ===")
for r in rows:
    if r["tp"][0] == 1.2 and r["tp"][2] == 3.5:
        print(f"  {r['tp']}: pnl={r['pnl_u']}U dd={r['max_dd_pct']}% wr={r['win_rate']}% PF={r['profit_factor']}")

print("\n=== tp2 固定 1.5，tp1 变化 ===")
for r in rows:
    if r["tp"][1] == 1.5 and r["tp"][2] == 3.5:
        print(f"  {r['tp']}: pnl={r['pnl_u']}U dd={r['max_dd_pct']}% wr={r['win_rate']}% PF={r['profit_factor']}")

print("\n=== tp1 固定 1.0（当前 tp1），tp2 变化 ===")
for r in rows:
    if r["tp"][0] == 1.0 and r["tp"][2] == 3.5:
        print(f"  {r['tp']}: pnl={r['pnl_u']}U dd={r['max_dd_pct']}% wr={r['win_rate']}% PF={r['profit_factor']}")

print("\n=== 当前 1.0/2.0/3.5 附近 ===")
for r in rows:
    if r["tp"] in ([1.0, 2.0, 3.5], [1.0, 1.5, 3.5], [0.8, 1.5, 3.5], [1.2, 1.5, 3.5], [1.0, 2.5, 3.5]):
        print(f"  {r['tp']}: pnl={r['pnl_u']}U dd={r['max_dd_pct']}% wr={r['win_rate']}% PF={r['profit_factor']}")

print("\n=== 收益集中度（top8 前 4 档）===")
for t in d["top8"][:4]:
    trades = [x for x in []]
print("  （trade_list 未存，见脚本运行输出）")
