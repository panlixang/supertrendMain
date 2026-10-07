# -*- coding: utf-8 -*-
"""下载 OKX INTC-USDT-SWAP 永续的历史 K 线进本地 candle_data.db。

标的：OKX 股票永续 INTC-USDT-SWAP，本地按 BTC 命名习惯存成 INTC-USDT（不带 SWAP）。
周期：只下 15m（重采样出 1h/30m）和 4h（V3 的 4h 上下文）。
OKX 与 bitget 的 candles 返回格式一致：[ts_ms, o, h, l, c, vol, ...]，倒序。
翻页用 after（毫秒），limit 最大 100。
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import time
import urllib.request

INST = "INTC-USDT-SWAP"
DB_SYM = "INTC-USDT"
DB = "candle_data.db"


def fetch(gran: str, after):
    url = (f"https://www.okx.com/api/v5/market/history-candles?instId={INST}"
           f"&bar={gran}&limit=100")
    if after is not None:
        url += f"&after={after}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for _ in range(4):
        try:
            d = json.load(urllib.request.urlopen(req, timeout=25))
            if d.get("code") == "0":
                return d.get("data", [])
            print("  API err", d.get("code"), d.get("msg"))
            return []
        except Exception as e:
            print("  retry", e)
            time.sleep(1.5)
    return []


def download(gran: str, tf_db: str, target_start: int):
    con = sqlite3.connect(DB)
    rows = []
    after = None
    pages = 0
    while True:
        data = fetch(gran, after)
        if not data:
            break
        for r in data:
            ts = int(r[0])
            rows.append((DB_SYM, tf_db, ts,
                         float(r[1]), float(r[2]), float(r[3]),
                         float(r[4]), float(r[5])))
        oldest = int(data[-1][0])
        if oldest <= target_start:
            break
        after = oldest - 1
        pages += 1
        if pages % 50 == 0:
            print(f"  {tf_db} 已拉 {len(rows)} 根，到 "
                  f"{dt.datetime.utcfromtimestamp(oldest / 1000):%Y-%m-%d %H:%M} UTC")
        time.sleep(0.05)
    if rows:
        con.executemany(
            "INSERT INTO candles(symbol,tf,ts,o,h,l,c,vol) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(symbol,tf,ts) DO UPDATE SET "
            "o=excluded.o,h=excluded.h,l=excluded.l,c=excluded.c,vol=excluded.vol",
            rows)
        con.commit()
    oldest_ts = min(r[2] for r in rows)
    newest_ts = max(r[2] for r in rows)
    print(f"[done] {tf_db}: {len(rows)} 根 | "
          f"{dt.datetime.utcfromtimestamp(oldest_ts/1000):%Y-%m-%d %H:%M} ~ "
          f"{dt.datetime.utcfromtimestamp(newest_ts/1000):%Y-%m-%d %H:%M} UTC")
    con.close()


if __name__ == "__main__":
    target = int(dt.datetime(2026, 2, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    print(f"下载 OKX {INST} -> {DB_SYM} ...")
    download("1H", "1h", target)
    download("4H", "4h", target)
