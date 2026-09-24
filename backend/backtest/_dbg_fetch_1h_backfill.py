# -*- coding: utf-8 -*-
"""独立进程: 将 1h 数据从现有最旧(2022-09)往回补到 2022-01, 用于 15m 信号的 4x 对齐"""
import sqlite3, math, time, datetime as dt, urllib.request, json

DB = r"d:\个人项目代码\supertrendMain\backend\candle_data.db"
SYM = "BTC-USDT"
UTC = dt.timezone.utc
START_MS = int(dt.datetime(2022, 1, 1, tzinfo=UTC).timestamp() * 1000)


def _get(url, timeout=20):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "st-signals/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        print("  REST 失败:", e)
        return None


con = sqlite3.connect(DB)
have = con.execute("SELECT MIN(ts) FROM candles WHERE symbol=? AND tf='1h'", (SYM,)).fetchone()[0]
con.close()
after = have
print(f"1h 现有最旧 {dt.datetime.fromtimestamp(after/1000, UTC).date()}, 续传到 2022-01-01")
collected = {}
pages = 0
while True:
    url = f"https://www.okx.com/api/v5/market/history-candles?instId={SYM}&bar=1H&limit=300&after={after}"
    d = None
    for _ in range(4):
        d = _get(url)
        if d:
            break
        time.sleep(1.5)
    if not d or d.get("code") != "0" or not d.get("data"):
        print(f"  停止, 已累计 {len(collected)} 根")
        break
    for r in d["data"]:
        ts = int(r[0])
        if ts < START_MS:
            continue
        collected[ts] = (ts, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
    mints = min(int(r[0]) for r in d["data"])
    if mints <= START_MS:
        break
    after = mints
    pages += 1
    time.sleep(0.15)
    if pages % 20 == 0:
        print(f"  已抓 {len(collected)} 根, 最旧 {dt.datetime.fromtimestamp(after/1000, UTC).date()}")

if collected:
    con = sqlite3.connect(DB)
    con.executemany(
        "INSERT INTO candles(symbol,tf,ts,o,h,l,c,vol) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(symbol,tf,ts) DO NOTHING",
        [(SYM, "1h", *v) for v in collected.values()])
    con.commit()
    con.close()
    print(f"1h 回填写入 {len(collected)} 根, 最旧 "
          f"{dt.datetime.fromtimestamp(min(collected)/1000, UTC).date()}")
else:
    print("1h 无新数据")
