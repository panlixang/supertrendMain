# -*- coding: utf-8 -*-
"""拉取 BTC-USDT 15m 全量历史并缓存（供 V3 + 15m 确认 A/B 分析用）。"""
import sys, os, json, time, datetime
BASE = r"d:\个人项目代码\supertrendMain\backend"
sys.path.insert(0, BASE)
import history

OUT = os.path.join(BASE, "backtest", "btc_15m_full.json")
if os.path.exists(OUT):
    print("已存在缓存:", OUT)
else:
    t0 = time.time()
    cs = history.fetch_candles("15m", limit=145000, symbol="BTC-USDT")
    base = [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol} for c in cs]
    json.dump({"base": base}, open(OUT, "w", encoding="utf-8"))
    print(f"拉取 {len(base)} 根，用时 {time.time()-t0:.0f}s")
    if base:
        fmt = lambda ms: datetime.datetime.utcfromtimestamp(ms / 1000).strftime("%Y/%m/%d %H:%M")
        print("区间:", fmt(base[0]["ts"]), "→", fmt(base[-1]["ts"]))
