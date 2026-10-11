import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_mu_home as M
for sym in ("AMD-USDT-SWAP", "INTC-USDT-SWAP", "BTC-USDT-SWAP"):
    try:
        data = M._get("https://www.okx.com/api/v5/market/history-candles?instId=%s&bar=1H&limit=2" % sym)
        print(sym, "->", (data.get("code"), len(data.get("data", [])) if data else "NO-RESP"))
    except Exception as e:
        print(sym, "ERR", e)
