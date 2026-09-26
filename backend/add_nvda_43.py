# -*- coding: utf-8 -*-
"""在 43 服务器【本项目 backend 目录】下运行，把 NVDA-USDT-SWAP 写进 pattern_trade.json。

为什么不能直接改本地：本地 pattern_trade.json 只是空壳模板（symbols=[]），
线上配置在 43 自己的文件里。此脚本 import pattern_trade 后会 load *当前目录* 的
json（在 43 跑就读 43 的），add_symbol 再写回 *当前目录* 的 json —— 即在哪台机器
运行就改哪台的配置，自动保留该机器已有的品种（如 BTC/ETH）。

参数对齐回测最优解（见 _nvda_v3_2026.py）：
  过滤 V3 + 单档 1.5%/70% + 3%硬止损（NVDA 上 3% 与 ST基线等价，中性无害，
  与 43 现有 BTC/ETH 的 3% 口径保持一致）。

运行后生效方式（二选一）：
  A. 重启 43 的形态下单服务（_load 重新读 json）—— 推荐，确保持久生效。
  B. 若服务在跑不想重启，用等价 curl 打到运行实例（见下方注释）。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pattern_trade

t = pattern_trade.trader
SYM = "NVDA-USDT-SWAP"

if SYM in t.symbols:
    print("[add_nvda] %s 已存在，先移除重建" % SYM)
    t.remove_symbol(SYM)

r = t.add_symbol(
    SYM,
    enabled=True,            # 加入即启用；43 重启后开始监听
    margin_usdt=10.0,        # 固定保证金 10U，可在前端按 U/% 改
    leverage=3,
    allow_tfs=["1h"],        # 基础周期 1h
    sizing_mode="fixed",
    tp1_pct=1.5, tp1_ratio=70.0,
    tp2_pct=None, tp2_ratio=None, tp3_pct=None, tp3_ratio=None, tp3_mode=None,
    exit_mode="single",      # 单档止盈（与回测一致）
    sl_pct=3.0, sl_mode="pct",   # 3% 硬止损（NVDA 上中性，与 BTC/ETH 口径统一）
    move_sl_to_entry=True, trail_with_st=True, reverse_close=False,
    filter_v3=True,          # 启用 V3 过滤
)
print("[add_nvda] add_symbol 返回:", r)
print("[add_nvda] 当前品种:", sorted(t.symbols.keys()))

# ── 运行实例热加（不重启服务时替代方案，取消注释并按 43 实际地址改）──
# import urllib.request, json
# req = urllib.request.Request(
#     "http://<43-host>:<port>/api/pattern/trade/symbols",
#     data=json.dumps({
#         "action": "add", "symbol": "NVDA-USDT-SWAP",
#         "enabled": True, "margin_usdt": 10.0, "leverage": 3,
#         "allow_tfs": ["1h"], "sizing_mode": "fixed",
#         "tp1_pct": 1.5, "tp1_ratio": 70.0,
#         "exit_mode": "single", "sl_pct": 3.0, "sl_mode": "pct",
#         "move_sl_to_entry": True, "trail_with_st": True,
#         "reverse_close": False, "filter_v3": True,
#     }).encode(),
#     headers={"Content-Type": "application/json"}, method="POST")
# print(urllib.request.urlopen(req).read().decode())
