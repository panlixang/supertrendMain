# -*- coding: utf-8 -*-
import json, os, urllib.request, glob

URLS = ["http://43.108.10.84:5174/api/trade/symbols",
        "http://47.84.106.154:5174/api/trade/symbols"]
names = set()
for u in URLS:
    try:
        d = json.loads(urllib.request.urlopen(
            urllib.request.Request(u, headers={"User-Agent": "x"}), timeout=20).read())
        for s in d.get("symbols", []):
            names.add(s["symbol"])
        print("OK", u, "->", len(d.get("symbols", [])), "symbols")
    except Exception as e:
        print("ERR", u, e)

print("--- live 可配置品种 ---")
print(sorted(names))

print("--- 本地 _live_data cbtf 盘点 ---")
for f in sorted(glob.glob("backend/backtest/_live_data/*.json")):
    try:
        d = json.load(open(f, encoding="utf-8"))
    except Exception as e:
        print(os.path.basename(f), "load-err", e)
        continue
    if isinstance(d, dict) and any(isinstance(v, list) for v in d.values()):
        tfs = [k for k, v in d.items() if isinstance(v, list) and v]
        n0 = len(d[tfs[0]]) if tfs else 0
        rng = (d[tfs[0]][0].get("ts"), d[tfs[0]][-1].get("ts")) if n0 else (None, None)
        print("%-22s tfs=%s n[%s]=%d range=%s" % (
            os.path.basename(f), tfs, tfs[0] if tfs else "?", n0, rng))
    else:
        print(os.path.basename(f), "NOT cbtf")
