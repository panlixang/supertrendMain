# -*- coding: utf-8 -*-
"""CL 首页策略回测 —— 调用通用引擎，参数来自 cl_config.json（线上真实配置）。"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
BACKTEST = os.path.dirname(HERE)          # backend/backtest
sys.path.insert(0, BACKTEST)
from engine import run_backtest

run_backtest(
    db_symbol="CL-USDT",
    config_path=os.path.join(HERE, "cl_config.json"),
    out_dir=HERE,
    out_name="cl_backtest_1h.txt",
)
