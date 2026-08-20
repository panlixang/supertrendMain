"""
独立回测脚本 - 不依赖完整项目环境

直接运行测试增强版效果
"""

import json
import urllib.request
from datetime import datetime


def fetch_okx_candles(symbol: str, timeframe: str, limit: int = 500):
    """从OKX获取K线数据"""
    tf_map = {
        "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
        "1h": "1H", "4h": "4H", "1d": "1D"
    }
    bar = tf_map.get(timeframe, "15m")

    url = f"https://www.okx.com/api/v5/market/candles?instId={symbol}&bar={bar}&limit={limit}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "backtest/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

        if data.get("code") != "0":
            return []

        candles = []
        for row in data.get("data", []):
            candles.append({
                "ts": int(row[0]),
                "o": float(row[1]),
                "h": float(row[2]),
                "l": float(row[3]),
                "c": float(row[4]),
                "vol": float(row[5]),
            })

        # OKX返回倒序，需要反转
        candles.reverse()
        return candles

    except Exception as e:
        print(f"获取数据失败: {e}")
        return []


def calculate_ema(values, period):
    """计算EMA"""
    if len(values) < period:
        return [None] * len(values)

    result = [None] * len(values)
    alpha = 2 / (period + 1)

    # 初始值用SMA
    sma = sum(values[:period]) / period
    result[period - 1] = sma

    for i in range(period, len(values)):
        result[i] = values[i] * alpha + result[i-1] * (1 - alpha)

    return result


def calculate_atr(candles, period):
    """计算ATR"""
    if len(candles) < period:
        return [None] * len(candles)

    tr = [0.0] * len(candles)
    tr[0] = candles[0]["h"] - candles[0]["l"]

    for i in range(1, len(candles)):
        tr[i] = max(
            candles[i]["h"] - candles[i]["l"],
            abs(candles[i]["h"] - candles[i-1]["c"]),
            abs(candles[i]["l"] - candles[i-1]["c"])
        )

    # RMA (Wilder's smoothing)
    atr = [None] * len(candles)
    atr[period - 1] = sum(tr[:period]) / period

    for i in range(period, len(candles)):
        atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period

    return atr


def calculate_supertrend(candles, period=15, multiplier=9.1):
    """计算SuperTrend"""
    n = len(candles)
    if n < period + 1:
        return None

    atr = calculate_atr(candles, period)
    hl2 = [(c["h"] + c["l"]) / 2 for c in candles]

    up = [None] * n
    dn = [None] * n
    trend = [None] * n

    # 找到第一个有效位置
    start = period - 1

    up[start] = hl2[start] - multiplier * atr[start]
    dn[start] = hl2[start] + multiplier * atr[start]
    trend[start] = 1

    for i in range(start + 1, n):
        # 计算上下轨
        raw_up = hl2[i] - multiplier * atr[i]
        raw_dn = hl2[i] + multiplier * atr[i]

        # 棘轮逻辑
        up[i] = max(raw_up, up[i-1]) if candles[i-1]["c"] > up[i-1] else raw_up
        dn[i] = min(raw_dn, dn[i-1]) if candles[i-1]["c"] < dn[i-1] else raw_dn

        # 趋势判定
        if trend[i-1] == -1 and candles[i]["c"] > dn[i-1]:
            trend[i] = 1
        elif trend[i-1] == 1 and candles[i]["c"] < up[i-1]:
            trend[i] = -1
        else:
            trend[i] = trend[i-1]

    # 找翻转点
    signals = []
    for i in range(start + 1, n):
        if trend[i] != trend[i-1]:
            sig_type = "buy" if trend[i] == 1 else "sell"
            stop_line = up[i] if trend[i] == 1 else dn[i]
            signals.append({
                "i": i,
                "type": sig_type,
                "price": candles[i]["c"],
                "line": stop_line,
                "atr": atr[i]
            })

    return {"signals": signals, "trend": trend, "up": up, "dn": dn, "atr": atr}


def backtest_original(candles, leverage=3, margin=10.0, er_min=0.15):
    """原版回测：单档止盈1.5%平70%"""
    st = calculate_supertrend(candles, 15, 9.1)
    if not st or not st["signals"]:
        return {"error": "无信号"}

    equity = 10000.0
    position = None
    trades = []

    tp1_pct = 1.5
    tp1_ratio = 0.7

    for sig in st["signals"]:
        i = sig["i"]
        price = sig["price"]

        # 平仓
        if position:
            if (position["side"] == "long" and sig["type"] == "sell") or \
               (position["side"] == "short" and sig["type"] == "buy"):
                # 结算
                pnl_pct = (price - position["entry"]) / position["entry"] * 100
                if position["side"] == "short":
                    pnl_pct = -pnl_pct

                pnl = position["notional"] * pnl_pct / 100
                equity += pnl

                trades.append({
                    "side": position["side"],
                    "entry": position["entry"],
                    "exit": price,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct
                })
                position = None

        # 开仓
        if not position and sig["type"] in ["buy", "sell"]:
            notional = margin * leverage
            side = "long" if sig["type"] == "buy" else "short"
            position = {
                "side": side,
                "entry": price,
                "notional": notional,
                "stop": sig["line"],
                "tp1_done": False
            }

        # 盘中止盈止损检查（简化版）
        if position:
            for j in range(i, min(i + 20, len(candles))):
                c = candles[j]
                pnl_pct = (c["c"] - position["entry"]) / position["entry"] * 100
                if position["side"] == "short":
                    pnl_pct = -pnl_pct

                # 止损
                hit_stop = (c["l"] <= position["stop"] if position["side"] == "long"
                           else c["h"] >= position["stop"])
                if hit_stop:
                    pnl = position["notional"] * pnl_pct / 100
                    equity += pnl
                    trades.append({
                        "side": position["side"],
                        "entry": position["entry"],
                        "exit": position["stop"],
                        "pnl": pnl,
                        "pnl_pct": (position["stop"] - position["entry"]) / position["entry"] * 100 * (1 if position["side"] == "long" else -1),
                        "reason": "止损"
                    })
                    position = None
                    break

                # 止盈
                if not position["tp1_done"] and pnl_pct >= tp1_pct:
                    # 平70%
                    pnl = position["notional"] * tp1_ratio * pnl_pct / 100
                    equity += pnl
                    position["notional"] *= (1 - tp1_ratio)
                    position["tp1_done"] = True
                    position["stop"] = position["entry"]  # 保本

    wins = [t for t in trades if t.get("pnl", 0) > 0]
    final = equity
    ret = (final - 10000) / 10000 * 100

    return {
        "final": final,
        "return_pct": ret,
        "trades": len(trades),
        "wins": len(wins),
        "win_rate": len(wins) / len(trades) * 100 if trades else 0,
        "avg_win": sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0,
        "trade_list": trades[-10:],
    }


def backtest_enhanced(candles, leverage=3, margin=10.0):
    """增强版回测：三档止盈1%/2%/3.5%，智能止损"""
    st = calculate_supertrend(candles, 15, 9.1)
    if not st or not st["signals"]:
        return {"error": "无信号"}

    equity = 10000.0
    position = None
    trades = []

    # 三档止盈
    tp1_pct, tp1_ratio = 1.0, 0.3
    tp2_pct, tp2_ratio = 2.0, 0.4
    tp3_pct, tp3_ratio = 3.5, 0.3

    for sig in st["signals"]:
        i = sig["i"]
        price = sig["price"]

        # 平仓
        if position:
            if (position["side"] == "long" and sig["type"] == "sell") or \
               (position["side"] == "short" and sig["type"] == "buy"):
                pnl_pct = (price - position["entry"]) / position["entry"] * 100
                if position["side"] == "short":
                    pnl_pct = -pnl_pct

                pnl = position["notional"] * pnl_pct / 100
                equity += pnl

                trades.append({
                    "side": position["side"],
                    "entry": position["entry"],
                    "exit": price,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct
                })
                position = None

        # 开仓（增强止损：ST线外扩0.5倍ATR，最小1.2%）
        if not position and sig["type"] in ["buy", "sell"]:
            notional = margin * leverage
            side = "long" if sig["type"] == "buy" else "short"

            # 智能止损
            atr_buffer = sig["atr"] * 0.5 if sig["atr"] else 0
            st_stop = sig["line"] - atr_buffer if side == "long" else sig["line"] + atr_buffer
            min_stop_dist = price * 0.012  # 1.2%
            min_stop = price - min_stop_dist if side == "long" else price + min_stop_dist

            stop = min(st_stop, min_stop) if side == "long" else max(st_stop, min_stop)

            position = {
                "side": side,
                "entry": price,
                "notional": notional,
                "stop": stop,
                "tp1_done": False,
                "tp2_done": False,
                "max_pnl": 0
            }

        # 盘中止盈止损
        if position:
            for j in range(i, min(i + 20, len(candles))):
                c = candles[j]
                pnl_pct = (c["c"] - position["entry"]) / position["entry"] * 100
                if position["side"] == "short":
                    pnl_pct = -pnl_pct

                position["max_pnl"] = max(position["max_pnl"], pnl_pct)

                # 盈利保护：浮盈达1.5%后允许回撤0.8%
                if position["max_pnl"] >= 1.5:
                    protected_level = position["max_pnl"] - 0.8
                    if pnl_pct < protected_level:
                        # 触发保护止损
                        pnl = position["notional"] * pnl_pct / 100
                        equity += pnl
                        trades.append({
                            "side": position["side"],
                            "entry": position["entry"],
                            "exit": c["c"],
                            "pnl": pnl,
                            "pnl_pct": pnl_pct,
                            "reason": "盈利保护"
                        })
                        position = None
                        break

                # 止损
                hit_stop = (c["l"] <= position["stop"] if position["side"] == "long"
                           else c["h"] >= position["stop"])
                if hit_stop:
                    stop_pnl = (position["stop"] - position["entry"]) / position["entry"] * 100
                    if position["side"] == "short":
                        stop_pnl = -stop_pnl
                    pnl = position["notional"] * stop_pnl / 100
                    equity += pnl
                    trades.append({
                        "side": position["side"],
                        "entry": position["entry"],
                        "exit": position["stop"],
                        "pnl": pnl,
                        "pnl_pct": stop_pnl,
                        "reason": "止损"
                    })
                    position = None
                    break

                # 三档止盈
                if not position["tp2_done"] and pnl_pct >= tp3_pct:
                    pnl = position["notional"] * tp3_ratio * pnl_pct / 100
                    equity += pnl
                    position["notional"] *= (1 - tp3_ratio)
                    position["tp2_done"] = True
                    if position["notional"] < 1:
                        position = None
                        break

                elif not position["tp1_done"] and pnl_pct >= tp2_pct:
                    pnl = position["notional"] * tp2_ratio * pnl_pct / 100
                    equity += pnl
                    position["notional"] *= (1 - tp2_ratio)
                    position["tp1_done"] = True
                    position["stop"] = position["entry"] * 1.01 if position["side"] == "long" else position["entry"] * 0.99

                elif not position["tp1_done"] and pnl_pct >= tp1_pct:
                    pnl = position["notional"] * tp1_ratio * pnl_pct / 100
                    equity += pnl
                    position["notional"] *= (1 - tp1_ratio)
                    position["tp1_done"] = True
                    position["stop"] = position["entry"]  # 保本

    wins = [t for t in trades if t.get("pnl", 0) > 0]
    final = equity
    ret = (final - 10000) / 10000 * 100

    return {
        "final": final,
        "return_pct": ret,
        "trades": len(trades),
        "wins": len(wins),
        "win_rate": len(wins) / len(trades) * 100 if trades else 0,
        "avg_win": sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0,
        "trade_list": trades[-10:],
    }


def main():
    print("="*70)
    print("SuperTrend 原版 vs 增强版 回测对比")
    print("="*70)

    symbols = [
        ("BTC-USDT", "BTC"),
        # MU在OKX可能不存在，替换为SOL
        ("SOL-USDT", "SOL(高波动)"),
    ]

    for symbol, label in symbols:
        print(f"\n{'='*70}")
        print(f"回测品种: {label} ({symbol})")
        print(f"{'='*70}\n")

        print("正在获取数据...")
        candles = fetch_okx_candles(symbol, "15m", 500)

        if len(candles) < 100:
            print(f"ERR 数据不足: {len(candles)} 根")
            continue

        print(f"OK 获取 {len(candles)} 根K线")
        print(f"时间范围: {datetime.fromtimestamp(candles[0]['ts']/1000).strftime('%Y-%m-%d')} 至 "
              f"{datetime.fromtimestamp(candles[-1]['ts']/1000).strftime('%Y-%m-%d')}\n")

        # 原版回测
        print("1️⃣  原版策略 (1.5%止盈平70%)")
        result_orig = backtest_original(candles)
        if "error" not in result_orig:
            print(f"  最终权益: ${result_orig['final']:,.2f}")
            print(f"  收益率:   {result_orig['return_pct']:+.2f}%")
            print(f"  交易次数: {result_orig['trades']}")
            print(f"  胜率:     {result_orig['win_rate']:.1f}%")
            print(f"  平均盈利: {result_orig['avg_win']:+.2f}%")

        # 增强版回测
        print("\n2️⃣  增强版策略 (三档止盈+智能止损)")
        result_enh = backtest_enhanced(candles)
        if "error" not in result_enh:
            print(f"  最终权益: ${result_enh['final']:,.2f}")
            print(f"  收益率:   {result_enh['return_pct']:+.2f}%")
            print(f"  交易次数: {result_enh['trades']}")
            print(f"  胜率:     {result_enh['win_rate']:.1f}%")
            print(f"  平均盈利: {result_enh['avg_win']:+.2f}%")

        # 对比
        if "error" not in result_orig and "error" not in result_enh:
            print("\n3️⃣  对比分析")
            ret_diff = result_enh['return_pct'] - result_orig['return_pct']
            wr_diff = result_enh['win_rate'] - result_orig['win_rate']

            print(f"  收益率差异: {ret_diff:+.2f}%")
            print(f"  胜率差异:   {wr_diff:+.1f}%")

            if ret_diff > 5:
                print("  SUCCESS 增强版收益明显更高！")
            elif ret_diff > 0:
                print("  OK 增强版收益略高")
            else:
                print("  WARN  增强版收益未改善")

            if wr_diff > 5:
                print("  TARGET 增强版胜率明显提升！")
            elif wr_diff > 0:
                print("  OK 增强版胜率提升")


if __name__ == "__main__":
    main()
