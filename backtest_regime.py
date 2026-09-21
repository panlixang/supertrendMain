"""
BTC 回测：原 SuperTrend(10,3) 全flip  vs  Regime Engine V2
- 数据：本地 candle_data.db (BTC-USDT 4h)，免联网
- 公平对比：同一 backtest() 引擎（相同费率/滑点/TP 管理）
- 用法：python backtest_regime.py
"""
import sys, os, sqlite3
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # 项目根
BACKEND = r'd:/个人项目代码/supertrendMain/backend'
sys.path.insert(0, BACKEND)

import backtest_st
from backtest_st import backtest, super_trend
from regime_engine import computeRegimeSignals, computeRegimeContext, zonesAt, stateAt, WARMUP

DB = r'd:/个人项目代码/supertrendMain/backend/candle_data.db'
SYMBOL = 'BTC-USDT'
TF = '4h'
TP1 = 0.02   # 与基线用同一套 TP，差异只来自信号选择
TP2 = 0.04


def load_candles(symbol=SYMBOL, tf=TF):
    c = sqlite3.connect(DB)
    rows = c.execute(
        "SELECT ts,o,h,l,c,vol FROM candles WHERE symbol=? AND tf=? ORDER BY ts ASC",
        (symbol, tf)).fetchall()
    c.close()
    return [{"ts": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "vol": r[5]} for r in rows]


def summarize(label, r):
    print(f"  ── {label} ──")
    print(f"     信号/头寸={r['signals']}/{r['trades']}  TP1命中={r['tp1_hits']}  TP2命中={r['tp2_hits']}")
    print(f"     最终净值={r['final_equity']:.2f}  总收益={r['total_return_pct']:.2f}%  "
          f"(费用占初始 {r['fees_pct']:.2f}%)")
    print(f"     胜率={r['win_rate_pct']:.1f}%  平均单笔={r['avg_trade_pct']:.2f}%  "
          f"最大回撤={r['max_drawdown_pct']:.2f}%")


def main():
    k = load_candles()
    n = len(k)
    bh = (k[-1]["c"] / k[0]["c"] - 1) * 100
    from datetime import datetime, timezone
    d0 = datetime.fromtimestamp(k[0]["ts"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    d1 = datetime.fromtimestamp(k[-1]["ts"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    print(f"{'#'*72}\n#  {SYMBOL} {TF}  数据 {n} 根  {d0}..{d1}\n"
          f"#  买入持有(BH)={bh:.2f}%  TP1={TP1*100:.1f}% TP2={TP2*100:.1f}% 含OKX费率+滑点\n{'#'*72}")

    # ── 基线：原 SuperTrend(10,3) 每个 flip 都是信号 ──
    st = super_trend([x["o"] for x in k], [x["h"] for x in k], [x["l"] for x in k],
                     [x["c"] for x in k], 10, 3.0, "hl2", True)
    baseline = [{"action": f["type"], "i": f["i"]} for f in st["flips"]]

    # ── 新模型：Regime Engine V2 信号 ──
    regime = computeRegimeSignals(k)

    # 状态分布（便于解读）
    ctx = computeRegimeContext(k)
    dist = {"trend": 0, "uptrend_consol": 0, "neutral_range": 0,
            "downtrend_consol": 0, "transition": 0, "breakout": 0}
    for i in range(WARMUP, n):
        r, s = zonesAt(k, i, ctx["atr"])
        env = {**ctx, "resistance": r, "support": s}
        dist[stateAt(i, env)] += 1
    cnt = lambda st: sum(1 for sig in regime if sig["state"] == st)
    bs = sum(1 for sig in regime if sig["state"] in ("transition", "breakout"))
    print(f"\n  状态分布(逐根): trend={dist['trend']} 上涨整理={dist['uptrend_consol']} "
          f"横盘={dist['neutral_range']} 下跌整理={dist['downtrend_consol']} "
          f"transition={dist['transition']} breakout={dist['breakout']}  (共 {n-WARMUP} 根)")
    print(f"  /model 信号按状态: 上涨整理={cnt('uptrend_consol')} 横盘={cnt('neutral_range')} "
          f"下跌整理={cnt('downtrend_consol')} breakout/transition={bs}  (趋势态不发信号)  合计 {len(regime)}")

    print(f"\n{'='*72}\n  回测对比（含费率+滑点, TP1={TP1*100:.1f}% TP2={TP2*100:.1f}%)\n{'='*72}")
    rb = backtest(k, baseline, apply_fees=True, tp1_pct=TP1, tp2_pct=TP2)
    rr = backtest(k, regime, apply_fees=True, tp1_pct=TP1, tp2_pct=TP2)
    summarize("原 SuperTrend(10,3) 全 flip", rb)
    summarize("/model 策略模型信号 (range+breakout)", rr)

    print(f"\n{'='*72}\n  差值 (/model - 原ST)\n{'='*72}")
    print(f"     总收益: {rr['total_return_pct']-rb['total_return_pct']:+.2f}pct  "
          f"| 回撤: {rr['max_drawdown_pct']-rb['max_drawdown_pct']:+.2f}pct  "
          f"| 胜率: {rr['win_rate_pct']-rb['win_rate_pct']:+.1f}pct")
    print(f"     BTC 买入持有(基准): {bh:.2f}%")


if __name__ == "__main__":
    main()
