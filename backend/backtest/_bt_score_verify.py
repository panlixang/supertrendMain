"""验证部署到 pattern_trade 的 ⑥ 加权打分过滤（端到端，2026 BTC 1h）。

用 bt_pattern_page.build_signals 算出每笔信号的 pass_score，
对比：不过滤 baseline vs ⑥ 打分过滤（score<=0.48 保留）。
两套出场：实盘(TP1 1.5%+保本+ST跟踪+2%兜底) 与 反向平仓。
名义 100U × 1x，单边 taker 0.05%。
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bt_pattern_page as BP
# V3 口径：不再有 SCORE_CUT_DEFAULT

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
    print(f"  {name:22s} {len(subset):>4d}笔 | "
          f"实盘 净{m['tot']:+.2f}U 胜率{m['wr']:.1f}% 回撤{m['max_dd']:.2f}% | "
          f"反向 净{sum(t['pnl'] for t in rev):+.2f}U 胜率{(rw/rn*100 if rn else 0):.1f}%")


def main():
    base, h4 = BP.load(SYM, use_cache=True)
    if not base:
        return
    sigs, opens, highs, lows, closes, up, dn, flip_idx = BP.build_signals(base, h4)
    tss = [c["ts"] for c in base]
    start = int(dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    end = int(dt.datetime(2027, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    win = [s for s in sigs if start <= tss[s["i"]] < end]
    print(f"2026 窗口信号 {len(win)} 笔；V3 口径（趋势打分闸门）")

    # V3 口径：用 v3_execute 判断
    sc_pass = [s for s in win if s.get("v3_execute")]

    print("\n== 2026 对比 ==")
    report("不过滤 baseline", win, closes, highs, lows, up, dn, flip_idx)
    report("V3 趋势打分过滤", sc_pass, closes, highs, lows, up, dn, flip_idx)

    print(f"\n  ⑥ 拦截 {len(win)-len(sc_pass)} 笔（放行 {len(sc_pass)}）")
    # 打分分布 sanity
    import collections
    hist = collections.Counter()
    for s in win:
        hist[round(s.get("feat", {}).get("_", 0) or 0, 0)] += 0
    print("  打分开关示例：前 5 笔 pass_score =",
          [s["pass_score"] for s in win[:5]])


if __name__ == "__main__":
    main()
