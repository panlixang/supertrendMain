"""验证 reverse_close（反向平仓）开关。

1) ExitRules / EnhancedExitRules 都带上 reverse_close 字段
2) 回测开启 reverse_close 后：没有任何一笔触发 TP1（tp1 标记全 False），
   出场原因只剩「反向信号 / 末根平仓」——即 TP1、保本、跟踪、2%硬止损全部失效
3) 对照：不开时会有 TP1 / 止损 出场
"""
import datetime as dt
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bt_pattern_page as BP
from position import ExitRules

SYM = "BTC-USDT"
BP.NOTIONAL = 100.0
BP.FEE = 0.05 / 100


def main():
    # 1) 字段存在（含增强版继承）
    from position_enhanced import EnhancedExitRules
    print("ExitRules.reverse_close        =", ExitRules(reverse_close=True).reverse_close)
    print("EnhancedExitRules.reverse_close=", EnhancedExitRules(reverse_close=True).reverse_close)

    # 2) 回测对照
    base, h4 = BP.load(SYM, use_cache=True)
    sigs, opens, highs, lows, closes, up, dn, flip_idx = BP.build_signals(base, h4)
    tss = [c["ts"] for c in base]
    start = int(dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    end = int(dt.datetime(2027, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    win = [s for s in sigs if start <= tss[s["i"]] < end]
    print(f"\n2026 窗口 {len(win)} 笔")

    for rc in (False, True):
        tr = BP.backtest(win, highs, lows, closes, up, dn, flip_idx, reverse_close=rc)
        n_tp1 = sum(1 for t in tr if t["tp1"])
        reasons = Counter(t["reason"] for t in tr)
        net = sum(t["pnl"] for t in tr)
        m = BP.metrics(tr)
        print(f"\n  reverse_close={rc}:")
        print(f"    触发 TP1 的笔数      : {n_tp1}")
        print(f"    出场原因分布         : {dict(reasons)}")
        print(f"    净 {m['tot']:+.2f}U  胜率 {m['wr']:.1f}%  回撤 {m['max_dd']:.2f}%")
        if rc:
            assert n_tp1 == 0, "反向平仓模式下不应有 TP1 触发"
            bad = [r for r in reasons if r not in ("反向信号", "末根平仓")]
            assert not bad, f"反向平仓模式下不应出现 {bad}"
            print("    [OK] 断言通过：TP1 零触发，且无止损/跟踪出场")


if __name__ == "__main__":
    main()
