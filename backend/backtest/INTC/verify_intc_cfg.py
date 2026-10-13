# -*- coding: utf-8 -*-
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
BACKTEST = os.path.dirname(HERE)
sys.path.insert(0, BACKTEST)
from engine import run_backtest

cfg = sys.argv[1] if len(sys.argv) > 1 else "intc_config.json"
out = sys.argv[2] if len(sys.argv) > 2 else "intc_verify.txt"
print("=== 配置:", cfg, "===")
run_backtest("INTC-USDT", os.path.join(HERE, cfg), HERE, out_name=out)
