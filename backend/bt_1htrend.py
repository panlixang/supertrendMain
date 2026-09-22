"""多窗口回测：A/B 两种过滤策略收益对比（本地只读，OKX 公共行情）。

信号/出场规则与 bt_2025.py 完全一致，唯一变化是「过滤条件」：

  A) 无 4h 过滤                       ：所有 1h 信号都下单
  B) 4h 趋势过滤(无明显趋势不过滤)   ：只拦 4h 明确反向 / 数据不足；4h 无趋势也下单

已移除 C) 1h 无明显趋势过滤。依据：在 BTC/ETH × 2025全年/2026H1 四个窗口验证，
该滤镜触发极少（占总信号 1.5%~9%）、最大回撤一次都没降过、收益基本持平或略降
→ 判定为无用叠加层，故删除。

backtest() 复用 position.py 规则：TP1 触 1.5% 平 70%+保本，剩余 30% 等下一根
反向 SuperTrend 翻转（即 SuperTrend 轨道止损）平掉；固定 $10k/笔、无复利、扣费。
"""
from __future__ import annotations
import bisect
import datetime as dt
import sys
from collections import Counter
from typing import Optional

import history
from indicators import super_trend
from pattern_recog import recognize as recognize_pattern

SYM = sys.argv[1] if len(sys.argv) > 1 else "BTC-USDT"
NOTIONAL = 10_000.0
TP1_PCT = 1.5
TP1_RATIO = 0.70
SL_PCT_FALLBACK = 2.0
FEE = 0.05 / 100

START = int(dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
END = int(dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)


def load():
    print("拉取 1h 历史(覆盖 2025 全年 + 预热)…")
    base = history.fetch_candles("1h", limit=15600, symbol=SYM)
    print("拉取 4h 历史…")
    h4 = history.fetch_candles("4h", limit=4200, symbol=SYM)
    if not base:
        print("fetch failed"); return None, None
    print(f"抓取完成: 1h {len(base)} 根 ({fmt(base[0].ts)}~{fmt(base[-1].ts)}), 4h {len(h4)} 根")
    return base, h4


def build_signals(base, h4, start=START, end=END):
    opens = [c.o for c in base]
    highs = [c.h for c in base]
    lows = [c.l for c in base]
    closes = [c.c for c in base]
    tss = [c.ts for c in base]
    st = super_trend(opens, highs, lows, closes, periods=10, multiplier=3.0, change_atr=True)
    up_plot, dn_plot = st["up_plot"], st["dn_plot"]

    # 4h 形态方向
    h4dict = [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c} for c in h4]
    pat = recognize_pattern(h4dict)["pattern"]
    pmap = {p["ts"]: p for p in pat}
    pts = [p["ts"] for p in pat]

    def dir_at(ts):
        idx = bisect.bisect_right(pts, ts) - 1
        return pmap[pts[idx]]["dir"] if idx >= 0 else None

    sigs = []
    for f in st["flips"]:
        i = f["i"]
        if i >= len(closes):
            continue
        if not (start <= tss[i] < end):
            continue
        sigs.append({"i": i, "ts": tss[i], "type": f["type"],
                     "dir": 1 if f["type"] == "buy" else -1,
                     "price": closes[i], "pdir": dir_at(tss[i])})
    flip_idx = {f["i"] for f in st["flips"] if f["i"] < len(closes)}
    return sigs, opens, highs, lows, closes, up_plot, dn_plot, flip_idx


def backtest(allow_set, opens, highs, lows, closes, up_plot, dn_plot, flip_idx):
    trades = []
    for s in allow_set:
        i = s["i"]
        long = s["type"] == "buy"
        entry = closes[i]
        stp0 = dn_plot[i] if long else up_plot[i]
        if stp0 is None or (long and stp0 >= entry) or (not long and stp0 <= entry):
            stp0 = entry * (1 - SL_PCT_FALLBACK / 100) if long else entry * (1 + SL_PCT_FALLBACK / 100)
        stop = stp0
        tp1p = entry * (1 + TP1_PCT / 100) if long else entry * (1 - TP1_PCT / 100)
        tp1 = False
        coins = NOTIONAL / entry
        pnl = -entry * coins * FEE
        closed = False
        ex = None
        for j in range(i + 1, len(closes)):
            if long:
                nl = dn_plot[j]
                if nl is not None and nl > stop:
                    stop = nl
                if lows[j] <= stop:
                    px = stop
                    if tp1:
                        pnl += (px - entry) * coins * (1 - TP1_RATIO) - px * coins * (1 - TP1_RATIO) * FEE
                    else:
                        pnl += (px - entry) * coins - px * coins * FEE
                    ex = px; closed = True; break
                if not tp1 and highs[j] >= tp1p:
                    pnl += (tp1p - entry) * coins * TP1_RATIO - tp1p * coins * TP1_RATIO * FEE
                    tp1 = True; stop = entry
            else:
                nl = up_plot[j]
                if nl is not None and nl < stop:
                    stop = nl
                if highs[j] >= stop:
                    px = stop
                    if tp1:
                        pnl += (entry - px) * coins * (1 - TP1_RATIO) - px * coins * (1 - TP1_RATIO) * FEE
                    else:
                        pnl += (entry - px) * coins - px * coins * FEE
                    ex = px; closed = True; break
                if not tp1 and lows[j] <= tp1p:
                    pnl += (entry - tp1p) * coins * TP1_RATIO - tp1p * coins * TP1_RATIO * FEE
                    tp1 = True; stop = entry
            if j in flip_idx:
                if tp1:
                    pnl += (closes[j] - entry) * coins * (1 - TP1_RATIO) - closes[j] * coins * (1 - TP1_RATIO) * FEE
                else:
                    pnl += (closes[j] - entry) * coins - closes[j] * coins * FEE
                ex = closes[j]; closed = True; break
        if not closed:
            if tp1:
                pnl += (closes[-1] - entry) * coins * (1 - TP1_RATIO) - closes[-1] * coins * (1 - TP1_RATIO) * FEE
            else:
                pnl += (closes[-1] - entry) * coins - closes[-1] * coins * FEE
            ex = closes[-1]
        trades.append({"entry": entry, "exit": ex, "pnl": pnl, "tp1": tp1,
                       "ret_pct": pnl / NOTIONAL * 100})
    return trades


def allowed(strat, s):
    p = s["pdir"]
    if strat == "A":
        return True
    if strat == "B":
        # 4h 趋势过滤：只拦明确反向 / 数据不足；无趋势也下单
        return isinstance(p, int) and (p == s["dir"] or p == 0)
    return False


def metrics(tr):
    """逐笔已按时间顺序排列。返回汇总 + 权益曲线统计。"""
    n = len(tr)
    wins = sum(1 for t in tr if t["pnl"] > 0)
    tot = sum(t["pnl"] for t in tr)
    worst = min((t["ret_pct"] for t in tr), default=0)
    # 权益曲线（按成交顺序累加）
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    streak = 0
    worst_streak = 0
    for t in tr:
        eq += t["pnl"]
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / NOTIONAL * 100)
        if t["pnl"] > 0:
            streak = 0
        else:
            streak += 1
            worst_streak = max(worst_streak, streak)
    return dict(n=n, wins=wins, wr=wins / n * 100 if n else 0,
                tot=tot, ret=tot / NOTIONAL * 100,
                avg=tot / n / NOTIONAL * 100 if n else 0,
                worst=worst, max_dd=max_dd, worst_streak=worst_streak)


def run(base, h4, start=START, end=END, wlabel="2025 全年"):
    sigs, opens, highs, lows, closes, up_plot, dn_plot, flip_idx = build_signals(base, h4, start, end)
    print(f"\n===== {wlabel} =====")
    print(f"1h 根数={len(base)}  窗口信号数={len(sigs)}")
    pc = Counter((s["pdir"] if s["pdir"] is not None else "None") for s in sigs)
    print("4h 方向分布: " + ", ".join(f"{k}={v}" for k, v in sorted(pc.items(),
          key=lambda x: str(x[0]))))
    results = {st_: backtest([s for s in sigs if allowed(st_, s)], opens, highs, lows,
                             closes, up_plot, dn_plot, flip_idx)
               for st_ in ["A", "B"]}
    m = {k: metrics(v) for k, v in results.items()}

    hdr = (f"{'策略':<32}{'笔数':>6}{'胜率':>8}{'收益率%':>9}"
           f"{'均笔%':>8}{'最大单笔亏%':>12}{'最大回撤%':>10}{'最长连亏':>9}")
    print("\n" + hdr)
    print("-" * len(hdr))
    for st_, label in [("A", "A) 无 4h 过滤"),
                       ("B", "B) 4h过滤·无趋势也下单")]:
        x = m[st_]
        print(f"{label:<32}{x['n']:>6}{x['wr']:>7.1f}%{x['ret']:>9.2f}"
              f"{x['avg']:>8.3f}{x['worst']:>12.2f}{x['max_dd']:>10.2f}{x['worst_streak']:>9}")

    print("\n放行 / 拦截统计（信号总数=%d）：" % len(sigs))
    for st_, label in [("A", "A) 无过滤"), ("B", "B) 4h反向拦")]:
        allow = sum(1 for s in sigs if allowed(st_, s))
        print(f"  {label:<22} 放行={allow:>4}  拦截={len(sigs)-allow:>4}")
    return results


def fmt(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


if __name__ == "__main__":
    b, h = load()
    if b:
        run(b, h, START, END, "2025 全年")
        h1s = int(dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
        h1e = int(dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
        run(b, h, h1s, h1e, "2026 H1")

