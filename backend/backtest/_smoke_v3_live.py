# -*- coding: utf-8 -*-
"""端到端校验实盘路径 signal_v3.features_from_candles + v3_decide。

用本地缓存 K 线（与线上同样的 dict 结构）跑完 808 笔，对比回测脚本产出的 V3_可执行 列，
确认「形态识别页勾选 ⑧ V3过滤」和「build_signal_features_1h.py 复盘」是同一口径。
"""
import sys, os, json, csv, calendar, datetime
BASE = r"d:\个人项目代码\supertrendMain\backend"
sys.path.insert(0, BASE)
import signal_v3

FEAT_CSV = os.path.join(BASE, "backtest", "st_signals_1h_features.csv")
base = json.load(open(os.path.join(BASE, "backtest", "btc_1h_full.json"), encoding="utf-8"))["base"]
c4 = json.load(open(os.path.join(BASE, "backtest", "btc_4h_full.json"), encoding="utf-8"))["h4"]

def msec(s):
    return calendar.timegm(datetime.datetime.strptime(s, "%Y/%m/%d %H:%M").timetuple())
base_sec = {int(b["ts"] / 1000): i for i, b in enumerate(base)}
rows = list(csv.DictReader(open(FEAT_CSV, encoding="utf-8-sig")))

def f(x):
    try: return float(x)
    except: return None

same = diff = 0
diff_rows = []
for r in rows:
    i = base_sec.get(msec(r["时间"]))
    if i is None:
        continue
    side = int(r["信号"])
    feats = signal_v3.features_from_candles(base, i, side, c4)
    if not feats:
        continue
    v = signal_v3.v3_decide(side, feats)
    exp = r["V3_可执行"] == "TRUE"
    if bool(v["execute"]) == exp:
        same += 1
    else:
        diff += 1
        diff_rows.append((r["时间"], side, r["V3_可执行"], v["path"], v["score"], r["盈亏"]))

print(f"与回测列一致: {same} 笔   不一致: {diff} 笔")
if diff_rows:
    kept_pnl = sum(f(d[5]) or 0 for d in diff_rows)
    print(f"不一致笔合计盈亏: {kept_pnl:.2f}%  (前10笔明细: 时间/方向/回测/实盘路径/分数/盈亏)")
    for d in diff_rows[:10]:
        print("   ", d)

# 实盘路径独立跑一遍的绩效
kept = []
for r in rows:
    i = base_sec.get(msec(r["时间"]))
    if i is None: continue
    side = int(r["信号"])
    feats = signal_v3.features_from_candles(base, i, side, c4)
    if not feats: continue
    if signal_v3.v3_decide(side, feats)["execute"]:
        kept.append(r)
pnls = [f(r["盈亏"]) or 0 for r in kept]
gp = sum(p for p in pnls if p > 0); gl = -sum(p for p in pnls if p < 0)
print(f"\n实盘路径: 放行 {len(kept)} 笔  累计={sum(pnls):.2f}%  PF={gp/gl:.2f}  红字={sum(1 for p in pnls if p>2)}")
