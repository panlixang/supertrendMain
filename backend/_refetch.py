# -*- coding: utf-8 -*-
"""重新拉取 INTC/AMD 完整长度(=BARS26)的 1h/4h 数据，覆盖本地缓存。"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_mu_home as M

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "_bt2026_data")
SYMS = {"INTC": "INTC-USDT-SWAP", "AMD": "AMD-USDT-SWAP"}
NEED = {"1h": 7300, "4h": 1900}

for base, sym in SYMS.items():
    d = {}
    for tf, n in NEED.items():
        t0 = time.time()
        arr = M.fetch_candles(sym, tf, n)
        d[tf] = arr
        print(f"  {base} {tf}: 拉到 {len(arr)} 根 ({time.time()-t0:.1f}s)", flush=True)
    json.dump(d, open(os.path.join(DATA, f"{base}_USDT_SWAP.json"), "w"))
    print(f"  已覆盖保存 {base}_USDT_SWAP.json", flush=True)
print("DONE", flush=True)
