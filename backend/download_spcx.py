# -*- coding: utf-8 -*-
"""下载 bitget SPCXUSDT 永续的历史 K 线进本地 candle_data.db。

标的：bitget 合约 SPCXUSDT（productType=usdt-futures），本地按 BTC 的命名习惯存成
      SPCX-USDT（带横线、无 SWAP 后缀），与回测脚本 load_db("SPCX-USDT", ...) 对齐。
周期：只下 15m（重采样出 1h/30m）和 4h（V3 的 4h 上下文），库里已有 UNIQUE(symbol,tf,ts) 可 upsert。
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import ssl
import time
import urllib.request

BIT = "SPCXUSDT"
DB_SYM = "SPCX-USDT"
PRODUCT = "usdt-futures"
DB = "candle_data.db"

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def fetch(gran: str, end: int):
    url = (f"https://api.bitget.com/api/v2/mix/market/candles?symbol={BIT}"
           f"&productType={PRODUCT}&granularity={gran}"
           f"&endTime={end}&limit=200")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for _ in range(4):
        try:
            d = json.load(urllib.request.urlopen(req, timeout=25, context=CTX))
            if d.get("code") == "00000":
                return d.get("data", [])
            print("  API err", d.get("code"), d.get("msg"))
            return []
        except Exception as e:
            print("  retry", e)
            time.sleep(1.5)
    return []


EARLIEST = int(dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)


def download(gran: str, tf_db: str):
    con = sqlite3.connect(DB)
    rows = []
    end = int(time.time() * 1000)
    pages = 0
    while True:
        data = fetch(gran, end)
        if not data:
            break
        for r in data:
            ts = int(r[0])
            rows.append((DB_SYM, tf_db, ts,
                         float(r[1]), float(r[2]), float(r[3]),
                         float(r[4]), float(r[5])))
        oldest = int(data[-1][0])
        if oldest < EARLIEST:
            break
        end = oldest - 1
        pages += 1
        if pages % 50 == 0:
            print(f"  {tf_db} 已拉 {len(rows)} 根，到 "
                  f"{dt.datetime.utcfromtimestamp(oldest / 1000):%Y-%m-%d %H:%M} UTC")
        time.sleep(0.12)
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
    print(f"下载 {BIT} -> {DB_SYM} ...")
    download("15m", "15m")
    download("4h", "4h")
