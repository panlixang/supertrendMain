import sqlite3, datetime as dt
c = sqlite3.connect("candle_data.db")
for tf in ["15m", "1h", "4h", "30m"]:
    r = c.execute("select count(*),min(ts),max(ts) from candles "
                  "where symbol=? and tf=?", ("SPCX-USDT", tf)).fetchone()
    n, mn, mx = r
    if mn:
        print(tf, "n=", n, "range",
              dt.datetime.utcfromtimestamp(mn/1000), "~",
              dt.datetime.utcfromtimestamp(mx/1000))
    else:
        print(tf, "空")
