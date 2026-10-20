# -*- coding: utf-8 -*-
"""震荡反复磨损归因分析（BTC）

复用 backtest.engine.run_core 跑线上真实参数，拿到逐笔成交，再给每笔标注
「开仓时的 ER / ATR 波动率」，回答四个问题：
  1) 亏损到底来自哪些平仓 reason（震荡假突破的"极端止损"？）
  2) 开仓时 ER 处于什么水平（是不是在震荡区反复开仓）？
  3) 剔除少数大亏笔后收益变多少（磨损的集中度）？
  4) 震荡段(IS) vs 趋势段(OOS) 的结构差异

运行：python study_churn.py
"""
import os, sys, json, sqlite3
from collections import defaultdict, Counter
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(BACKEND, "backtest"))

from engine import run_core, _extract          # noqa: E402
from regime import efficiency_ratio, atr_volatility, range_bound  # noqa: E402

CFG = os.path.join(BACKEND, "backtest", "BTC", "btc_config.json")
TF = "1h"

# ── 先探测 db 里 BTC 的 symbol 名与可用周期 ──
con = sqlite3.connect(os.path.join(BACKEND, "candle_data.db"))
rows = con.execute(
    "SELECT symbol, tf, COUNT(*) FROM candles WHERE symbol LIKE '%BTC%' "
    "GROUP BY symbol, tf ORDER BY symbol, tf").fetchall()
con.close()
print("[db] BTC 数据:", rows)
btc_syms = [s for (s, tf, n) in rows if s.startswith("BTC")]
if not btc_syms:
    raise SystemExit("db 里没有 BTC 数据")
DB_SYMBOL = "BTC-USDT" if "BTC-USDT" in btc_syms else btc_syms[0]
print(f"[db] 使用 db_symbol={DB_SYMBOL}")

with open(CFG, encoding="utf-8") as f:
    C = json.load(f)
sc, pr, er = _extract(C)
print("[run] 回测 BTC 线上真实参数（全量）...")
r = run_core(DB_SYMBOL, sc, pr, er, tf=TF, candles=None)
klines, trades, INIT = r["klines"], r["trades"], r["INIT"]
A, I, O = r["all"], r["is"], r["oos"]
print(f"[run] K线 {r['n']} 根 | 成交记录 {len(trades)} 条 | INIT={INIT:.0f}U")
print(f"[总览] 全 {A['ret_pct']:+.2f}% PF {A['pf']:.2f} 胜率 {A['winrate']:.1f}% "
      f"| IS {I['ret_pct']:+.2f}% (n{I['n']}) | OOS {O['ret_pct']:+.2f}% (n{O['n']}) "
      f"| DD {r['max_dd']:.2f}%")

# ── 给每笔成交标注开仓时的行情状态 ──
ts2i = {c["ts"]: i for i, c in enumerate(klines)}
for t in trades:
    i = ts2i.get(t["entry_ts"])
    if i is None or i < 60:
        t["er"] = t["atr_r"] = t["rng"] = None
        continue
    w = klines[:i + 1]                 # 与回测口径一致：截至信号根
    t["er"] = efficiency_ratio(w)      # 0~1，越低越震荡
    t["atr_r"] = atr_volatility(w)     # <1 萎缩 / >1 扩张
    t["rng"] = range_bound(w)["range_pct"]

IS_CUT = sorted(t["entry_ts"] for t in trades)
IS_CUT = IS_CUT[int(len(IS_CUT) * 0.7)] if IS_CUT else 0


def seg_of(t):
    return "IS震荡" if (t["entry_ts"] or 0) < IS_CUT else "OOS趋势"


def bucket_er(er):
    if er is None:
        return "未知"
    if er < 0.12:
        return "震荡<0.12"
    if er < 0.15:
        return "边缘.12-.15"
    if er < 0.30:
        return "弱趋.15-.30"
    return "趋势>.30"


def grp(rows, keyfn):
    d = defaultdict(lambda: [0, 0.0, 0])   # n, pnl, win
    for t in rows:
        b = d[keyfn(t)]
        b[0] += 1; b[1] += t["pnl"]; b[2] += 1 if t["pnl"] > 0 else 0
    return d


print("\n===== 1) 按平仓原因（磨损来自哪）=====")
for k, (n, p, w) in sorted(grp(trades, lambda t: t["reason"]).items(),
                           key=lambda x: -x[1][1]):
    print(f"  {k:10s} {n:4d} 笔  累计 {p:+9.2f}U  均 {p / n:+7.2f}U  胜率 {w / n * 100:3.0f}%")

print("\n===== 2) 开仓时 ER 档位 × 盈亏 =====")
d = grp(trades, lambda t: bucket_er(t["er"]))
for k in ["震荡<0.12", "边缘.12-.15", "弱趋.15-.30", "趋势>.30", "未知"]:
    if k in d:
        n, p, w = d[k]
        print(f"  {k:12s} {n:4d} 笔  累计 {p:+9.2f}U  胜率 {w / n * 100:3.0f}%")

print("\n===== 3) 磨损集中度（少数大亏笔吃多少）=====")
tot = sum(t["pnl"] for t in trades)
print(f"  全部累计 {tot:+.2f}U ({tot / INIT * 100:+.2f}%)")
for dn in [5, 10, 20, 30]:
    drop = sum(t["pnl"] for t in sorted(trades, key=lambda t: t["pnl"])[:dn])
    print(f"  剔除最亏 {dn:2d} 笔(共 {drop:+.2f}U) -> 剩 {(tot - drop) / INIT * 100:+7.2f}%")
print("  最亏 8 笔:")
for t in sorted(trades, key=lambda t: t["pnl"])[:8]:
    print(f"    {t['reason']:6s} {t['side']:5s} pnl={t['pnl']:+8.2f}U  开仓ER={t['er']} atr_r={t['atr_r']}")

print("\n===== 4) 亏损<-5U 的笔，开仓 ER 画像 =====")
big = [t for t in trades if t["pnl"] < -5]
print(f"  共 {len(big)} 笔, 累计 {sum(t['pnl'] for t in big):+.2f}U")
ers = [t["er"] for t in big if t["er"] is not None]
if ers:
    import statistics
    print(f"  开仓ER 均值 {statistics.mean(ers):.3f} 中位 {statistics.median(ers):.3f} "
          f"[{min(ers):.3f}, {max(ers):.3f}]")
    print("  ER分布:", dict(Counter(bucket_er(t["er"]) for t in big)))

print("\n===== 5) 震荡段(IS) vs 趋势段(OOS) 结构 =====")
for seg in ["IS震荡", "OOS趋势"]:
    rows = [t for t in trades if seg_of(t) == seg]
    p = sum(t["pnl"] for t in rows)
    print(f"  [{seg}] {len(rows)} 笔 累计 {p:+.2f}U ({p / INIT * 100:+.2f}%)")
    for k, (n, pp, w) in sorted(grp(rows, lambda t: t["reason"]).items(),
                                key=lambda x: -x[1][1])[:5]:
        print(f"      {k:10s} {n:4d} 笔  {pp:+8.2f}U")

print("\n===== 6) 若 ER<0.15 一律不开仓，会怎样 =====")
kept = [t for t in trades if (t["er"] is None or t["er"] >= 0.15)]
dropped = [t for t in trades if t["er"] is not None and t["er"] < 0.15]
kp = sum(t["pnl"] for t in kept)
dp = sum(t["pnl"] for t in dropped)
print(f"  保留 ER>=0.15: {len(kept)} 笔 {kp:+.2f}U ({kp / INIT * 100:+.2f}%)")
print(f"  砍掉 ER<0.15 : {len(dropped)} 笔 {dp:+.2f}U ({dp / INIT * 100:+.2f}%)  <- 这部分就是磨损")
