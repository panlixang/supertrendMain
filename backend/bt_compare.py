"""形态识别页原始 SuperTrend 信号：A/B/C 三种 4h 过滤策略收益对比（本地只读回测）。

数据：本地 candle_data.db，BTC-USDT / 1h（约半年）。
信号：原始 SuperTrend（periods=10, multiplier=3.0, change_atr=True）的 trend 翻转。
出场：完全复用 position.py 规则 —— TP1 触 1.5% 平 70% 并止损移至开仓价（保本），
      剩余 30% 等下一根反向 SuperTrend 信号收盘平掉，或途中触及跟踪止损（SuperTrend 轨道）。
      三种策略用同一套出场规则，唯一变量是「4h 形态是否放行该信号」。

  A) 无 4h 过滤        ：所有信号都下单
  B) 4h 趋势过滤(当前) ：仅 pdir == 信号方向 才下单；pdir==0 / None / 反向 都不下
  C) 4h 过滤但无趋势不下滤：pdir==0 也正常下单；仅 pdir 反向 / None 不下
"""
from __future__ import annotations
import bisect
import datetime as dt
from typing import Optional

import candle_store
from indicators import super_trend
from pattern_recog import recognize as recognize_pattern


SYM, BASE_TF, H4_TF = "BTC-USDT", "1h", "4h"
NOTIONAL = 10_000.0          # 每笔名义价值（USDT），用于换算盈亏
TP1_PCT = 1.5                # 第一档止盈触发幅度（价格 %）
TP1_RATIO = 0.70             # 第一档平掉比例
SL_PCT_FALLBACK = 2.0        # 超趋线无效时退回的固定止损 %
FEE_PCT = 0.05               # OKX 合约 taker 手续费（单边 %），往返约 0.1%
FEE = FEE_PCT / 100


def main():
    base = candle_store.load_candles(SYM, BASE_TF, 4500)
    h4 = candle_store.load_candles(SYM, H4_TF, 1200)
    if not base:
        print("no base candles"); return

    bcandles = [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol}
                for c in base]
    opens = [c["o"] for c in bcandles]
    highs = [c["h"] for c in bcandles]
    lows = [c["l"] for c in bcandles]
    closes = [c["c"] for c in bcandles]
    tss = [c["ts"] for c in bcandles]

    st = super_trend(opens, highs, lows, closes, periods=10, multiplier=3.0, change_atr=True)
    up_plot, dn_plot = st["up_plot"], st["dn_plot"]

    # 4h 形态方向
    h4dict = [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c} for c in h4]
    pat = recognize_pattern(h4dict)["pattern"]
    pmap = {p["ts"]: p for p in pat}
    pts = [p["ts"] for p in pat]

    def dir_at(ts: int) -> Optional[int]:
        idx = bisect.bisect_right(pts, ts) - 1
        return pmap[pts[idx]]["dir"] if idx >= 0 else None

    # 构造信号序列（含 4h 方向）
    signals = []
    for f in st["flips"]:
        i = f["i"]
        if i >= len(bcandles):
            continue
        sig_dir = 1 if f["type"] == "buy" else -1
        pdir = dir_at(tss[i])
        c = bcandles[i]
        signals.append({"i": i, "ts": c["ts"], "type": f["type"], "dir": sig_dir,
                        "price": c["c"], "pdir": pdir})

    # 三种策略的放行判定
    def allowed(strategy: str, s: dict) -> bool:
        p = s["pdir"]
        if strategy == "A":
            return True
        if strategy == "B":
            return isinstance(p, int) and p == s["dir"]
        if strategy == "C":
            return isinstance(p, int) and (p == s["dir"] or p == 0)
        return False

    print(f"数据范围: {fmt(ts(tss[0]))} ~ {fmt(ts(tss[-1]))}  "
          f"1h 根数={len(bcandles)}  信号总数={len(signals)}")
    # 方向分布
    from collections import Counter
    pc = Counter((s["pdir"] if s["pdir"] is not None else "None") for s in signals)
    print(f"4h 方向分布: " + ", ".join(f"{k}={v}" for k, v in sorted(pc.items(),
          key=lambda x: str(x[0]))) )

    # 回测核心
    def backtest(allow_set, flip_idx):
        """allow_set: 该策略放行的信号子集；flip_idx: 所有(任意)SuperTrend翻转根索引，
        剩余 30% 仓位在该信号之后出现的下一根翻转(必为反向)收盘时平掉。"""
        trades = []
        # 按时间顺序遍历放行的信号，下一个 flip（任意）即剩余仓位平仓触发
        for s in allow_set:
            i = s["i"]
            long = s["type"] == "buy"
            entry = closes[i]
            # 初始止损 = 超趋线轨道
            if long:
                stp0 = dn_plot[i]
                if stp0 is None or stp0 >= entry:
                    stp0 = entry * (1 - SL_PCT_FALLBACK / 100)
            else:
                stp0 = up_plot[i]
                if stp0 is None or stp0 <= entry:
                    stp0 = entry * (1 + SL_PCT_FALLBACK / 100)
            stop = stp0
            tp1_price = entry * (1 + TP1_PCT / 100) if long else entry * (1 - TP1_PCT / 100)
            tp1_done = False
            coins = NOTIONAL / entry
            realized = -entry * coins * FEE          # 开仓手续费
            exit_reason = "末根平仓"
            exit_price = closes[-1]
            closed = False

            for j in range(i + 1, len(bcandles)):
                # 跟踪止损：随超趋线向有利方向移动
                if long:
                    nl = dn_plot[j]
                    if nl is not None and nl > stop:
                        stop = nl
                    # 先查止损（用本根 low）
                    if lows[j] <= stop:
                        px = stop
                        if tp1_done:
                            realized += (px - entry) * coins * (1 - TP1_RATIO)
                            realized -= px * coins * (1 - TP1_RATIO) * FEE
                        else:
                            realized += (px - entry) * coins
                            realized -= px * coins * FEE
                        exit_reason = "止损"; exit_price = px; closed = True; break
                    # 查 TP1（用本根 high）
                    if not tp1_done and highs[j] >= tp1_price:
                        realized += (tp1_price - entry) * coins * TP1_RATIO
                        realized -= tp1_price * coins * TP1_RATIO * FEE
                        tp1_done = True
                        stop = entry  # 保本
                else:
                    nl = up_plot[j]
                    if nl is not None and nl < stop:
                        stop = nl
                    if highs[j] >= stop:
                        px = stop
                        if tp1_done:
                            realized += (entry - px) * coins * (1 - TP1_RATIO)
                            realized -= px * coins * (1 - TP1_RATIO) * FEE
                        else:
                            realized += (entry - px) * coins
                            realized -= px * coins * FEE
                        exit_reason = "止损"; exit_price = px; closed = True; break
                    if not tp1_done and lows[j] <= tp1_price:
                        realized += (entry - tp1_price) * coins * TP1_RATIO
                        realized -= tp1_price * coins * TP1_RATIO * FEE
                        tp1_done = True
                        stop = entry

                # 反向信号出现 → 平掉剩余
                if j in flip_idx:
                    # 由于 flips 交替，下一个 flip 必为反向
                    if tp1_done:
                        realized += (closes[j] - entry) * coins * (1 - TP1_RATIO)
                        realized -= closes[j] * coins * (1 - TP1_RATIO) * FEE
                    else:
                        realized += (closes[j] - entry) * coins
                        realized -= closes[j] * coins * FEE
                    exit_reason = "反向信号"; exit_price = closes[j]; closed = True; break

            # 数据末尾仍未平仓 → 按末根收盘平掉剩余
            if not closed:
                if tp1_done:
                    realized += (closes[-1] - entry) * coins * (1 - TP1_RATIO)
                    realized -= closes[-1] * coins * (1 - TP1_RATIO) * FEE
                else:
                    realized += (closes[-1] - entry) * coins
                    realized -= closes[-1] * coins * FEE

            trades.append({"entry": entry, "long": long, "realized": realized,
                           "tp1": tp1_done, "reason": exit_reason,
                           "ret_pct": realized / NOTIONAL * 100})
        return trades

    # 预先建好「所有 SuperTrend 翻转根索引集合」：用于判断某根 j 是否触发反向平仓。
    all_flip_idx = {f["i"] for f in st["flips"] if f["i"] < len(bcandles)}

    results = {}
    for strat in ["A", "B", "C"]:
        allow_set = [s for s in signals if allowed(strat, s)]
        tr = backtest(allow_set, all_flip_idx)
        results[strat] = tr

    # 汇总打印
    hdr = f"{'策略':<28}{'笔数':>6}{'盈利笔':>7}{'胜率':>8}{'总收益USDT':>12}{'收益率%':>9}{'均笔%':>8}{'最大单笔亏%':>12}"
    print("\n" + hdr)
    print("-" * len(hdr))
    for strat, label in [("A", "A) 无 4h 过滤"),
                         ("B", "B) 4h 趋势过滤(当前)"),
                         ("C", "C) 4h过滤·无趋势也下单")]:
        tr = results[strat]
        n = len(tr)
        wins = sum(1 for t in tr if t["realized"] > 0)
        tot = sum(t["realized"] for t in tr)
        ret_pct = tot / NOTIONAL * 100 if n else 0
        avg = (tot / n / NOTIONAL * 100) if n else 0
        worst = min((t["ret_pct"] for t in tr), default=0)
        wr = (wins / n * 100) if n else 0
        print(f"{label:<28}{n:>6}{wins:>7}{wr:>7.1f}%{tot:>12.2f}{ret_pct:>9.2f}{avg:>8.3f}{worst:>12.2f}")

    # 放行/拦截统计
    print("\n放行 / 拦截统计（信号总数=%d）：" % len(signals))
    for strat, label in [("A", "A) 无过滤"), ("B", "B) 当前过滤"), ("C", "C) 无趋势也下单")]:
        allow = sum(1 for s in signals if allowed(strat, s))
        block = len(signals) - allow
        print(f"  {label:<22} 放行={allow:>4}  拦截={block:>4}")
    # C 相比 B 多放行的 dir==0 笔数
    extra0 = sum(1 for s in signals if s["pdir"] == 0 and allowed("C", s) and not allowed("B", s))
    print(f"  → C 比 B 多放行(因 dir==0 无趋势): {extra0} 笔")

    # 出场原因分布（B）
    print("\nB) 出场原因分布:")
    rc = Counter(t["reason"] for t in results["B"])
    for k, v in rc.most_common():
        print(f"  {k:<10}{v}")
    print("\nC) 出场原因分布:")
    rc = Counter(t["reason"] for t in results["C"])
    for k, v in rc.most_common():
        print(f"  {k:<10}{v}")


def ts(ms):
    return dt.datetime.utcfromtimestamp(ms / 1000)


def fmt(d):
    return d.strftime("%Y-%m-%d")


if __name__ == "__main__":
    main()
