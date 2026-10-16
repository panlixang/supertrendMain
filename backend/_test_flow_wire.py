# -*- coding: utf-8 -*-
"""验证 flow 双因子接入链路：配置字段 → 序列化 → flow 否决/补位 → 持久化往返。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "backtest"))

from state import SymbolTradeConfig
from flow_gate import flow_factor, apply_flow_gate, st_direction

ok = True


def check(label, cond, extra=""):
    global ok
    print(f"{'[PASS]' if cond else '[FAIL]'} {label} {extra}")
    if not cond:
        ok = False


# 1) 配置字段存在且默认关闭，vars() 能序列化（前端靠 vars(st.cfg) 拿字段）
c = SymbolTradeConfig(symbol="TEST-USDT")
v = vars(c)
flow_keys = {k: v[k] for k in v if k.startswith("flow_")}
check("1) SymbolTradeConfig 含 flow 字段且默认关闭",
      len(flow_keys) == 6 and v["flow_enabled"] is False, str(flow_keys))

# 2) flow_factor 方向语义
def mk(n=80, last_vol=500.0, base_vol=100.0, last_c=101.0, base_c=100.0):
    out = []
    for i in range(n):
        is_last = (i == n - 1)
        out.append({
            "ts": i * 3600000, "o": 100.0, "h": 102.0, "l": 98.0,
            "c": last_c if is_last else base_c,
            "vol": last_vol if is_last else base_vol,
        })
    return out

f_up = flow_factor(mk(last_vol=500.0, last_c=101.0), 24, 2.0)   # 放量上涨 → 正
f_dn = flow_factor(mk(last_vol=500.0, last_c=99.0), 24, 2.0)    # 放量下跌 → 负
check("2) flow_factor 放量上涨为正", f_up > 0.3, f"f={f_up:.3f}")
check("2) flow_factor 放量下跌为负", f_dn < -0.3, f"f={f_dn:.3f}")

# 3) veto 模式：想开多(buy)但放量下跌背离 → 否决
cfg_v = SymbolTradeConfig(symbol="T", flow_enabled=True, flow_mode="veto", flow_veto=0.50)
g = apply_flow_gate(mk(last_vol=500.0, last_c=99.0), {"periods": 15, "multiplier": 9.1},
                    {"type": "buy"}, {"trade": True, "reasons": ["趋势开多"]}, cfg_v)
check("3) veto：buy + 放量下跌背离 → 否决",
      g["trade"] is False and any("否决" in r for r in g["reasons"]), str(g["reasons"]))

# 3b) veto 模式：同向(放量上涨 + buy) → 不否决
g2 = apply_flow_gate(mk(last_vol=500.0, last_c=101.0), {"periods": 15, "multiplier": 9.1},
                     {"type": "buy"}, {"trade": True, "reasons": ["趋势开多"]}, cfg_v)
check("3b) veto：buy + 放量上涨同向 → 放行", g2["trade"] is True, str(g2["reasons"]))

# 4) 开关关闭时完全不干预（这是「开关可关闭」的核心保证）
cfg_off = SymbolTradeConfig(symbol="T", flow_enabled=False)
g3 = apply_flow_gate(mk(last_vol=500.0, last_c=99.0), {"periods": 15, "multiplier": 9.1},
                     {"type": "buy"}, {"trade": True, "reasons": ["趋势开多"]}, cfg_off)
check("4) 开关关闭时不干预原信号", g3["trade"] is True and g3["reasons"] == ["趋势开多"],
      str(g3["reasons"]))

# 5) fill 模式：SuperTrend 已转多 + 放量上涨共振 → 补位复活
#    构造一段上涨趋势，让 ST 呈多头
trend_up = []
price = 100.0
for i in range(120):
    price += 0.8
    vol = 500.0 if i == 119 else 100.0
    trend_up.append({"ts": i * 3600000, "o": price - 0.5, "h": price + 0.3,
                     "l": price - 0.6, "c": price, "vol": vol})
st_dir = st_direction(trend_up, {"periods": 15, "multiplier": 9.1})
check("5a) st_direction 上涨段为 +1", st_dir == 1, f"st_dir={st_dir}")

cfg_f = SymbolTradeConfig(symbol="T", flow_enabled=True, flow_mode="fill", flow_theta=0.30)
g4 = apply_flow_gate(trend_up, {"periods": 15, "multiplier": 9.1},
                     {"type": "buy"}, {"trade": False, "reasons": ["被次级过滤拦"]}, cfg_f)
check("5b) fill：ST多头 + 放量上涨共振 → 补位复活",
      g4["trade"] is True and any("补位" in r for r in g4["reasons"]), str(g4["reasons"]))

# 6) 持久化往返：vars(cfg) → 模拟 settings.json → SymbolTradeConfig(...) 重建
cfg_set = SymbolTradeConfig(symbol="T", flow_enabled=True, flow_mode="fill",
                            flow_theta=0.45, flow_veto=0.65, flow_lookback=48, flow_scale=3.0)
e = vars(cfg_set)
loaded = SymbolTradeConfig(
    symbol="T",
    flow_enabled=e.get("flow_enabled", False),
    flow_mode=e.get("flow_mode", "veto"),
    flow_theta=e.get("flow_theta", 0.30),
    flow_veto=e.get("flow_veto", 0.50),
    flow_lookback=e.get("flow_lookback", 24),
    flow_scale=e.get("flow_scale", 2.0),
)
check("6) 持久化往返保留 flow 设置",
      loaded.flow_enabled and loaded.flow_mode == "fill" and loaded.flow_theta == 0.45
      and loaded.flow_veto == 0.65 and loaded.flow_lookback == 48 and loaded.flow_scale == 3.0,
      f"mode={loaded.flow_mode} theta={loaded.flow_theta} lb={loaded.flow_lookback}")

# 7) 旧配置（无 flow 字段）加载时不炸、回落默认关闭
old = {}
loaded2 = SymbolTradeConfig(symbol="T", flow_enabled=old.get("flow_enabled", False))
check("7) 旧配置无 flow 字段 → 默认关闭", loaded2.flow_enabled is False)

print("\n==== 全部通过 ====" if ok else "\n==== 存在失败项 ====")
sys.exit(0 if ok else 1)
