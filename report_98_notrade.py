"""还原原始 98 笔信号, 逐笔标注新 Market Permission Layer(取代 No Trade)的决策。
先算真实权限矩阵(perm4), 再关掉总开关还原 98 笔原始信号, 逐笔对比新逻辑下是执行还是拦截。
输出 bt_98_permission_analysis.txt。
"""
import sys, os, bisect
from datetime import datetime, timezone
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
BACKEND = r'd:/个人项目代码/supertrendMain/backend'
sys.path.insert(0, BACKEND)
import regime_engine as re
from analyze_signals import load, instrumented_backtest

SYMBOL = 'BTC-USDT'
TP1, TP2 = 0.02, 0.04

k4 = load(SYMBOL, '4h')
k1 = load(SYMBOL, '1h')
ts4 = [x["ts"] for x in k4]
ctx4 = re.computeRegimeContext(k4)
st4 = ctx4["st"]

# 1) 真实权限矩阵(总开关开): perm4[j] 含 trend/range/breakout 权限 + 评分 + A/B/C/D 条件
re.MARKET_PERMISSION_ENABLED = True
newsigs, perm4, state4 = re.computeRegimeSignalsMTF(k4, k1, return_nt=True)
newset = set((s["i"], s["action"]) for s in newsigs)

# 2) 还原"原始 98 笔": 关掉总开关 + 旧微动/偏置, 得到未加任何权限过滤的信号
re.MARKET_PERMISSION_ENABLED = False
re.STRUCTURE_BIAS = False
re.TREND_MIN_MOVE_ATR = 0.0
re.TREND_MIN_HOLD = 0
sigs = re.computeRegimeSignalsMTF(k4, k1)
print("还原原始信号数:", len(sigs), " | 新逻辑信号数:", len(newsigs))

trades = instrumented_backtest(k1, sigs, apply_fees=True, tp1_pct=TP1, tp2_pct=TP2)
tag = {"trend": "趋势", "breakout": "突破", "neutral_range": "横盘",
       "uptrend_consol": "上涨整理", "downtrend_consol": "下跌整理", "transition": "过渡"}
cond_cat = lambda c: "+".join([x for x in ("A", "B", "C", "D") if c.get(x)]) or "ok"


def new_decision(state, action, j, perm):
    """新逻辑下该笔被拦截时的原因(执行分支在外部以 newset 命中判断)。"""
    t = perm["trend"]; r = perm["range"]; b = perm["breakout"]; cond = perm["cond"]
    if state == "trend":
        if not t["allow"]:
            if cond["B"]:
                return "B弱ADX禁Trend"
            if cond["C"]:
                return "C低波动禁Trend"
            return "TrendScore<60"
        if t["down"]:
            return "A/D降权+锯齿/微动过滤"
        return "趋势锯齿TNK"
    if state in ("neutral_range", "uptrend_consol", "downtrend_consol"):
        if not r["allow"]:
            return "C低波动禁Range"
        if state != "neutral_range" and r["no_chase"]:
            chase = (state == "uptrend_consol" and action == "buy") or \
                    (state == "downtrend_consol" and action == "sell")
            if chase:
                return "D老化禁追趋势"
        cbr_dir, _ = re.consolidationBreakRisk(state, j, k4, st4)
        if cbr_dir == action:
            return "CBR结构风险"
        return "区间噪声"
    if state in ("transition", "breakout"):
        if b["wait"]:
            return "C低波动Breakout等待"
        return "突破噪声"
    return "?"


rows = []
for k, sig in enumerate(sigs):
    i = sig["i"]
    j = bisect.bisect_right(ts4, k1[i]["ts"]) - 1
    if j < 0:
        j = 0
    perm = perm4[j] if 0 <= j < len(perm4) and perm4[j] else None
    present = (i, sig["action"]) in newset
    if present:
        decision = "执行"
        if perm and sig["state"] == "trend" and perm["trend"]["down"]:
            reason = "A/D降权执行"
        elif perm and sig["state"] in ("uptrend_consol", "downtrend_consol") and perm["range"]["no_chase"]:
            reason = "D老化下仍执行"
        elif perm and sig["state"] in ("uptrend_consol", "downtrend_consol") and \
                re.consolidationBreakRisk(sig["state"], j, k4, st4)[0] == sig["action"]:
            reason = "CBR下仍执行"
        else:
            reason = "正常执行"
    else:
        decision = "拦截"
        reason = new_decision(sig["state"], sig["action"], j, perm) if perm else "?"
    t = trades[k]
    opents = datetime.fromtimestamp(k1[i]["ts"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    cond = perm["cond"] if perm else {"A": False, "B": False, "C": False, "D": False}
    rows.append((k + 1, opents, ('多' if sig['action'] == 'buy' else '空'),
                 tag.get(sig['state'], sig['state']), t['entry'], t['exit'],
                 t['pnl_pct'], cond_cat(cond), decision, reason))

with open("bt_98_permission_analysis.txt", "w", encoding="utf-8") as f:
    f.write(f"样本: {SYMBOL} 2026-03-15..2026-09-19  1h={len(k1)}根  原始信号={len(sigs)}笔\n")
    f.write("新 Market Permission Layer(取代 No Trade Zone):\n")
    f.write("  A概率接近=Trend降权(不禁)  B弱ADX=禁Trend  C低波动=禁Trend+Breakout等待  "
            "D老化=Trend降权+Range禁追趋势\n")
    f.write("  结构风险: CBR(整理逆势单查高低点, 防轧空/轧多)  TNF(趋势近10根1h翻转簇=锯齿, 不误杀单笔起点)\n")
    f.write("  列: 条件=该桶A/B/C/D触发(ok=无); 新决策=执行/拦截; 新原因=新逻辑下处置\n\n")
    f.write(f"{'#':>3} {'开仓时间':16} {'方向':4} {'状态':8} {'入场价':>11} {'出场价':>11} "
            f"{'盈亏%':>8} {'条件':12} {'新决策':5} {'新原因':14}\n")
    for r in rows:
        f.write(f"{r[0]:3d} {r[1]:16} {r[2]:4} {r[3]:8} {r[4]:11.1f} {r[5]:11.1f} "
                f"{r[6]:8.2f} {r[7]:12} {r[8]:5} {r[9]:14}\n")

exc = [r for r in rows if r[8] == "执行"]
blk = [r for r in rows if r[8] == "拦截"]
print(f"\n新逻辑下: 执行={len(exc)} / 拦截={len(blk)}  (共 {len(rows)})")
print("  拦截按原因:", dict(Counter(r[9] for r in blk)))
print(f"  拦截的 {len(blk)} 笔原始盈亏合计: {sum(r[6] for r in blk):+.2f}%")
print(f"  执行的 {len(exc)} 笔原始盈亏合计: {sum(r[6] for r in exc):+.2f}%")
# 之前被 A/D 命中的趋势大盈利是否保住
ad_trend = [r for r in rows if ('A' in r[7] or 'D' in r[7]) and r[3] == "趋势"]
ad_exec = [r for r in ad_trend if r[8] == "执行"]
print(f"\n曾被旧 A/D 命中的趋势笔: {len(ad_trend)}  其中新逻辑执行: {len(ad_exec)} "
      f"(原会被误杀 {len(ad_trend)-len(ad_exec)} 笔)")
print(f"  这些 A/D 趋势笔原始盈亏合计: {sum(r[6] for r in ad_trend):+.2f}%")
print("文件已写出: bt_98_permission_analysis.txt")
