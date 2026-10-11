import json, os
for sym in ("AMD", "INTC"):
    d = json.load(open(f"_bt2026_data/{sym}_USDT_SWAP.json"))
    for tf in ("1h", "4h"):
        a = d.get(tf) or []
        lo = a[0]["ts"] if a else 0
        hi = a[-1]["ts"] if a else 0
        def fmt(ms):
            import datetime
            return datetime.datetime.utcfromtimestamp(ms/1000).strftime("%Y-%m-%d")
        print(sym, tf, "n=", len(a), "range", fmt(lo), "->", fmt(hi))
