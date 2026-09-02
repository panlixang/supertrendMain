# -*- coding: utf-8 -*-
"""查看线上 MU 的真实配置。"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _live_cfg_backtest import _get, LIVE_URL  # noqa: E402

live = _get(LIVE_URL)
for s in live["symbols"]:
    if "MU" in s.get("symbol", "").upper():
        print(json.dumps(s, ensure_ascii=False, indent=2))
