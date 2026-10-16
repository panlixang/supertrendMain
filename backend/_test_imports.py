# -*- coding: utf-8 -*-
"""冒烟测试：确认改动后的后端模块能被正常导入（避免服务启动崩溃）。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "backtest"))

import state          # noqa: E402
import flow_gate      # noqa: E402
import feed           # noqa: E402
import router         # noqa: E402

print("IMPORT_OK: state / flow_gate / feed / router")

# 确认 SymbolCfgIn（router）确实带 flow 字段，前端「保存所有配置」能写进去
flds = router.SymbolCfgIn.__fields__
need = ["flow_enabled", "flow_mode", "flow_theta", "flow_veto",
        "flow_lookback", "flow_scale"]
missing = [k for k in need if k not in flds]
print("SymbolCfgIn flow 字段:", "全部存在" if not missing else f"缺失 {missing}")
assert not missing, f"SymbolCfgIn 缺字段: {missing}"

# 确认 upsert 写入目标 cfg 有这些字段
cf = state.SymbolTradeConfig(symbol="X-USDT")
assert all(hasattr(cf, k) for k in need), "SymbolTradeConfig 缺 flow 字段"
print("SymbolTradeConfig flow 字段: 全部存在")
