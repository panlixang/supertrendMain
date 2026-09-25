import sqlite3, datetime as dt
con = sqlite3.connect(r"d:\个人项目代码\supertrendMain\backend\candle_data.db")
r = con.execute("SELECT MIN(ts),MAX(ts),COUNT(*) FROM candles WHERE symbol='ETH-USDT' AND tf='1h'").fetchone()
print("ETH 1h:", r)
if r[0]:
    print("起", dt.datetime.utcfromtimestamp(r[0]/1000), "止", dt.datetime.utcfromtimestamp(r[1]/1000))
a = int(dt.datetime(2026,1,1,tzinfo=dt.timezone.utc).timestamp()*1000)
b = int(dt.datetime(2027,1,1,tzinfo=dt.timezone.utc).timestamp()*1000)
print("2026根数", con.execute("SELECT COUNT(*) FROM candles WHERE symbol='ETH-USDT' AND tf='1h' AND ts>=? AND ts<?",(a,b)).fetchone())
# 也看下有哪些symbol
print("symbols:", [x[0] for x in con.execute("SELECT DISTINCT symbol FROM candles WHERE tf='1h'").fetchall()])
