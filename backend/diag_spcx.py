import sqlite3, datetime as dt

con = sqlite3.connect(r"d:/个人项目代码/supertrendMain/backend/candle_data.db")
r = con.execute(
    "select count(1),count(distinct ts),min(ts),max(ts) from candles "
    "where symbol='SPCX-USDT' and tf='15m'").fetchone()
print("15m total/distinct:", r[0], r[1])
print("range", dt.datetime.utcfromtimestamp(r[2] / 1000), "~",
      dt.datetime.utcfromtimestamp(r[3] / 1000))
print("first3", [x[0] for x in con.execute(
    "select ts from candles where symbol='SPCX-USDT' and tf='15m' "
    "order by ts limit 3").fetchall()])
print("last3", [x[0] for x in con.execute(
    "select ts from candles where symbol='SPCX-USDT' and tf='15m' "
    "order by ts desc limit 3").fetchall()])
print("4h rows:", con.execute(
    "select count(1) from candles where symbol='SPCX-USDT' and tf='4h'").fetchone()[0])
con.close()
