# -*- coding: utf-8 -*-
"""从 OKX 历史接口拉指定 instId 的 1h/4h/15m K线到本地 candle_data.db。
用法: python _dl_ohlcv.py <INST> <SYM>   (例如 NVDA-USDT-SWAP NVDA-USDT)
若省略参数则用默认的 NVDA-USDT-SWAP / NVDA-USDT。
"""
import urllib.request, json, sqlite3, os, time, sys
from datetime import datetime, timezone
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

INST = sys.argv[1] if len(sys.argv) > 1 else "NVDA-USDT-SWAP"
SYM = sys.argv[2] if len(sys.argv) > 2 else "NVDA-USDT"
ROOT = "d:/个人项目代码/supertrendMain/backend"
START = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def fetch(bar, after):
    url = f"https://www.okx.com/api/v5/market/history-candles?instId={INST}&bar={bar}&limit=100"
    if after:
        url += f"&after={after}"
    for _ in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.loads(r.read().decode())
            return d.get("data", [])
        except Exception as e:
            print("  retry", repr(e)[:60])
            time.sleep(1)
    return []


def dl(bar):
    rows = []
    after = None
    pages = 0
    while True:
        data = fetch(bar, after)
        if not data:
            break
        rows.extend(data)
        last = int(data[-1][0])
        pages += 1
        if last <= START:
            break
        after = last
        time.sleep(0.2)
    rows = [r for r in rows if int(r[0]) >= START]
    rows.sort(key=lambda r: int(r[0]))
    seen = set()
    uniq = []
    for r in rows:
        if r[0] in seen:
            continue
        seen.add(r[0])
        uniq.append(r)
    print(f"  {bar}: pages={pages} rows={len(uniq)} "
          f"range={uniq[0][0] if uniq else None}~{uniq[-1][0] if uniq else None}")
    return uniq


def save(tf, rows):
    con = sqlite3.connect(os.path.join(ROOT, "candle_data.db"))
    n = 0
    for r in rows:
        con.execute(
            "INSERT INTO candles(symbol,tf,ts,o,h,l,c,vol) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(symbol,tf,ts) DO UPDATE SET o=excluded.o,h=excluded.h,"
            "l=excluded.l,c=excluded.c,vol=excluded.vol",
            (SYM, tf, int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])))
        n += 1
    con.commit()
    con.close()
    return n


print(f"downloading {INST} -> {SYM}")
for tf, bar in [("1h", "1H"), ("4h", "4H"), ("15m", "15m")]:
    print("downloading", tf)
    rows = dl(bar)
    if rows:
        save(tf, rows)
print("[完成] 数据已写入本地 db")
