# -*- coding: utf-8 -*-
"""拉取 Hyperliquid xyz:NVDA 15m/30m/1h/4h K线 → backend/candle_data.db

背景：本地 candle_data.db 已缺失，api.bitget.com 在当前网络不可达（SNI 阻断）。
Gate.io candles 接口强制要求 API key，Bybit/OKX/MEXC 超时或被阻断；
Hyperliquid 公开 info 接口可达且 xyz dex 上有 NVDA 股票永续（跟踪 NVDA 股价）。

数据入库格式与 engine._load_tf 一致：
    candles(symbol='NVDA-USDT', tf, ts(ms), o, h, l, c, vol)

candleSnapshot 单次最多返回 5000 根，xyz:NVDA 上市约 2026-05-03，
15m 仅约 52 天 / 30m 约 104 天 / 1h 约 209 天，属数据源上限。

运行：py fetch_nvda_hl.py
"""
import json
import os
import sqlite3
import sys
import time
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(os.path.dirname(HERE), "candle_data.db")  # backend/candle_data.db
COIN = "xyz:NVDA"
DB_SYMBOL = "NVDA-USDT"
TF_MS = {"15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000}


def post(url, payload, timeout=30, retries=4):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise last


def fetch_all(interval):
    """candleSnapshot 单次最多返回最近 5000 根；已实测 15m 存储 2026-10-07 起、
    30m 2026-08-16 起、1h 2026-05-03 起，均 ≤5000 根，单次即可取全。"""
    for _ in range(3):
        try:
            cs = post("https://api.hyperliquid.xyz/info",
                      {"type": "candleSnapshot", "req": {"coin": COIN, "interval": interval, "startTime": 0}})
            if isinstance(cs, list):
                return [{"ts": c["t"], "o": float(c["o"]), "h": float(c["h"]),
                         "l": float(c["l"]), "c": float(c["c"]), "vol": float(c.get("v") or 0)}
                        for c in sorted(cs, key=lambda x: x["t"])]
        except Exception as e:
            print(f"  {interval} 重试: {e}", flush=True)
    return []


def main():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS candles(
        symbol TEXT, tf TEXT, ts INTEGER, o REAL, h REAL, l REAL, c REAL, vol REAL,
        PRIMARY KEY(symbol, tf, ts))""")
    total = 0
    for tf in TF_MS:
        rows = fetch_all(tf)
        con.execute("DELETE FROM candles WHERE symbol=? AND tf=?", (DB_SYMBOL, tf))
        con.executemany(
            "INSERT OR REPLACE INTO candles VALUES(?,?,?,?,?,?,?,?)",
            [(DB_SYMBOL, tf, r["ts"], r["o"], r["h"], r["l"], r["c"], r["vol"]) for r in rows])
        total += len(rows)
        if rows:
            print(f"{tf}: {len(rows)} 根  {time.strftime('%Y-%m-%d %H:%M', time.gmtime(rows[0]['ts']/1000))}"
                  f" ~ {time.strftime('%Y-%m-%d %H:%M', time.gmtime(rows[-1]['ts']/1000))}", flush=True)
    con.commit()
    con.close()
    print(f"[完成] 共 {total} 根 → {DB_PATH}")


if __name__ == "__main__":
    main()
