"""2025 全年回测：A/B/C 三种 4h 过滤策略收益对比（本地只读，OKX 公共行情）。

与 bt_compare.py 同套信号与出场规则，仅数据窗口改为 2025-01-01 ~ 2025-12-31。
  A) 无 4h 过滤
  B) 4h 趋势过滤(当前)：仅 pdir==信号方向 下单
  C) 4h 过滤·无趋势也下单：pdir==0 也下单，仅 pdir 反向/None 不下

backtest() 为模块级函数，返回逐笔明细；run() 与任何校验脚本共用同一函数，
避免「两份实现」导致的不一致。
"""
from __future__ import annotations
import bisect
import datetime as dt
from typing import Optional

import history
from indicators import super_trend
from pattern_recog import recognize as recognize_pattern

SYM = "BTC-USDT"
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
    h4dict = [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c} for c in h4]
    pat = recognize_pattern(h4dict)["pattern"]
    pmap = {p["ts"]: p for p in pat}; pts = [p["ts"] for p in pat]

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
    """与 position.py 一致：TP1 触 1.5% 平 70%+保本，剩余 30% 等下一根反向翻转
    （即 SuperTrend 轨道止损）平掉。返回逐笔明细。"""
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
        return isinstance(p, int) and p == s["dir"]
    if strat == "C":
        return isinstance(p, int) and (p == s["dir"] or p == 0)
    return False


def run(base, h4):
    sigs, opens, highs, lows, closes, up_plot, dn_plot, flip_idx = build_signals(base, h4)
    print(f"1h 根数={len(base)}  2025 窗口信号数={len(sigs)}")
    from collections import Counter
    pc = Counter((s["pdir"] if s["pdir"] is not None else "None") for s in sigs)
    print("4h 方向分布: " + ", ".join(f"{k}={v}" for k, v in sorted(pc.items(),
          key=lambda x: str(x[0]))))

    results = {st_: backtest([s for s in sigs if allowed(st_, s)], opens, highs, lows,
                             closes, up_plot, dn_plot, flip_idx)
               for st_ in ["A", "B", "C"]}

    hdr = f"{'策略':<28}{'笔数':>6}{'盈利笔':>7}{'胜率':>8}{'总收益USDT':>12}{'收益率%':>9}{'均笔%':>8}{'最大单笔亏%':>12}"
    print("\n" + hdr)
    print("-" * len(hdr))
    for st_, label in [("A", "A) 无 4h 过滤"),
                       ("B", "B) 4h 趋势过滤(当前)"),
                       ("C", "C) 4h过滤·无趋势也下单")]:
        tr = results[st_]
        n = len(tr)
        wins = sum(1 for t in tr if t["pnl"] > 0)
        tot = sum(t["pnl"] for t in tr)
        wr = wins / n * 100 if n else 0
        avg = tot / n / NOTIONAL * 100 if n else 0
        worst = min((t["ret_pct"] for t in tr), default=0)
        print(f"{label:<28}{n:>6}{wins:>7}{wr:>7.1f}%{tot:>12.2f}{tot/NOTIONAL*100:>9.2f}{avg:>8.3f}{worst:>12.2f}")
    return results


def fmt(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


if __name__ == "__main__":
    b, h = load()
    if b:
        run(b, h)
