# -*- coding: utf-8 -*-
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
BACKTEST = os.path.dirname(HERE)
sys.path.insert(0, BACKTEST)
from engine import run_backtest

print("=== 写回方案A后 intc_config.json 正式回测 ===")
run_backtest("INTC-USDT", os.path.join(HERE, "intc_config.json"), HERE,
             out_name="intc_backtest_1h.txt")
