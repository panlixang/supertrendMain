"""4h 策略分年回测，严格对齐实盘配置（与 position.py 语义一致）：

- 信号：SuperTrend(10, 3.0, change_atr=True) 翻转，翻转根收盘开仓
- 初始止损：sl_mode=pct → 开仓价固定 ±2%（真实硬止损，非超趋线轨道）
- 跟踪：trail_with_st=true，独立于 sl_mode，止损跟随"当前趋势的轨道线"只朝有利方向移动
        多头跟 up_plot（支撑线），空头跟 dn_plot（阻力线）
- TP1：浮盈 +2.0% 平 50%，随后 move_sl_to_entry 把止损移到开仓价（保本）
- 剩余：下一根反向翻转收盘平仓，或途中止损
- 手续费：单边 0.05%（taker），开平各计一次

输出：分年（2025 / 2026 / 其他）笔数、胜率、收益%、profit_factor、最大回撤。
"""
from __future__ import annotations
import asyncio
import datetime as dt
from collections import Counter, defaultdict

from history import fetch_candles
from indicators import super_trend

BASE_TF = "4h"
LIMIT = 6000
NOTIONAL = 10_000.0      # 每笔名义价值，收益% = 累计盈亏 / NOTIONAL（≈1X 名义）
TP1_PCT = 2.0            # TP1 触发幅度 %
TP1_RATIO = 0.50         # TP1 平掉比例
SL_PCT = 2.0             # sl_mode=pct 的固定止损 %
FEE = 0.05 / 100         # 单边 taker


def y_of(ms: int) -> int:
    return dt.datetime.utcfromtimestamp(ms / 1000).year


def fmt(ms: int) -> str:
    return dt.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d")


def stat(trades: list[dict]) -> None:
    if not trades:
        print("   无交易"); return
    rs = [t["realized"] for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    tot = sum(rs)
    pf = sum(wins) / (-sum(losses)) if losses and sum(losses) != 0 else float("inf")
    # 年内权益曲线与最大回撤
    eq, cur = [], 0.0
    for r in rs:
        cur += r
        eq.append(NOTIONAL + cur)
    peak, mx = eq[0], eq[0]
    mdd = 0.0
    for v in eq:
        mx = max(mx, v)
        mdd = min(mdd, (v - mx) / mx)
    print(f"   笔数={len(rs):3d}  胜率={len(wins)/len(rs)*100:5.1f}%  "
          f"收益={tot/NOTIONAL*100:+8.1f}%  均笔={tot/len(rs):+8.1f}U  "
          f"pf={pf:5.2f}  最大回撤={mdd*100:6.1f}%")


async def main():
    import sys
    targets = sys.argv[1:] or ["ETH-USDT-SWAP", "ETH-USDT"]
    # 止损模式：pct=开仓价固定2%(真实硬止损) | st=超趋线初始+跟随 | st_notrail=超趋线初始但不跟随
    mode = "pct"
    if len(sys.argv) > 2 and sys.argv[2] in ("pct", "st", "st_notrail"):
        mode = sys.argv[2]
        targets = sys.argv[1:2]
    loop = asyncio.get_event_loop()
    raw, sym = None, None
    for s in targets:
        try:
            raw = await loop.run_in_executor(None, fetch_candles, BASE_TF, LIMIT, s)
        except Exception as e:
            print(f"   {s} 取数异常: {e}")
            raw = None
        if raw:
            sym = s
            break
    if not raw:
        print("no data"); return

    bc = [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol} for c in raw]
    opens = [x["o"] for x in bc]
    highs = [x["h"] for x in bc]
    lows = [x["l"] for x in bc]
    closes = [x["c"] for x in bc]
    tss = [x["ts"] for x in bc]

    st = super_trend(opens, highs, lows, closes, periods=10, multiplier=3.0, change_atr=True)
    up_plot, dn_plot = st["up_plot"], st["dn_plot"]
    signals = [f for f in st["flips"] if f["i"] < len(bc)]
    flip_idx = {f["i"] for f in signals}

    n_up = sum(1 for v in up_plot if v is not None)
    n_dn = sum(1 for v in dn_plot if v is not None)
    print(f"标的={sym}  数据范围: {fmt(tss[0])} ~ {fmt(tss[-1])}  4h根数={len(bc)}  信号数={len(signals)}")
    print(f"[校验] up_plot(支撑线)非None={n_up}/{len(bc)}  dn_plot(阻力线)非None={n_dn}/{len(bc)}")

    trades = []
    for f in signals:
        i = f["i"]
        long = f["type"] == "buy"
        entry = closes[i]
        fallback = entry * (1 - SL_PCT / 100) if long else entry * (1 + SL_PCT / 100)
        if mode == "pct":
            # sl_mode=pct：初始止损 = 开仓价固定百分比
            stop = fallback
        else:
            # sl_mode=st：初始止损 = 超趋线轨道；轨道无效/方向不对则回落固定百分比
            line0 = up_plot[i] if long else dn_plot[i]
            if line0 is None or (long and line0 >= entry) or (not long and line0 <= entry):
                stop = fallback
            else:
                stop = line0
        tp1_price = entry * (1 + TP1_PCT / 100) if long else entry * (1 - TP1_PCT / 100)
        coins = NOTIONAL / entry
        realized = -entry * coins * FEE      # 开仓手续费
        tp1_done = False
        closed = False
        reason = "末根平仓"

        for j in range(i + 1, len(bc)):
            # trail_with_st：对齐「这套」基准策略（bt_compare / 改后 feed._st_line）
            # 多头持仓跟踪取 dn_plot、空头取 up_plot（与按最新 trend 取线相反）
            if mode != "st_notrail":
                if long:
                    line = dn_plot[j]
                    if line is not None and line > stop:
                        stop = line
                else:
                    line = up_plot[j]
                    if line is not None and line < stop:
                        stop = line

            # 止损优先于止盈（同 position.check）
            if (lows[j] <= stop) if long else (highs[j] >= stop):
                px = stop
                left = (1 - TP1_RATIO) if tp1_done else 1.0
                realized += ((px - entry) * coins * left) if long else ((entry - px) * coins * left)
                realized -= px * coins * left * FEE
                reason = "止损"; closed = True; break

            if not tp1_done and ((highs[j] >= tp1_price) if long else (lows[j] <= tp1_price)):
                realized += ((tp1_price - entry) * coins * TP1_RATIO) if long \
                    else ((entry - tp1_price) * coins * TP1_RATIO)
                realized -= tp1_price * coins * TP1_RATIO * FEE
                tp1_done = True
                stop = entry          # move_sl_to_entry 保本

            if j in flip_idx:
                px = closes[j]
                left = (1 - TP1_RATIO) if tp1_done else 1.0
                realized += ((px - entry) * coins * left) if long else ((entry - px) * coins * left)
                realized -= px * coins * left * FEE
                reason = "反向信号"; closed = True; break

        if not closed:
            px = closes[-1]
            left = (1 - TP1_RATIO) if tp1_done else 1.0
            realized += ((px - entry) * coins * left) if long else ((entry - px) * coins * left)
            realized -= px * coins * left * FEE

        trades.append({"y": y_of(tss[i]), "realized": realized, "reason": reason, "long": long})

    print(f"\n配置: 止损模式={mode} · TP1 +{TP1_PCT}% 平 {TP1_RATIO*100:.0f}% · 保本 · "
          f"{'ST跟踪' if mode != 'st_notrail' else '不跟踪'} · 下一翻转平仓 · 费 0.05%单边")

    by_year = defaultdict(list)
    for t in trades:
        by_year[t["y"]].append(t)

    print("\n=== 分年收益 ===")
    for y in sorted(by_year):
        print(f"  {y} 年:")
        stat(by_year[y])

    print("\n=== 全部合计 ===")
    stat(trades)

    print("\n出场原因分布:")
    for k, v in Counter(t["reason"] for t in trades).most_common():
        print(f"  {k:<10}{v}")


if __name__ == "__main__":
    asyncio.run(main())
