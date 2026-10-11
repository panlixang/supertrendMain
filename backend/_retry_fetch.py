import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_mu_home as M
for sym in ("INTC-USDT-SWAP", "AMD-USDT-SWAP"):
    for attempt in range(3):
        a = M.fetch_candles(sym, "1h", 20)
        print(sym, "attempt", attempt, "1h=", len(a))
        if a:
            break
        time.sleep(2)
