# -*- coding: utf-8 -*-
import os, sys
BACKTEST = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKTEST)
from engine import _load_tf
for s in ["BTC-USDT", "ETH-USDT", "CL-USDT", "NVDA-USDT", "SNDK-USDT"]:
    try:
        n = len(_load_tf(s, "1h"))
        n15 = len(_load_tf(s, "15m"))
        n4 = len(_load_tf(s, "4h"))
        print(f"{s}: 1h={n} 15m={n15} 4h={n4}")
    except Exception as e:
        print(s, "ERR", repr(e))
