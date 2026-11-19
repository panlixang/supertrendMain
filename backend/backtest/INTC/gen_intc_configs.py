# -*- coding: utf-8 -*-
"""生成 INTC 两个降回撤候选配置快照, 并验证方案A 完整回测。
方案A(最小改动/推荐): 仅 scoring_alert_threshold 30->40, 其余全原值(15x3.0 hl2 + pct 4% 出场)。
方案B(回撤更低但改动大): ST 11x3.0 close + gate 40/40/40 + 原 pct 出场。
"""
import os, sys, json, copy
HERE = os.path.dirname(os.path.abspath(__file__))
BACKTEST = os.path.dirname(HERE)
sys.path.insert(0, BACKTEST)
from engine import run_backtest

cfg = json.load(open(os.path.join(HERE, "intc_config.json"), encoding="utf-8"))

# 方案A: 仅 alert 30->40
a = copy.deepcopy(cfg)
a["symbol_cfg"]["scoring_alert_threshold"] = 40.0
json.dump(a, open(os.path.join(HERE, "intc_alert40_config.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)

# 方案B: ST 11x3.0 close + gate 40/40/40
b = copy.deepcopy(cfg)
b["params"]["periods"] = 11
b["params"]["src"] = "close"
b["symbol_cfg"]["scoring_full_threshold"] = 40.0
b["symbol_cfg"]["scoring_half_threshold"] = 40.0
b["symbol_cfg"]["scoring_alert_threshold"] = 40.0
json.dump(b, open(os.path.join(HERE, "intc_opt2_config.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)

print("快照已生成: intc_alert40_config.json (方案A), intc_opt2_config.json (方案B)\n")
print("=== 方案A 完整回测 (仅 alert 30->40, 其余全原值) ===")
run_backtest("INTC-USDT", os.path.join(HERE, "intc_alert40_config.json"), HERE,
             out_name="intc_alert40_1h.txt")
