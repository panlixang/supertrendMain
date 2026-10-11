# -*- coding: utf-8 -*-
"""抓取 11 个品种 2026 年回测所需行情（1h + 4h），落本地缓存 _bt2026_data。

1h 从 2025-11-01 起（给 SuperTrend / 4h 形态足够预热），4h 取 2500 根。
已存在缓存则跳过（加 --force 重拉）。
"""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import history

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_bt2026_data")
SYMS = ["NVDA-USDT-SWAP", "CL-USDT-SWAP", "MU-USDT-SWAP", "SNDK-USDT-SWAP",
        "SKHYNIX-USDT-SWAP", "TSLA-USDT-SWAP", "BTC-USDT-SWAP", "ETH-USDT-SWAP",
        "INTC-USDT-SWAP", "AMD-USDT-SWAP", "XAU-USDT-SWAP"]
LIMIT_1H = 8200       # ≈ 341 天
LIMIT_4H = 2500


def f(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def rows(cs):
    return [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol}
            for c in cs]


def main():
    force = "--force" in sys.argv
    os.makedirs(OUT, exist_ok=True)
    for s in SYMS:
        p = os.path.join(OUT, s.replace("-", "_") + ".json")
        if os.path.exists(p) and not force:
            d = json.load(open(p, encoding="utf-8"))
            print("%-22s 缓存命中 1h n=%d %s~%s" % (
                s, len(d["1h"]), f(d["1h"][0]["ts"]), f(d["1h"][-1]["ts"])), flush=True)
            continue
        b = history.fetch_candles("1h", limit=LIMIT_1H, symbol=s)
        if not b:
            print("%-22s 1h 抓取失败" % s, flush=True)
            continue
        h = history.fetch_candles("4h", limit=LIMIT_4H, symbol=s)
        if not h:
            print("%-22s 4h 抓取失败" % s, flush=True)
            continue
        json.dump({"sym": s, "1h": rows(b), "4h": rows(h)}, open(p, "w", encoding="utf-8"))
        print("%-22s 1h n=%d %s~%s | 4h n=%d %s~%s" % (
            s, len(b), f(b[0].ts), f(b[-1].ts),
            len(h), f(h[0].ts), f(h[-1].ts)), flush=True)


if __name__ == "__main__":
    main()
