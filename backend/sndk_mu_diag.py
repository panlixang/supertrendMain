# -*- coding: utf-8 -*-
"""SNDK/MU 47 服务器实盘对照：本地 bitget 数据能否覆盖 10-14，并算 ST 翻转信号流。"""
import datetime
import urllib.request
import json

import sys
HERE = sys.path[0] if sys.path else "."
try:
    sys.path.insert(0, HERE)
    sys.path.insert(0, "backtest")
    from indicators import super_trend
except Exception:
    pass


def bitget(symbol, tf="1H", limit=400):
    url = (f"https://api.bitget.com/api/v2/mix/market/candles"
           f"?productType=usdt-futures&symbol={symbol}&granularity={tf}&limit={limit}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = json.loads(urllib.request.urlopen(req, timeout=20).read())
    rows = raw.get("data", [])
    out = []
    for r in rows:
        ts = int(r[0])
        out.append({"ts": ts, "o": float(r[1]), "h": float(r[2]),
                    "l": float(r[3]), "c": float(r[4]), "v": float(r[5])})
    out.sort(key=lambda x: x["ts"])
    return out


def flips_in(cs, p, m, start_ms, end_ms):
    st = super_trend([c["o"] for c in cs], [c["h"] for c in cs],
                     [c["l"] for c in cs], [c["c"] for c in cs],
                     periods=p, multiplier=m, change_atr=True)
    res = []
    for f in st.get("flips") or []:
        i = f["i"]
        if start_ms <= cs[i]["ts"] <= end_ms:
            res.append((datetime.datetime.utcfromtimestamp(cs[i]["ts"] / 1000),
                        f["type"], round(cs[i]["c"], 2)))
    return res


for sym in ["SNDKUSDT", "MUUSDT"]:
    print(f"\n===== {sym} =====")
    try:
        cs = bitget(sym, "1H", 400)
    except Exception as e:
        print("  拉取失败:", e)
        continue
    print(f"  根数={len(cs)} 最后一根时间="
          f"{datetime.datetime.utcfromtimestamp(cs[-1]['ts']/1000)}")
    if cs[-1]["ts"] < 1770000000000:  # 远早于 10-14
        print("  ⚠ bitget 数据滞后，无法覆盖 10-14，本地复现不可用")
        continue
    win0 = int(datetime.datetime(2026, 10, 13, 0, 0).timestamp() * 1000)
    win1 = int(datetime.datetime(2026, 10, 15, 0, 0).timestamp() * 1000)
    for (p, m) in [(15, 3.0), (12, 4.0)]:
        fl = flips_in(cs, p, m, win0, win1)
        print(f"  ST({p}x{m}) 10-13~15 翻转: {[(str(t), tp, c) for t, tp, c in fl]}")
