import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_mu_home as M
for sym in ("INTC-USDT-SWAP", "AMD-USDT-SWAP", "BTC-USDT-SWAP"):
    try:
        data = M._get("https://www.okx.com/api/v5/market/history-candles?instId=%s&bar=1H&limit=2" % sym)
        if data and data.get("code") == "0":
            print(sym, "OK candles=", len(data.get("data", [])))
        else:
            print(sym, "resp code=", data.get("code") if data else "NO-RESP",
                  "msg=", data.get("msg") if data else "")
    except Exception as e:
        print(sym, "ERR", repr(e))
