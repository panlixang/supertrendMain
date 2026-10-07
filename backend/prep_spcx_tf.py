# -*- coding: utf-8 -*-
"""SPCX 在 bitget 只有 15m 数据，重采样出 1h / 4h 写回 candle_data.db，
供形态识别页+V3 回测使用（对齐 bt_spcx_v3.py 的 resample 逻辑）。"""
from __future__ import annotations

import sqlite3

from bt_2026_weekend import resample_ms
from sl2_tf_sweep import load_db

SYM = "SPCX-USDT"
DB = "candle_data.db"


def main():
    c15 = load_db(SYM, "15m")
    print("15m 根数:", len(c15))
    if not c15:
        print("无 15m 数据，先跑 download_spcx.py")
        return
    c1h = resample_ms(c15, 60 * 60_000, 4)      # 15m -> 1h
    c4h = resample_ms(c1h, 4 * 60 * 60_000, 4)  # 1h  -> 4h
    print("重采样后 1h:", len(c1h), " 4h:", len(c4h))
    con = sqlite3.connect(DB)
    for tf, cs in (("1h", c1h), ("4h", c4h)):
        rows = [(SYM, tf, r["ts"], r["o"], r["h"], r["l"], r["c"], r["vol"])
                for r in cs]
        con.executemany(
            "INSERT INTO candles(symbol,tf,ts,o,h,l,c,vol) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(symbol,tf,ts) DO UPDATE SET "
            "o=excluded.o,h=excluded.h,l=excluded.l,c=excluded.c,vol=excluded.vol",
            rows)
        con.commit()
        print(f"  写入 {tf}: {len(rows)} 根")
    con.close()


if __name__ == "__main__":
    main()
