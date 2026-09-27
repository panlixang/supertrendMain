# -*- coding: utf-8 -*-
"""定位 CL 多单差异根因：CL 各周期(15m/1h/4h/1d) ST(10,3.0) 最近翻转。
数据用 OKX（已实测与 Bitget 一致）。找出哪个周期在 9-27 附近翻 buy，
从而解释"当前系统 9-27 下多单 / Bitget 1h 停在 9-25 空"的分歧。
"""
import sys, time
BASE = r"d:\个人项目代码\supertrendMain\backend"
sys.path.insert(0, BASE)
from indicators import super_trend
from history import fetch_candles

def fmt_ts(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts/1000))

def flips_for(tf, limit):
    cs = fetch_candles(tf, limit, "CL-USDT-SWAP")
    if len(cs) < 50:
        return None, [], 0
    o=[c.o for c in cs]; h=[c.h for c in cs]; l=[c.l for c in cs]; c_=[c.c for c in cs]
    tss=[c.ts for c in cs]
    st = super_trend(o, h, l, c_, periods=10, multiplier=3.0, change_atr=True)
    flips = sorted(st.get("flips") or [], key=lambda f: f["i"])
    out = [(tss[f["i"]], f["type"]) for f in flips]
    return out, cs, len(cs)

LIM = {"15m": 2000, "1h": 800, "4h": 500, "1d": 400}
for tf in ("15m", "1h", "4h", "1d"):
    flips, cs, n = flips_for(tf, LIM[tf])
    print("\n==== CL %s (K线 %d) 最近 8 次翻转 ====" % (tf, n))
    if flips is None:
        print("  数据不足"); continue
    for ts, typ in flips[-8:]:
        mark = ""
        # 标出 9-26 ~ 9-27 区间的 buy
        if typ == "buy" and 1769337600 <= ts/1000 <= 1769510400:  # 9-26~9-28 UTC
            mark = "  <== 9-26~28 BUY"
        print("   %s  %s%s" % (fmt_ts(ts), typ, mark))
