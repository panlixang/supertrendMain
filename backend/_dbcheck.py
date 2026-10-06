import sqlite3, time
con = sqlite3.connect("candle_data.db")
rows = con.execute(
    "select symbol, tf, count(*), min(ts), max(ts) from candles "
    "group by symbol, tf order by symbol, tf"
).fetchall()
for s, tf, n, lo, hi in rows:
    if tf == "4h":
        lo_s = time.strftime("%Y-%m-%d", time.gmtime(lo/1000)) if lo else "-"
        hi_s = time.strftime("%Y-%m-%d", time.gmtime(hi/1000)) if hi else "-"
        print(f"{s} 4h bars={n} range {lo_s} -> {hi_s}")
