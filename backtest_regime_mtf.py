"""
BTC 回测（多周期版，对齐用户模型）：
- 4h 计算 S/R 支撑压力 + 市场状态；1h 触发信号
  * range 态: 1h 走到 4h 支撑=开多平空, 走到 4h 压力=开空平多
  * breakout 态: 4h 突破, 下一 4h 桶首根 1h 顺势执行
- 对比: 原 SuperTrend(10,3) 也在 1h 上跑（公平同周期），同一 backtest() 引擎
- 用法: python backtest_regime_mtf.py
"""
import sys, os, sqlite3
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
BACKEND = r'd:/个人项目代码/supertrendMain/backend'
sys.path.insert(0, BACKEND)

import backtest_st
from backtest_st import backtest, super_trend
from regime_engine import (computeRegimeSignalsMTF, computeRegimeContext,
                           zonesAt, stateAt, WARMUP, TREND_SCORE_MIN)

DB = r'd:/个人项目代码/supertrendMain/backend/candle_data.db'
SYMBOL = 'BTC-USDT'
TP1 = 0.02
TP2 = 0.04


def load(symbol, tf):
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
    k4 = load(SYMBOL, '4h')
    k1 = load(SYMBOL, '1h')
    bh = (k1[-1]["c"] / k1[0]["c"] - 1) * 100
    from datetime import datetime, timezone
    d0 = datetime.fromtimestamp(k1[0]["ts"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    d1 = datetime.fromtimestamp(k1[-1]["ts"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    print(f"{'#'*72}\n#  {SYMBOL}  多周期  4h 状态/zone + 1h 信号  | 1h K线 {len(k1)} 根 {d0}..{d1}\n"
          f"#  买入持有(BH)={bh:.2f}%  TP1={TP1*100:.1f}% TP2={TP2*100:.1f}% 含OKX费率+滑点\n{'#'*72}")

    # 4h 状态分布（context）
    ctx = computeRegimeContext(k4)
    dist = {"trend": 0, "uptrend_consol": 0, "neutral_range": 0,
            "downtrend_consol": 0, "transition": 0, "breakout": 0}
    for j in range(WARMUP, len(k4)):
        r, s = zonesAt(k4, j, ctx["atr"], win=400)
        env = {**ctx, "resistance": r, "support": s}
        dist[stateAt(j, env)] += 1
    print(f"\n  4h 状态分布(逐根): trend={dist['trend']} 上涨整理={dist['uptrend_consol']} "
          f"横盘={dist['neutral_range']} 下跌整理={dist['downtrend_consol']} "
          f"transition={dist['transition']} breakout={dist['breakout']}  (共 {len(k4)-WARMUP} 根 4h)")

    # 新模型 多周期信号
    sigs, perm4, state4 = computeRegimeSignalsMTF(k4, k1, return_nt=True)
    cnt = lambda st: sum(1 for s in sigs if s["state"] == st)
    bs = sum(1 for s in sigs if s["state"] in ("transition", "breakout"))
    nbuy = sum(1 for s in sigs if s["action"] == "buy")
    nsell = sum(1 for s in sigs if s["action"] == "sell")
    from collections import Counter
    seg = [p for p in perm4[WARMUP:] if p]
    allow_trend = sum(1 for p in seg if p["trend"]["allow"])
    down_trend = sum(1 for p in seg if p["trend"]["down"])
    block_trend = sum(1 for p in seg if not p["trend"]["allow"])
    block_range = sum(1 for p in seg if not p["range"]["allow"])
    wait_brk = sum(1 for p in seg if p["breakout"]["wait"])
    no_chase = sum(1 for p in seg if p["range"]["no_chase"])
    cond_cnt = Counter()
    for p in seg:
        for kk, vv in p["cond"].items():
            if vv:
                cond_cnt[kk] += 1
    avg_ts = sum(p["trend_score"] for p in seg) / len(seg)
    avg_bs = sum(p["breakout_score"] for p in seg) / len(seg)
    print(f"  /model 多周期信号(三 Agent): 上涨整理={cnt('uptrend_consol')} 横盘={cnt('neutral_range')} "
          f"下跌整理={cnt('downtrend_consol')} 突破/过渡={bs}  合计 {len(sigs)}  (在 {len(k1)} 根 1h 上)")
    print(f"  Market Permission Layer(4h桶, 共 {len(seg)}):")
    print(f"    A概率接近={cond_cnt['A']}  B弱ADX={cond_cnt['B']}  C低波动={cond_cnt['C']}  D老化={cond_cnt['D']}")
    print(f"    Trend: 允许={allow_trend}  降权(A/D)={down_trend}  硬禁(B/C/Score<{TREND_SCORE_MIN})={block_trend}")
    print(f"    Range: 禁(C低波动)={block_range}  禁追趋势(D老化)={no_chase}")
    print(f"    Breakout: 等待(C低波动)={wait_brk}")
    print(f"    TrendScore均值={avg_ts:.1f}  BreakoutScore均值={avg_bs:.1f}")
    print(f"  最终信号动作: buy={nbuy}  sell={nsell}")

    # 原 SuperTrend(10,3) 也在 1h 上跑（公平同周期）
    st = super_trend([x["o"] for x in k1], [x["h"] for x in k1],
                     [x["l"] for x in k1], [x["c"] for x in k1], 10, 3.0, "hl2", True)
    baseline = [{"action": f["type"], "i": f["i"]} for f in st["flips"]]

    print(f"\n{'='*72}\n  回测对比（1h 执行, 含费率+滑点, TP1={TP1*100:.1f}% TP2={TP2*100:.1f}%)\n{'='*72}")
    rb = backtest(k1, baseline, apply_fees=True, tp1_pct=TP1, tp2_pct=TP2)
    rr = backtest(k1, sigs, apply_fees=True, tp1_pct=TP1, tp2_pct=TP2)
    summarize(f"原 SuperTrend(10,3) 全 flip (1h)", rb)
    summarize("/model 多周期 (4h zone + 1h 信号)", rr)

    print(f"\n{'='*72}\n  差值 (/model - 原ST)\n{'='*72}")
    print(f"     总收益: {rr['total_return_pct']-rb['total_return_pct']:+.2f}pct  "
          f"| 回撤: {rr['max_drawdown_pct']-rb['max_drawdown_pct']:+.2f}pct  "
          f"| 胜率: {rr['win_rate_pct']-rb['win_rate_pct']:+.1f}pct")
    print(f"     BTC 1h 买入持有(基准): {bh:.2f}%")


if __name__ == "__main__":
    main()
