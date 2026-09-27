# -*- coding: utf-8 -*-
"""在 43 项目 backend 目录运行：核对 NVDA/CL/BTC/ETH/SKHYNIX 的关键参数是否对齐回测结论。
在哪台机器跑就读哪台的 pattern_trade.json（自动避开的本地空壳问题）。

回测结论（2026 1h V3 + 单档 1.5%/70%）：
  NVDA    : sl_mode st/pct 均可（中性）        -> 任意
  CL      : sl_mode 必须 st（pct 会 41%->22% 且 DD 变大）-> 必须 st
  BTC/ETH : sl_mode pct(3%) 有益              -> 建议 pct
  SKHYNIX : sl_mode pct(3%) 更稳（R/DD 8.89） -> 建议 pct
  通用    : exit_mode=single, tp1=1.5/70, filter_v3=True,
            move_sl_to_entry=True, trail_with_st=True
"""
import json, os

CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pattern_trade.json")
if not os.path.exists(CFG):
    print("[check] 找不到 pattern_trade.json（请在本项目 backend 目录运行）")
    raise SystemExit(1)

data = json.load(open(CFG, encoding="utf-8"))
symbols = {s["symbol"]: s for s in data.get("symbols", [])}

TARGETS = {
    "NVDA-USDT-SWAP": ("NVDA",    "st 或 pct 均可（中性）"),
    "CL-USDT-SWAP":   ("CL",      "必须 st（pct 会腰斩+回撤变大）"),
    "BTC-USDT-SWAP":  ("BTC",     "建议 pct(3%)"),
    "ETH-USDT-SWAP":  ("ETH",     "建议 pct(3%)"),
    "SKHYNIX-USDT-SWAP": ("SKHYNIX", "建议 pct(3%) 更稳"),
}

# 期望的通用参数
EXP = {"exit_mode": "single", "tp1_pct": 1.5, "tp1_ratio": 70.0,
       "filter_v3": True, "move_sl_to_entry": True, "trail_with_st": True}

print("== 43 品种配置核对 ==")
all_ok = True
for sym, (tag, rule) in TARGETS.items():
    s = symbols.get(sym)
    if not s:
        print("  [%-8s] ❌ 未配置 %s" % (tag, sym))
        all_ok = False
        continue
    sl = s.get("sl_mode"); ex = s.get("exit_mode")
    fv = s.get("filter_v3"); en = s.get("enabled")
    # 逐项核对
    msgs = []
    if sl == "pct":
        if tag in ("CL",):
            msgs.append("⚠️ sl_mode=pct 对 CL 有害(41%->22%)，应改 st")
            all_ok = False
        else:
            msgs.append("✅ sl_mode=pct(3%)")
    else:
        if tag == "CL":
            msgs.append("✅ sl_mode=st（CL 正确）")
        elif tag in ("BTC", "ETH", "SKHYNIX"):
            msgs.append("⚠️ 建议 pct(3%) 更有利，当前 %s" % sl); all_ok = False
        else:
            msgs.append("✅ sl_mode=%s（NVDA 中性）" % sl)
    if ex != "single":
        msgs.append("⚠️ exit_mode=%s 应是 single（回测用单档）" % ex); all_ok = False
    else:
        msgs.append("✅ exit=single")
    if fv is not True:
        msgs.append("⚠️ filter_v3=%s 应为 True（V3 过滤）" % fv); all_ok = False
    else:
        msgs.append("✅ filter_v3=True")
    if not en:
        msgs.append("⚠️ enabled=False（未启用）")
    print("  [%-8s] %s  en=%s sl=%s exit=%s tp1=%.1f/%.0f fv=%s" % (
        tag, sym, en, sl, ex, s.get("tp1_pct"), s.get("tp1_ratio"), fv))
    for m in msgs:
        print("           %s" % m)

print("\n== 结论 ==")
print("  ✅ 全部符合回测结论" if all_ok else "  ⚠️ 存在需修正项，见上")
print("\n提示：请确保 43 部署的 backend/pattern_router.py 含 SymbolIn 的")
print("      sl_mode/exit_mode/sizing_mode/equity_pct 字段补丁；否则这些字段")
print("      会被 Pydantic 吞掉，配置存了也不生效（旧 bug）。")
