# -*- coding: utf-8 -*-
"""看 43 上 SKHYNIX K 线最新时间戳, 判断 10-18/19 是否有数据(实时流是否断)。"""
import os, sys, json, urllib.request
from datetime import datetime, timezone
sys.stdout.reconfigure(encoding="utf-8")
H43 = "http://43.108.10.84:5174"


def fmt(ts):
    return datetime.fromtimestamp(ts / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


for path in ["/api/candles?symbol=SKHYNIX-USDT-SWAP&tf=1h",
             "/api/candles?symbol=SKHYNIX-USDT-SWAP"]:
    try:
        with urllib.request.urlopen(H43 + path, timeout=15) as r:
            d = json.loads(r.read().decode())
        arr = d if isinstance(d, list) else (d.get("candles") if isinstance(d, dict) else None)
        if isinstance(arr, dict):
            # candles 可能是 {tf: [...]} 或单列表
            for tf, v in arr.items():
                if isinstance(v, list) and v:
                    print(f"PATH {path}\n  tf={tf} 数量={len(v)} 最新={fmt(v[-1]['ts'])} 首根={fmt(v[0]['ts'])}")
        elif isinstance(arr, list) and arr:
            print(f"PATH {path}\n  数量={len(arr)} 最新={fmt(arr[-1]['ts'])} 首根={fmt(arr[0]['ts'])}")
        else:
            print(f"PATH {path}\n  空/未知结构: {str(d)[:200]}")
    except Exception as e:
        print("PATH", path, "ERR", repr(e)[:200])
