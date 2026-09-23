"""2026 BTC 1h：⑥ 加权打分 与 ②/④/⑤ 两两组合对比。

组合为 AND（都通过才放行）。对照：
    不过滤 baseline / ⑥ 单独 / ⑥+② / ⑥+④ / ⑥+⑤ / ②+④+⑤
两套出场：实盘(TP1 1.5%+保本+ST跟踪+2%兜底) 与 反向平仓。
名义 100U × 1x，单边 taker 0.05%。
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bt_pattern_page as BP
from pattern_trade import SCORE_CUT_DEFAULT

SYM = "BTC-USDT"
NOTIONAL = 100.0
FEE = 0.05 / 100
BP.NOTIONAL = NOTIONAL
BP.FEE = FEE


def reverse_pnl(sigs, closes, flip_idx):
    trades = []
    order = sorted(flip_idx)
    for s in sigs:
        i, nxt = s["i"], None
        for j in order:
            if j > i:
                nxt = j
                break
        if nxt is None:
            continue
        entry, exit_px = closes[i], closes[nxt]
        gross = (exit_px - entry) / entry if s["dir"] > 0 else (entry - exit_px) / entry
        trades.append({"pnl": gross * NOTIONAL - 2 * NOTIONAL * FEE})
    return trades


def report(name, subset, closes, highs, lows, up, dn, flip_idx):
    tr_live = BP.backtest(subset, highs, lows, closes, up, dn, flip_idx)
    m = BP.metrics(tr_live)
    rev = reverse_pnl(subset, closes, flip_idx)
    rn = len(rev); rw = sum(1 for t in rev if t["pnl"] > 0)
    print(f"  {name:16s} {len(subset):>4d}笔(拦{len(win)-len(subset):>3d}) | "
          f"实盘 净{m['tot']:+7.2f}U 胜率{m['wr']:5.1f}% 回撤{m['max_dd']:5.2f}% | "
          f"反向 净{sum(t['pnl'] for t in rev):+7.2f}U 胜率{(rw/rn*100 if rn else 0):5.1f}%")


def main():
    global win
    base, h4 = BP.load(SYM, use_cache=True)
    if not base:
        return
    sigs, opens, highs, lows, closes, up, dn, flip_idx = BP.build_signals(base, h4)
    tss = [c["ts"] for c in base]
    start = int(dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    end = int(dt.datetime(2027, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    win = [s for s in sigs if start <= tss[s["i"]] < end]
    print(f"2026 窗口信号 {len(win)} 笔；⑥ 打分阈值 cut={SCORE_CUT_DEFAULT}")

    P = lambda k: [s for s in win if s[k]]
    print("\n== 2026 组合对比（AND 组合）==")
    report("不过滤 baseline", win, closes, highs, lows, up, dn, flip_idx)
    report("⑥ 单独", P("pass_score"), closes, highs, lows, up, dn, flip_idx)
    report("⑥+② 波动异常", [s for s in win if s["pass_score"] and s["pass_vol"]],
           closes, highs, lows, up, dn, flip_idx)
    report("⑥+④ 极端K", [s for s in win if s["pass_score"] and s["pass_candle"]],
           closes, highs, lows, up, dn, flip_idx)
    report("⑥+⑤ 近高价", [s for s in win if s["pass_score"] and s["pass_near_high"]],
           closes, highs, lows, up, dn, flip_idx)
    report("②+④+⑤(参考)", [s for s in win
                          if s["pass_vol"] and s["pass_candle"] and s["pass_near_high"]],
           closes, highs, lows, up, dn, flip_idx)


if __name__ == "__main__":
    main()
