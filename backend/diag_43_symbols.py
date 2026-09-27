# -*- coding: utf-8 -*-
"""远程读 43 的 pattern 配置，精简列出各品种关键出场/周期参数，核对是否回落全局默认。
只读 GET，不改任何东西。
"""
import json, urllib.request

HOST = "http://43.108.10.84:5174"
UA = {"User-Agent": "diag/1.0"}

with urllib.request.urlopen(urllib.request.Request(
        HOST + "/api/pattern/trade/config", headers=UA), timeout=20) as r:
    d = json.loads(r.read())

cfg = d.get("cfg") or {}
print("全局默认: tp1=%.1f/%.1f  exit_mode=%s  sl=%.1f(%s)  exchange=%s  block_4h=%s" % (
    cfg.get("tp1_pct", 0), cfg.get("tp1_ratio", 0), cfg.get("exit_mode"),
    cfg.get("sl_pct", 0), cfg.get("sl_mode"), cfg.get("exchange"), cfg.get("block_4h")))

print("\n%-20s %-8s %-12s %-12s %-14s %-6s %-6s" % (
    "品种", "enabled", "allow_tfs", "exit_mode", "tp1(pct/ratio)", "sl", "v3"))
print("-" * 92)
for s in d.get("symbols") or []:
    tp1 = s.get("tp1_pct"); tr = s.get("tp1_ratio")
    tp_s = "%s/%s" % (tp1, tr)
    if tp1 is None or tr is None:
        tp_s += " ←回落默认"   # 未显式设置 → 用全局 tp1_pct/tp1_ratio
    sl = "%s/%s" % (s.get("sl_mode"), s.get("sl_pct"))
    if s.get("sl_pct") is None:
        sl += " ←默认"
    print("%-20s %-8s %-12s %-12s %-14s %-6s %-6s" % (
        s.get("symbol"), s.get("enabled"), ",".join(s.get("allow_tfs") or []),
        s.get("exit_mode"), tp_s, sl, s.get("filter_v3")))
    pos = s.get("position")
    if pos:
        print("%-20s 持仓: %s entry=%s 现价=%s pnl=%.2f%% roe=%.2f%% stop=%s" % (
            "", pos.get("side"), pos.get("entry"), pos.get("price"),
            pos.get("pnl_pct", 0), pos.get("roe_pct", 0), pos.get("stop")))
