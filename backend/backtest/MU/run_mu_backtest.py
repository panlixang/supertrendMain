# -*- coding: utf-8 -*-
"""MU 首页策略回测 —— 调用通用引擎，参数来自 mu_config.json（线上真实配置）。"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
BACKTEST = os.path.dirname(HERE)          # backend/backtest
sys.path.insert(0, BACKTEST)
from engine import run_backtest

run_backtest(
    db_symbol="MU-USDT",
    config_path=os.path.join(HERE, "mu_config.json"),
    out_dir=HERE,
    out_name="mu_backtest_1h.txt",
)
