# -*- coding: utf-8 -*-
"""在 43 的 backend 目录下运行：只读打印 CL-USDT-SWAP 的全部配置字段，
重点 allow_tfs（决定它看哪些周期的信号）。不改配置。
"""
import os, json
_DIR = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(_DIR, "pattern_trade.json")
if not os.path.exists(CFG):
    print("✗ 找不到 pattern_trade.json —— 确认运行目录是 43 的 backend")
else:
    with open(CFG, encoding="utf-8") as f:
        data = json.load(f)
    cl = next((r for r in (data.get("symbols") or [])
               if (r.get("symbol") or "").upper() == "CL-USDT-SWAP"), None)
    if cl is None:
        print("✗ 43 上未找到 CL-USDT-SWAP")
    else:
        print("==== 43 上 CL-USDT-SWAP 配置 ====")
        for k, v in cl.items():
            mark = "  <== 周期配置，决定看哪些周期信号" if k == "allow_tfs" else ""
            print("  %-18s = %s%s" % (k, v, mark))
        tfs = cl.get("allow_tfs") or []
        print("\n解读：")
        if "1h" in tfs and len(tfs) == 1:
            print("  allow_tfs 仅 1h → CL 只看 1h 信号（9-25 起空），不会下 9-27 多单；")
            print("  若 43 真下了 9-27 多单，说明下单的不是本 pattern_trade 的 ST 信号。")
        else:
            print("  allow_tfs = %s → 含多周期时，4h/1d 的多信号(9-24/9-01 起)会触发开多，" % tfs)
            print("  这正是 9-27 多单的来源；与只看 1h 的 Bitget 自然不同。")
