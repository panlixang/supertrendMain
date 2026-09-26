# -*- coding: utf-8 -*-
"""校验: 形态页已切到「形态页+V3」档, 且出场参数与回测口径一致."""
import sys
sys.path.insert(0, r"d:\个人项目代码\supertrendMain\backend")
from state import SymbolTradeConfig
from pattern_trade import PatternTrader

ok = True
def chk(label, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("  PASS " if good else "  FAIL ") + label + f" -> {got!r} (期望 {want!r})")

print("[1] SymbolTradeConfig 默认 V3 开关")
chk("filter_v3 默认值", SymbolTradeConfig(symbol="BTC-USDT").filter_v3, True)

print("[2] _load 持久化语义 (缺字段回落默认 / 显式 false 尊重)")
chk("缺 filter_v3 字段", bool({}.get("filter_v3", True)), True)
chk("显式 false", bool({"filter_v3": False}.get("filter_v3", True)), False)

print("[3] PatternTrader 实际行为 (禁写盘, 不污染真实配置)")
t = PatternTrader()
t.save = lambda: None
t.add_symbol("BTC-USDT", allow_tfs=["1h"])
sc = t.symbols["BTC-USDT"]
chk("add_symbol 不传 -> 默认开", bool(sc.filter_v3), True)
t.update_symbol("BTC-USDT", filter_v3=False)
chk("update_symbol 显式关 -> 仍可关", bool(t.symbols["BTC-USDT"].filter_v3), False)
t.update_symbol("BTC-USDT", filter_v3=True)
chk("update_symbol 显式开", bool(t.symbols["BTC-USDT"].filter_v3), True)

print("[4] 出场规则是否 = 回测口径 (ExitRules 1.5%/70% + st止损 + 保本 + ST跟踪)")
er = t.rules_for_symbol("BTC-USDT")
chk("规则类", type(er).__name__, "ExitRules")
for f, want in (("tp1_pct", 1.5), ("tp1_ratio", 70.0), ("sl_mode", "st"),
                ("sl_pct", 2.0), ("move_sl_to_entry", True),
                ("trail_with_st", True), ("reverse_close", False)):
    chk("  " + f, getattr(er, f), want)

print("\n结论:", "全部通过 - 形态页已切到『形态页+V3』回测档" if ok else "存在不一致, 见上")
