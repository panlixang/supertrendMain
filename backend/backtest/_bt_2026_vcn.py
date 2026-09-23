"""2026 BTC 1h · 仅 ②+④+近高价 过滤的 SuperTrend 信号回测。

信号：原始 SuperTrend(10×3.0) 翻转。
过滤（仅这三个，不动 flip/position，也不加 4h 硬门槛）：
    ② 波动异常   ATR_percent > 0.8 拦截
    ④ 极端K     candle_range_ATR > 3 拦截
    ⑤ 近高价     距48根高点 (Hi48-C)/ATR > 3.47 拦截
名义 100U × 1x，单边 taker 0.05%。

两套出场对照：
    A) 实盘出场 = bt_pattern_page.backtest（TP1 1.5% 平70% + 保本 + ST 跟踪 + 2%兜底）
    B) 反向平仓 = 下一根反向翻转收盘平（与 _raw_full.csv 同口径）

用法：python _bt_2026_vcn.py
"""
from __future__ import annotations

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bt_pattern_page as BP
import history

SYM = "BTC-USDT"
NOTIONAL = 100.0
FEE = 0.05 / 100          # 单边 taker

BP.NOTIONAL = NOTIONAL
BP.FEE = FEE


def reverse_pnl(sigs, closes, flip_idx):
    """反向平仓口径：每笔信号在下一根反向翻转收盘平仓。返回 trades 列表（含 pnl）。"""
    trades = []
    order = sorted(flip_idx)
    for s in sigs:
        i = s["i"]
        long = s["dir"] > 0
        # 下一根翻转
        nxt = None
        for j in order:
            if j > i:
                nxt = j
                break
        if nxt is None:
            continue
        entry = closes[i]
        exit_px = closes[nxt]
        gross = (exit_px - entry) / entry if long else (entry - exit_px) / entry
        pnl = gross * NOTIONAL - 2 * NOTIONAL * FEE
        trades.append({"pnl": pnl, "ret_pct": pnl / NOTIONAL * 100})
    return trades


def main():
    base, h4 = BP.load(SYM, use_cache=True)
    if not base:
        return
    sigs, opens, highs, lows, closes, up_plot, dn_plot, flip_idx = BP.build_signals(base, h4)

    tss = [c["ts"] for c in base]
    start = int(dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    end = int(dt.datetime(2027, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    win = [s for s in sigs if start <= s["ts"] < end]
    print(f"2026 窗口信号总数: {len(win)}")

    # 仅 ②+④+⑤
    fv = [s for s in win if s["pass_vol"] and s["pass_candle"] and s["pass_near_high"]]
    print(f"  ②+④+⑤ 放行: {len(fv)}  （拦截 {len(win) - len(fv)}）")

    def report(name, subset):
        tr_live = BP.backtest(subset, highs, lows, closes, up_plot, dn_plot, flip_idx)
        tr_rev = reverse_pnl(subset, closes, flip_idx)
        m1 = BP.metrics(tr_live)
        rev_net = sum(t["pnl"] for t in tr_rev)
        rev_n = len(tr_rev)
        rev_w = sum(1 for t in tr_rev if t["pnl"] > 0)
        print(f"\n== {name} （{len(subset)} 笔）==")
        print(f"  实盘出场 : 胜率 {m1['wr']:.1f}%  净 {m1['tot']:+.2f}U  收益率 {m1['ret']:+.2f}%  "
              f"最大回撤 {m1['max_dd']:.2f}%  最长连亏 {m1['streak']}")
        print(f"  反向平仓 : 胜率 {rev_w/rev_n*100:.1f}%  净 {rev_net:+.2f}U  （{rev_n} 笔，{NOTIONAL:.0f}U名义）")

    report("A) 不过滤(baseline)", win)
    report("B) 仅 ②+④+⑤", fv)


if __name__ == "__main__":
    main()
