# -*- coding: utf-8 -*-
import csv
P = r"d:\个人项目代码\supertrendMain\backend\backtest\st_signals_15m.csv"
rows = list(csv.DictReader(open(P, encoding="utf-8-sig")))
print("字段数:", len(rows[0]))
print("信号条数:", len(rows))
print("列名:", list(rows[0].keys()))
print("\n最早5条:")
for r in rows[:5]:
    print(r["time"], "sig", r["signal"], "close", r["close"], "align", r["align"],
          "htf", r["htf_dir"], "squeeze", r["squeeze"], "rev", r["reverse_signal_price"],
          r["exit_result"], "pnl", r["pnl_pct"])
# 检查 align=-1 (未知) 在前8个月的比例
miss = [r for r in rows if r["align"] == "-1"]
print("\nalign=-1 条数:", len(miss))
if miss:
    print("  最旧一条 align=-1 时间:", min(r["time"] for r in miss))
    print("  最新一条 align=-1 时间:", max(r["time"] for r in miss))
