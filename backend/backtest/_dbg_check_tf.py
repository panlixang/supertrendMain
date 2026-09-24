# -*- coding: utf-8 -*-
import sqlite3
DB = r"d:\个人项目代码\supertrendMain\backend\candle_data.db"
c = sqlite3.connect(DB)
for tf in ("15m", "1h", "4h"):
    row = c.execute(
        "SELECT COUNT(*),MIN(ts),MAX(ts) FROM candles WHERE symbol='BTC-USDT' AND tf=?",
        (tf,)).fetchone()
    print(tf, "count=", row[0], "min=", row[1], "max=", row[2])
print("all tf:", c.execute(
    "SELECT tf,COUNT(*) FROM candles WHERE symbol='BTC-USDT' GROUP BY tf").fetchall())
