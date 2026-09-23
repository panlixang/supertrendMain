"""2026 BTC 1h：过滤策略 vs 买入持有(Buy&Hold) 同数据对比。

买持有用与回测完全相同的 base 蜡烛（BP.load 缓存），保证口径一致。
名义 100U × 1x，单边 taker 0.05%（买入+卖出各一次）。
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bt_pattern_page as BP

SYM = "BTC-USDT"
NOTIONAL = 100.0
FEE = 0.05 / 100


def main():
    base, h4 = BP.load(SYM, use_cache=True)
    if not base:
        return
    tss = [c["ts"] for c in base]
    closes = [c["c"] for c in base]

    start = int(dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    end = int(dt.datetime(2026, 9, 24, tzinfo=dt.timezone.utc).timestamp() * 1000)

    idx = [k for k, t in enumerate(tss) if start <= t < end]
    if not idx:
        print("无 2026 窗口数据")
        return
    i0, i1 = idx[0], idx[-1]
    p0, p1 = closes[i0], closes[i1]
    # 买入持有：开仓 i0，平仓 i1，单边费各一次
    bh_gross = (p1 - p0) / p0
    bh_net = bh_gross * NOTIONAL - 2 * NOTIONAL * FEE
    # 买入持有最大回撤（窗口内）
    run_max = closes[i0]
    mdd = 0.0
    for k in range(i0, i1 + 1):
        run_max = max(run_max, closes[k])
        dd = (run_max - closes[k]) / run_max
        mdd = max(mdd, dd)

    fmt = lambda ms: dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
    print(f"2026 窗口: {fmt(tss[i0])} ~ {fmt(tss[i1])}  "
          f"({i1-i0+1} 根 1h K)")
    print(f"  开仓价 {p0:,.1f} → 平仓价 {p1:,.1f}")
    print(f"\n== 买入持有 BTC 现货（1x, 100U）==")
    print(f"  毛收益 {bh_gross*100:+.2f}%  净 {bh_net:+.2f}U  收益率 {bh_net/NOTIONAL*100:+.2f}%  "
          f"最大回撤 {mdd*100:.2f}%")

    print("\n== 同窗口 SuperTrend 过滤策略（来自 _bt_2026_vcn.py，100U/1x）==")
    print("  不过滤 baseline : 实盘 +(-5.23)U / 反向平仓 +4.80U")
    print("  ②+④+⑤ 过滤     : 实盘 +4.11U (胜率54.4%, 最大回撤9.93%) / 反向平仓 +16.30U")
    print(f"\n== 对照 ==\n  买入持有净收益 {bh_net:+.2f}U  vs  ②+④+⑤实盘 {4.11:+.2f}U  "
          f"vs  ②+④+⑤反向平仓 {16.30:+.2f}U")
    print(f"  买入持有回撤 {mdd*100:.2f}%  vs  ②+④+⑤实盘最大回撤 9.93%")


if __name__ == "__main__":
    main()
