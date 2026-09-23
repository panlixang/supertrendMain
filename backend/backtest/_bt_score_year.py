"""按年份跑 BTC 1h：⑥ 加权打分 / ⑤ 近高价 / ⑥+⑤ 对比。

用法：python _bt_score_year.py 2025   （默认 2025）
对照：不过滤 baseline / ⑥ 单独 / ⑤ 单独 / ⑥+⑤ / ②+④+⑤
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


def report(name, subset, total, closes, highs, lows, up, dn, flip_idx):
    tr_live = BP.backtest(subset, highs, lows, closes, up, dn, flip_idx)
    m = BP.metrics(tr_live)
    rev = reverse_pnl(subset, closes, flip_idx)
    rn = len(rev); rw = sum(1 for t in rev if t["pnl"] > 0)
    print(f"  {name:14s} {len(subset):>4d}笔(拦{total-len(subset):>3d}) | "
          f"实盘 净{m['tot']:+8.2f}U 胜率{m['wr']:5.1f}% 回撤{m['max_dd']:6.2f}% | "
          f"反向 净{sum(t['pnl'] for t in rev):+8.2f}U 胜率{(rw/rn*100 if rn else 0):5.1f}%")


def main():
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2025
    base, h4 = BP.load(SYM, use_cache=True)
    if not base:
        return
    sigs, opens, highs, lows, closes, up, dn, flip_idx = BP.build_signals(base, h4)
    tss = [c["ts"] for c in base]
    start = int(dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    end = int(dt.datetime(year + 1, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    win = [s for s in sigs if start <= tss[s["i"]] < end]
    print(f"{year} 窗口信号 {len(win)} 笔；⑥ 打分阈值 cut={SCORE_CUT_DEFAULT}")

    print(f"\n== {year} 对比 ==")
    report("不过滤 baseline", win, len(win), closes, highs, lows, up, dn, flip_idx)
    report("⑥ 单独", [s for s in win if s["pass_score"]], len(win),
           closes, highs, lows, up, dn, flip_idx)
    report("⑤ 单独", [s for s in win if s["pass_near_high"]], len(win),
           closes, highs, lows, up, dn, flip_idx)
    report("⑥+⑤ 近高价", [s for s in win if s["pass_score"] and s["pass_near_high"]],
           len(win), closes, highs, lows, up, dn, flip_idx)
    report("②+④+⑤(参考)", [s for s in win
                          if s["pass_vol"] and s["pass_candle"] and s["pass_near_high"]],
           len(win), closes, highs, lows, up, dn, flip_idx)


if __name__ == "__main__":
    main()
