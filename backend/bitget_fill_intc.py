# -*- coding: utf-8 -*-
"""用 bitget 公共行情补全本地 INTC 历史 K 线（INTCUSDT -> INTC-USDT）。
翻页拉全量写 db，覆盖到 2026-02 起。"""
import os, sys, json, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from state import Candle
from candle_store import save_candles

SYM_BITGET = "INTCUSDT"
SYM_DB = "INTC-USDT"
START_TARGET = 1738368000000  # 2026-02-01 毫秒


def fetch_page(tf, gran, end):
    url = (f"https://api.bitget.com/api/v2/mix/market/candles"
           f"?symbol={SYM_BITGET}&productType=usdt-futures&granularity={gran}"
           f"&limit=1000&endTime={end}")
    with urllib.request.urlopen(url, timeout=20) as r:
        d = json.loads(r.read())
    return d.get("data", [])


def fetch_all(tf, gran):
    out = []
    end = int(time.time() * 1000)
    while True:
        rows = fetch_page(tf, gran, end)
        if not rows:
            break
        rows = sorted(rows, key=lambda x: int(x[0]))  # 升序
        out.extend(rows)
        earliest = int(rows[0][0])
        if earliest <= START_TARGET:
            break
        end = earliest - 1
        if len(rows) < 1000:
            break
    return out


def to_candle(row):
    return Candle(ts=int(row[0]), o=float(row[1]), h=float(row[2]),
                 l=float(row[3]), c=float(row[4]), vol=float(row[5]), confirm=True)


for tf, gran in [("1h", 3600), ("15m", 900), ("4h", 14400)]:
    rows = fetch_all(tf, gran)
    if not rows:
        print(f"{tf}: 拉取为空，跳过")
        continue
    candles = [to_candle(r) for r in rows]
    save_candles(SYM_DB, tf, candles)
    t0 = time.strftime("%Y-%m-%d", time.gmtime(candles[0].ts / 1000))
    t1 = time.strftime("%Y-%m-%d", time.gmtime(candles[-1].ts / 1000))
    print(f"{tf}: 写入 {len(candles)} 根  范围 {t0} ~ {t1}")
