# -*- coding: utf-8 -*-
import os, json
base = json.load(open("backtest/CL/cl_config.json", encoding="utf-8"))
for sym, name in [("BTC-USDT", "BTC"), ("ETH-USDT", "ETH")]:
    d = os.path.join("backtest", name)
    os.makedirs(d, exist_ok=True)
    c = dict(base)
    c["symbol"] = sym
    out = os.path.join(d, name.lower() + "_config.json")
    json.dump(c, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("written", sym, "->", out)
