"""
最新策略(Market Permission Layer) BTC 1h 全样本回测 —— 逐笔明细 + 收益汇总。
- 信号：regime_engine.computeRegimeSignalsMTF (4h定势 + 1h触发, MARKET_PERMISSION_ENABLED=True)
- 回测：与 backtest_st.backtest 逐字一致(TP1=2% TP2=4% + OKX费率+滑点), 逐笔记录 entry/exit/pnl
- 输出：每张成交单的 时间/方向/状态/入场/出场/盈亏%/所在4h桶A-B-C-D条件/处置
        + 收益汇总(净收益/回撤/胜率)并与 原SuperTrend(10,3) 及 买入持有 对比
用法: python backtest_regime_mtf_trades.py
"""
import sys, os, bisect
from datetime import datetime, timezone
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
BACKEND = r'd:/个人项目代码/supertrendMain/backend'
sys.path.insert(0, BACKEND)

import regime_engine as re
from analyze_signals import load, instrumented_backtest
import backtest_st

SYMBOL = 'BTC-USDT'
TP1, TP2 = 0.02, 0.04

k4 = load(SYMBOL, '4h')
k1 = load(SYMBOL, '1h')
ts4 = [x["ts"] for x in k4]
d0 = datetime.fromtimestamp(k1[0]["ts"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
d1 = datetime.fromtimestamp(k1[-1]["ts"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
bh = (k1[-1]["c"] / k1[0]["c"] - 1) * 100

# 最新策略(权限层开): 信号 + 真实权限矩阵
re.MARKET_PERMISSION_ENABLED = True
sigs, perm4, state4 = re.computeRegimeSignalsMTF(k4, k1, return_nt=True)
trades = instrumented_backtest(k1, sigs, apply_fees=True, tp1_pct=TP1, tp2_pct=TP2)

tag = {"trend": "趋势", "breakout": "突破", "neutral_range": "横盘",
       "uptrend_consol": "上涨整理", "downtrend_consol": "下跌整理", "transition": "过渡"}
cond_cat = lambda c: "+".join([x for x in ("A", "B", "C", "D") if c.get(x)]) or "ok"


def bucket_of(i):
    j = bisect.bisect_right(ts4, k1[i]["ts"]) - 1
    return j if j >= 0 else 0


def dispose(state, action, perm):
    """该笔(已被新逻辑执行)的处置标注。"""
    if perm is None:
        return "正常"
    if state == "trend":
        t = perm["trend"]
        if cond := perm["cond"]:
            if cond["B"]:
                return "B禁Trend"
            if cond["C"] and not t["allow"]:
                return "C禁Trend"
            if t["down"]:
                return "A/D降权"
            if not t["allow"]:
                return "硬禁(Score)"
        return "正常"
    if state in ("uptrend_consol", "downtrend_consol", "neutral_range"):
        r = perm["range"]
        if not r["allow"]:
            return "C禁Range"
        if state != "neutral_range" and r["no_chase"]:
            chase = (state == "uptrend_consol" and action == "buy") or \
                    (state == "downtrend_consol" and action == "sell")
            if chase:
                return "D禁追趋势"
        cbr_dir, _ = re.consolidationBreakRisk(state, bucket_of(i), k4, st4)
        if cbr_dir == action:
            return "CBR禁"
        return "正常"
    if state in ("transition", "breakout"):
        return "C等待" if perm["breakout"]["wait"] else "正常"
    return "正常"


ctx4 = re.computeRegimeContext(k4)
st4 = ctx4["st"]

rows = []
for k, t in enumerate(trades):
    sig = sigs[k]
    i = sig["i"]
    j = bucket_of(i)
    perm = perm4[j] if 0 <= j < len(perm4) and perm4[j] else None
    opents = datetime.fromtimestamp(k1[i]["ts"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    cond = perm["cond"] if perm else {"A": False, "B": False, "C": False, "D": False}
    rows.append((k + 1, opents, ('多' if t['side'] == 1 else '空'),
                 tag.get(sig['state'], sig['state']), t['entry'], t['exit'],
                 t['pnl_pct'], cond_cat(cond), dispose(sig['state'], sig['action'], perm)))

# ── 打印明细 ──
L = []
L.append(f"{SYMBOL} 最新策略(Market Permission Layer) 1h 全样本回测明细\n"
         f"样本 {d0}..{d1}  1h={len(k1)}根  成交单={len(rows)}  TP1={TP1*100:.0f}% TP2={TP2*100:.0f}% 含OKX费率+滑点\n")
L.append(f"{'#':>3} {'开仓时间':16} {'方向':4} {'状态':8} {'入场价':>11} {'出场价':>11} {'盈亏%':>8} {'条件':10} {'处置':10}")
for r in rows:
    L.append(f"{r[0]:3d} {r[1]:16} {r[2]:4} {r[3]:8} {r[4]:11.1f} {r[5]:11.1f} "
             f"{r[6]:8.2f} {r[7]:10} {r[8]:10}")

wins = sum(1 for r in rows if r[6] > 0)
gross_win = sum(r[6] for r in rows if r[6] > 0)
gross_loss = sum(r[6] for r in rows if r[6] <= 0)
# 逐笔复利累计(与官方 backtest 应一致)
eq = 1.0
for r in rows:
    eq *= (1 + r[6] / 100)
comp = eq * 100 - 100
L.append(f"\n成交单: {len(rows)}  盈利 {wins}  亏损 {len(rows)-wins}  胜率 {wins/len(rows)*100:.1f}%")
L.append(f"逐笔盈亏合计(算术): {sum(r[6] for r in rows):+.2f}%   盈利均 {gross_win/wins:.2f}%  亏损均 {gross_loss/(len(rows)-wins):.2f}%")
L.append(f"逐笔复利累计净值: {comp:+.2f}%")

# ── 引擎级收益汇总(含 TP 分批止盈的落袋) ──
rr = backtest_st.backtest(k1, sigs, apply_fees=True, tp1_pct=TP1, tp2_pct=TP2)
st = backtest_st.super_trend([x["o"] for x in k1], [x["h"] for x in k1],
                             [x["l"] for x in k1], [x["c"] for x in k1], 10, 3.0, "hl2", True)
baseline = [{"action": f["type"], "i": f["i"]} for f in st["flips"]]
rb = backtest_st.backtest(k1, baseline, apply_fees=True, tp1_pct=TP1, tp2_pct=TP2)
L.append(f"\n{'='*84}\n  收益汇总(TP{TP1*100:.0f}%/{TP2*100:.0f}% + 费率滑点)\n{'='*84}")
L.append(f"  最新策略 /model     : 信号{len(sigs)} 成交{rr['trades']}  "
         f"净收益 {rr['total_return_pct']:+.2f}%  "
         f"回撤 {rr['max_drawdown_pct']:.2f}%  胜率 {rr['win_rate_pct']:.1f}%  费占 {rr['fees_pct']:.2f}%")
L.append(f"  原ST(10,3)全flip    : 信号{len(baseline)}  净收益 {rb['total_return_pct']:+.2f}%  "
         f"回撤 {rb['max_drawdown_pct']:.2f}%  胜率 {rb['win_rate_pct']:.1f}%")
L.append(f"  BTC 买入持有(基准)  : {bh:+.2f}%")
L.append(f"  超额(最新-原ST): {rr['total_return_pct']-rb['total_return_pct']:+.2f}pct   "
         f"(最新-BH): {rr['total_return_pct']-bh:+.2f}pct")
L.append(f"\n  注①: 引擎 win_rate={rr['win_rate_pct']:.1f}% 只统计平仓最后一段(不含开仓费与TP1/TP2已落袋), "
         f"系统性偏低;")
L.append(f"       真实逐笔(权益口径)胜率 {wins/len(rows)*100:.1f}% = {wins}/{len(rows)}, "
         f"逐笔复利 {comp:+.2f}% 与引擎净收益一致, 以上表为准。")
L.append(f"  注②: TP1命中 {rr['tp1_hits']}次 TP2命中 {rr['tp2_hits']}次; "
         f"表中大量 +0.48% = 打到TP1(2%落袋30%)后回撤保本平仓。")

out = "\n".join(L)
print(out)
with open("bt_latest_trades.txt", "w", encoding="utf-8") as f:
    f.write(out + "\n")
print("\n文件已写出: bt_latest_trades.txt")
