import json, urllib.request
for tag, url in [("43", "http://43.108.10.84:5174/api/trade/symbols"),
                 ("47", "http://47.84.106.154:5174/api/trade/symbols")]:
    try:
        r = urllib.request.urlopen(url, timeout=20)
        d = json.loads(r.read())
        print(f"\n=== {tag} {url} symbols={len(d['symbols'])} ===")
        for s in d["symbols"]:
            base = s["symbol"].replace("-USDT-SWAP", "").replace("-USDT", "")
            print(f"{base:10s} params=periods={s['params']['periods']:2d}, mult={s['params']['multiplier']:.2f}, "
                  f"exit={s.get('tp1_pct')}/{s.get('tp1_ratio')} sl={s.get('sl_mode')}/{s.get('sl_pct')} trail={s.get('trail_with_st')} "
                  f"engine={s.get('score_engine')!r} thresholds={s.get('scoring_full_threshold')}")
    except Exception as e:
        print(f"{tag} error: {e}")
