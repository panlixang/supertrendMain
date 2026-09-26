# -*- coding: utf-8 -*-
"""校验形态页三档止盈改造: 规则构造/按品种覆盖/保本/TP3=0反向平仓/落盘字段。"""
import sys
sys.path.insert(0, r"d:\个人项目代码\supertrendMain\backend")
from state import SymbolTradeConfig
from pattern_trade import PatternTrader
from position_enhanced import EnhancedExitRules, EnhancedPosition, check_enhanced, apply_tp_enhanced

ok = True
def chk(label, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("  PASS " if good else "  FAIL ") + label + f" -> {got!r} (期望 {want!r})")

print("[1] 品种配置默认三档字段存在且为 None (回落全局)")
sc = SymbolTradeConfig(symbol="BTC-USDT")
for f in ("tp1_pct", "tp1_ratio", "tp2_pct", "tp2_ratio", "tp3_pct", "tp3_ratio", "tp3_mode"):
    chk("  " + f, getattr(sc, f, "MISSING"), None)

print("[2a] 全新 PatternConfig 默认值 (未被存档覆盖时)")
from pattern_trade import PatternConfig
_fresh = PatternConfig()
for f, w in (("tp1_pct", 1.0), ("tp1_ratio", 30.0),
             ("tp2_pct", 2.0), ("tp2_ratio", 40.0),
             ("tp3_pct", 3.5), ("tp3_ratio", 100.0), ("tp3_mode", "pct")):
    chk("  " + f, getattr(_fresh, f), w)

t = PatternTrader()
t.save = lambda: None          # 禁止落盘，避免污染真实配置

print("[2b] 存档优先：pattern_trade.json 里的旧值会压过代码默认值")
import os, json
_local_persisted = {}
if os.path.exists("pattern_trade.json"):
    _local_persisted = (json.load(open("pattern_trade.json", encoding="utf-8")).get("cfg") or {})
print(f"  存档 cfg = {_local_persisted or '{}'}")
chk("实读 cfg.tp1_pct 等于存档值而非新默认",
    t.cfg.tp1_pct, _local_persisted.get("tp1_pct", 1.0))
print("  → 部署提示：若服务器存档里有旧 tp1 值，必须在面板显式改成 1.0，否则不生效")

print("[2c] 显式设为标准三档后的全局值")
t.update_cfg(tp1_pct=1.0, tp1_ratio=30.0)
for f, w in (("tp1_pct", 1.0), ("tp1_ratio", 30.0),
             ("tp2_pct", 2.0), ("tp2_ratio", 40.0),
             ("tp3_pct", 3.5), ("tp3_ratio", 100.0), ("tp3_mode", "pct")):
    chk("  cfg." + f, getattr(t.cfg, f), w)

print("[3] rules_for_symbol 返回增强版且参数正确")
t.add_symbol("BTC-USDT-SWAP", allow_tfs=["1h"])
er = t.rules_for_symbol("BTC-USDT-SWAP")
chk("规则类", type(er).__name__, "EnhancedExitRules")
for f, w in (("tp1_pct", 1.0), ("tp1_ratio", 30.0), ("tp2_pct", 2.0),
             ("tp2_ratio", 40.0), ("tp3_pct", 3.5), ("tp3_ratio", 100.0),
             ("move_sl_to_entry", True), ("trail_with_st", True),
             ("sl_mode", "st"), ("sl_pct", 2.0)):
    chk("  " + f, getattr(er, f), w)

print("[4] 按品种覆盖生效 (TP1 改为 0.8% 平 50%)")
t.update_symbol("BTC-USDT-SWAP", tp1_pct=0.8, tp1_ratio=50.0)
er2 = t.rules_for_symbol("BTC-USDT-SWAP")
chk("覆盖 tp1_pct", er2.tp1_pct, 0.8)
chk("覆盖 tp1_ratio", er2.tp1_ratio, 50.0)
chk("未覆盖的 tp2 仍回落全局", er2.tp2_pct, 2.0)

print("[5] TP1 达成 -> 止损移到开仓价（保本）")
pos = EnhancedPosition(symbol="BTC-USDT-SWAP", side="long", tf="1h",
                       entry=100.0, qty=1.0, init_qty=1.0, stop=97.0,
                       leverage=1, entry_ts=0)
apply_tp_enhanced(pos, 101.0, 0.3, er, 1)   # (pos, price, closed_qty, rules, stage)
chk("剩余仓位", round(pos.qty, 4), 0.7)
chk("止损=开仓价", pos.stop, 100.0)
chk("已保本标记", pos.breakeven, True)

print("[6] tp3_pct=0 -> 反向信号平仓（所有价格止盈止损失效）")
t.update_symbol("BTC-USDT-SWAP", tp3_pct=0.0)
er3 = t.rules_for_symbol("BTC-USDT-SWAP")
chk("tp3_pct", er3.tp3_pct, 0.0)
# 大幅盈利也不应触发任何价格止盈
act = check_enhanced(pos, 120.0, er3, 20.0)
chk("价格检查返回 None", act, None)

print("\n结论:", "全部通过" if ok else "存在失败项, 见上")
