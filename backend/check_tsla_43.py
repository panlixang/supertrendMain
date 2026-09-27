# -*- coding: utf-8 -*-
"""在 43 的 backend 目录下运行：读 43 自己的 pattern_trade.json，
核对 TSLA 是否符合 1h-V3-单档1.5%/70%-3%硬止损 的回测最优解。
（本脚本只读不改配置文件）
"""
import os, json

_DIR = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(_DIR, "pattern_trade.json")

# 回测最优解对 TSLA 的推荐值（单档 ExitRules 口径）
WANT = {
    "enabled": True,
    "allow_tfs": ["1h"],          # 千万别含 15m（15m 上 BTC/MU 失效/反转）
    "filter_v3": True,
    "exit_mode": "single",        # 单档：只用 tp1，剩余靠保本+跟踪/反向
    "tp1_pct": 1.5,
    "tp1_ratio": 70.0,
    "sl_mode": "pct",             # 3% 真实硬止损
    "sl_pct": 3.0,
    "move_sl_to_entry": True,
    "trail_with_st": True,
    "reverse_close": False,
}
# 全局默认（PatternConfig），品种字段为 None 时回落到这些 —— 与回测不符需显式覆盖
GLOBAL_DEFAULT = {
    "exit_mode": "multi", "tp1_pct": 1.0, "tp1_ratio": 30.0,
    "sl_mode": "st", "sl_pct": 2.0, "filter_v3": True,
    "allow_tfs": ["1h"], "enabled": False,
}

def main():
    if not os.path.exists(CFG):
        print("✗ 在 %s 找不到 pattern_trade.json —— 确认运行目录是 43 的 backend" % CFG)
        return
    with open(CFG, encoding="utf-8") as f:
        data = json.load(f)
    symbols = data.get("symbols") or []
    print("43 当前品种数: %d" % len(symbols))
    for row in symbols:
        print("  - %s enabled=%s tfs=%s" % (
            row.get("symbol"), row.get("enabled"), row.get("allow_tfs")))

    tsla = next((r for r in symbols if (r.get("symbol") or "").upper() == "TSLA-USDT-SWAP"), None)
    if tsla is None:
        print("\n✗ 未找到 TSLA-USDT-SWAP —— 确认已在 43 加好")
        return

    print("\n==== TSLA 逐项核对（回测最优解 vs 43 实际，None=回落全局默认）====")
    all_ok = True
    for k, want in WANT.items():
        actual = tsla.get(k)
        if actual is None:
            # 看全局默认是否刚好等于推荐
            gd = GLOBAL_DEFAULT.get(k)
            if gd == want:
                print("  ✓ %-18s = <None> 回落全局默认=%s (与推荐一致)" % (k, gd))
            else:
                print("  ✗ %-18s = <None> 回落全局默认=%s ≠ 推荐 %s  ⚠需显式设置" % (k, gd, want))
                all_ok = False
        elif actual == want:
            print("  ✓ %-18s = %s" % (k, actual))
        else:
            print("  ✗ %-18s = %s ≠ 推荐 %s" % (k, actual, want))
            all_ok = False

    # 额外提示：allow_tfs 含 15m 是危险项
    tfs = tsla.get("allow_tfs") or GLOBAL_DEFAULT["allow_tfs"]
    if "15m" in tfs:
        print("  ✗ allow_tfs 含 15m —— 15m 上 TSLA 未测但 BTC/MU 已证实小周期失效，建议移除")
        all_ok = False

    print("\n==== 结论 ====")
    if all_ok:
        print("✓ TSLA 配置与回测最优解完全一致，可上线（记得重启 43 服务加载新 json）")
    else:
        print("✗ 存在不一致项，按上面 ✗ 标记修正后再重启 43")

if __name__ == "__main__":
    main()
