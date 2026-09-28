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
MODE = "pct"             # 止损模式：pct=硬止损 | st=超趋线轨道+跟随 | st_notrail


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


def summarize(trades: list[dict]) -> dict:
    """返回数值版汇总（与 stat 同口径，含相对权益曲线最大回撤）。"""
    rs = [t["realized"] for t in trades]
    if not rs:
        return {"n": 0, "wr": 0.0, "tot": 0.0, "pf": 0.0, "payoff": 0.0, "mdd": 0.0}
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    tot = sum(rs)
    pf = sum(wins) / (-sum(losses)) if losses and sum(losses) != 0 else float("inf")
    wr = len(wins) / len(rs) * 100
    aw = sum(wins) / len(wins) if wins else 0.0
    al = (-sum(losses)) / len(losses) if losses else 0.0
    payoff = aw / al if al else float("inf")
    eq, cur = [], 0.0
    for r in rs:
        cur += r
        eq.append(NOTIONAL + cur)
    mx, mdd = eq[0], 0.0
    for v in eq:
        mx = max(mx, v)
        mdd = min(mdd, (v - mx) / mx)
    return {"n": len(rs), "wr": wr, "tot": tot / NOTIONAL * 100,
            "pf": pf, "payoff": payoff, "mdd": mdd * 100}


async def main():
    import sys
    global MODE, SL_PCT, TP1_PCT, TP1_RATIO
    targets = sys.argv[1:] or ["ETH-USDT-SWAP", "ETH-USDT"]
    # 止损模式：pct=开仓价固定止损 | st=超趋线轨道+跟随 | st_notrail=超趋线初始但不跟随
    mode = "pct"
    sl_arg = float(sys.argv[3]) if len(sys.argv) > 3 else SL_PCT
    tp1_arg = float(sys.argv[4]) if len(sys.argv) > 4 else TP1_PCT
    ratio_arg = float(sys.argv[5]) if len(sys.argv) > 5 else TP1_RATIO
    if len(sys.argv) > 2 and sys.argv[2] in ("pct", "st", "st_notrail"):
        mode = sys.argv[2]
        targets = sys.argv[1:2]
    MODE, SL_PCT, TP1_PCT, TP1_RATIO = mode, sl_arg, tp1_arg, ratio_arg
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

    def run_backtest(sl_pct: float, tp1_pct: float = TP1_PCT, ratio: float = TP1_RATIO, mode: str = MODE) -> list:
        trades = []
        for f in signals:
            i = f["i"]
            long = f["type"] == "buy"
            entry = closes[i]
            fallback = entry * (1 - sl_pct / 100) if long else entry * (1 + sl_pct / 100)
            if mode == "pct":
                stop = fallback
            else:
                line0 = up_plot[i] if long else dn_plot[i]
                if line0 is None or (long and line0 >= entry) or (not long and line0 <= entry):
                    stop = fallback
                else:
                    stop = line0
            tp1_price = entry * (1 + tp1_pct / 100) if long else entry * (1 - tp1_pct / 100)
            coins = NOTIONAL / entry
            realized = -entry * coins * FEE
            tp1_done = False
            closed = False

            for j in range(i + 1, len(bc)):
                if mode != "st_notrail":
                    if long:
                        line = up_plot[j]        # 多头跟支撑线（与 A/bt_pattern_page 一致）
                        if line is not None and line > stop:
                            stop = line
                    else:
                        line = dn_plot[j]        # 空头跟阻力线（与 A 一致）
                        if line is not None and line < stop:
                            stop = line
                if (lows[j] <= stop) if long else (highs[j] >= stop):
                    px = stop
                    left = (1 - ratio) if tp1_done else 1.0
                    realized += ((px - entry) * coins * left) if long else ((entry - px) * coins * left)
                    realized -= px * coins * left * FEE
                    closed = True; break
                if not tp1_done and ((highs[j] >= tp1_price) if long else (lows[j] <= tp1_price)):
                    realized += ((tp1_price - entry) * coins * ratio) if long \
                        else ((entry - tp1_price) * coins * ratio)
                    realized -= tp1_price * coins * ratio * FEE
                    tp1_done = True
                    stop = entry
                if j in flip_idx:
                    px = closes[j]
                    left = (1 - ratio) if tp1_done else 1.0
                    realized += ((px - entry) * coins * left) if long else ((entry - px) * coins * left)
                    realized -= px * coins * left * FEE
                    closed = True; break
            if not closed:
                px = closes[-1]
                left = (1 - ratio) if tp1_done else 1.0
                realized += ((px - entry) * coins * left) if long else ((entry - px) * coins * left)
                realized -= px * coins * left * FEE
            trades.append({"y": y_of(tss[i]), "realized": realized, "long": long})
        return trades

    print(f"\n配置: 止损模式={mode} · TP1 +{TP1_PCT}% 平 {TP1_RATIO*100:.0f}% · 保本 · "
          f"{'ST跟踪' if mode != 'st_notrail' else '不跟踪'} · 下一翻转平仓 · 费 0.05%单边")

    # pct 模式：硬止损视角下的阈值敏感度（st 模式下 sl 仅作兜底，跳过这些表）
    if MODE == "pct":
        # 硬止损敏感度：2% / 3% / 4% / 5%（2026 年，固定金额 1X 口径）
        print("\n=== 硬止损敏感度（2026 年）===")
        print(f"{'sl%':>4} {'笔数':>5} {'胜率':>7} {'收益%':>9} {'pf':>6} {'盈亏比':>7}")
        for sl in [2, 3, 4, 5]:
            tr = run_backtest(sl)
            y26 = [t for t in tr if t["y"] == 2026]
            rs = [t["realized"] for t in y26]
            n = len(rs)
            wins = [r for r in rs if r > 0]
            losses = [-r for r in rs if r <= 0]
            wr = len(wins) / n * 100 if n else 0
            tot = sum(rs)
            gw, gl = sum(wins), sum(losses)
            pf = gw / gl if gl else float("inf")
            aw = gw / len(wins) if wins else 0
            al = gl / len(losses) if losses else 0
            payoff = aw / al if al else float("inf")
            print(f"{sl:>4} {n:>5} {wr:>6.1f}% {tot / NOTIONAL * 100:>+9.1f} {pf:>6.2f} {payoff:>7.2f}")

        # TP1 阈值敏感度（固定 sl=2%）
        print("\n=== TP1 阈值敏感度（sl=2%，2026 年）===")
        print(f"{'tp1%':>5} {'笔数':>5} {'胜率':>7} {'收益%':>9} {'pf':>6} {'盈亏比':>7}")
        for tp1 in [2, 3, 4, 5]:
            tr = run_backtest(SL_PCT, tp1)
            y26 = [t for t in tr if t["y"] == 2026]
            rs = [t["realized"] for t in y26]
            n = len(rs)
            wins = [r for r in rs if r > 0]
            losses = [-r for r in rs if r <= 0]
            wr = len(wins)/n*100 if n else 0
            tot = sum(rs)
            gw, gl = sum(wins), sum(losses)
            pf = gw/gl if gl else float("inf")
            aw = gw/len(wins) if wins else 0
            al = gl/len(losses) if losses else 0
            payoff = aw/al if al else float("inf")
            print(f"{tp1:>5} {n:>5} {wr:>6.1f}% {tot/NOTIONAL*100:>+9.1f} {pf:>6.2f} {payoff:>7.2f}")

        # 组合：sl=3% 下 TP1 阈值变化
        print("\n=== 组合：sl=3% 下 TP1 阈值敏感度（2026 年）===")
        print(f"{'tp1%':>5} {'笔数':>5} {'胜率':>7} {'收益%':>9} {'pf':>6} {'盈亏比':>7}")
        for tp1 in [2, 3, 4, 5]:
            tr = run_backtest(3, tp1)
            y26 = [t for t in tr if t["y"] == 2026]
            rs = [t["realized"] for t in y26]
            n = len(rs)
            wins = [r for r in rs if r > 0]
            losses = [-r for r in rs if r <= 0]
            wr = len(wins)/n*100 if n else 0
            tot = sum(rs)
            gw, gl = sum(wins), sum(losses)
            pf = gw/gl if gl else float("inf")
            aw = gw/len(wins) if wins else 0
            al = gl/len(losses) if losses else 0
            payoff = aw/al if al else float("inf")
            print(f"{tp1:>5} {n:>5} {wr:>6.1f}% {tot/NOTIONAL*100:>+9.1f} {pf:>6.2f} {payoff:>7.2f}")

    # 原分年 + 合计（sl=基准值）
    trades = run_backtest(SL_PCT)
    by_year = defaultdict(list)
    for t in trades:
        by_year[t["y"]].append(t)
    sl_desc = f"st轨道止损+兜底{SL_PCT}%" if MODE == "st" else f"sl={SL_PCT}%"
    print("\n=== 分年收益（" + sl_desc + "）===")
    for y in sorted(by_year):
        print(f"  {y} 年:")
        stat(by_year[y])
    print("\n=== 全部合计 ===")
    stat(trades)

    # 参数寻优网格 —— 对非 BTC 标的（ETH/CL）做寻优；st 模式扫 tp1 触发阈值（与 A 出场一致），pct 模式扫 sl×tp1
    if sym.upper() not in ("BTC-USDT", "BTC-USDT-SWAP"):
        title = "CL" if "CL" in sym.upper() else "ETH"
        if MODE == "st":
            print(f"\n=== {title} 参数寻优（tp1 触发阈值，st 轨道止损+平{TP1_RATIO*100:.0f}%，4h，合计 2026）===")
            print(f"{'tp1%':>5} {'笔':>4} {'胜率':>6} {'收益%':>8} {'pf':>6} {'盈亏比':>6} {'回撤%':>7}")
            for tp1 in [1.5, 2, 3, 4, 5]:
                s = summarize(run_backtest(SL_PCT, tp1, TP1_RATIO, "st"))
                print(f"{tp1:>5} {s['n']:>4} {s['wr']:>5.1f}% {s['tot']:>+8.1f} {s['pf']:>6.2f} {s['payoff']:>6.2f} {s['mdd']:>7.1f}")
        else:
            print(f"\n=== {title} 参数寻优（sl × tp1，合计 2024-2026）===")
            print(f"{'sl%':>4} {'tp1%':>5} {'笔':>4} {'胜率':>6} {'收益%':>8} {'pf':>6} {'盈亏比':>6} {'回撤%':>7}")
            for sl in [2, 3, 4, 5]:
                for tp1 in [2, 3, 4, 5]:
                    s = summarize(run_backtest(sl, tp1))
                    print(f"{sl:>4} {tp1:>5} {s['n']:>4} {s['wr']:>5.1f}% {s['tot']:>+8.1f} {s['pf']:>6.2f} {s['payoff']:>6.2f} {s['mdd']:>7.1f}")


if __name__ == "__main__":
    asyncio.run(main())
