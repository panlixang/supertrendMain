# -*- coding: utf-8 -*-
import os, sys
BACKTEST = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKTEST)
from engine import run_backtest

cfg = sys.argv[1] if len(sys.argv) > 1 else "intc_config.json"
out = sys.argv[2] if len(sys.argv) > 2 else "verify.txt"
sym = sys.argv[3] if len(sys.argv) > 3 else "INTC-USDT"
cfg_path = os.path.abspath(cfg)
out_dir = os.path.dirname(cfg_path)
print("=== 配置:", cfg, "品种:", sym, "===")
run_backtest(sym, cfg_path, out_dir, out_name=out)
