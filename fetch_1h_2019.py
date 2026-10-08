"""拉取 BTCUSDT 1h (2019-01-01 ~ 2026-10-08) 历史，存 CSV。"""
import urllib.request, json, time, datetime as dt, csv

SYMBOL = "BTCUSDT"
START_MS = int(dt.datetime(2019, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
END_MS = int(dt.datetime(2026, 10, 8, tzinfo=dt.timezone.utc).timestamp() * 1000)
LIMIT = 1000

def fetch(interval, start_ms, end_ms):
    out, cursor = [], start_ms
    while cursor < end_ms:
        url = (f"https://api.binance.com/api/v3/klines?symbol={SYMBOL}"
               f"&interval={interval}&startTime={cursor}&endTime={end_ms}&limit={LIMIT}")
        req = urllib.request.Request(url, headers={"User-Agent": "histfetch/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
        if not data:
            break
        out.extend(data)
        cursor = int(data[-1][0]) + 1
        time.sleep(0.15)
    return out

rows = fetch("1h", START_MS, END_MS)
fn = "btc_1h_2019_2026.csv"
with open(fn, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["open_time", "open", "high", "low", "close", "vol", "close_time"])
    for r in rows:
        w.writerow([r[0], r[1], r[2], r[3], r[4], r[5], r[6]])
print(f"1h: {len(rows)} rows -> {fn}")
