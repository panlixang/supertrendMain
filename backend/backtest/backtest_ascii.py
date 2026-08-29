"""
Standalone backtest script - ASCII only version
"""

import json
import urllib.request
from datetime import datetime


def fetch_okx_candles(symbol, timeframe, limit=500):
    """Fetch candles from OKX with pagination"""
    tf_map = {
        "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
        "1h": "1H", "4h": "4H", "1d": "1D"
    }
    bar = tf_map.get(timeframe, "15m")

    all_candles = {}
    after = None

    # Fetch in batches of 300 (OKX limit)
    while len(all_candles) < limit:
        batch_size = min(300, limit - len(all_candles))
        url = f"https://www.okx.com/api/v5/market/history-candles?instId={symbol}&bar={bar}&limit={batch_size}"
        if after:
            url += f"&after={after}"

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "backtest/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())

            if data.get("code") != "0" or not data.get("data"):
                break

            # Store by timestamp to deduplicate
            for row in data.get("data", []):
                ts = int(row[0])
                all_candles[ts] = {
                    "ts": ts,
                    "o": float(row[1]),
                    "h": float(row[2]),
                    "l": float(row[3]),
                    "c": float(row[4]),
                    "vol": float(row[5]),
                }

            # Set cursor for next page (earliest timestamp)
            after = min(int(row[0]) for row in data["data"])

            if len(data["data"]) < batch_size:
                break

        except Exception as e:
            print(f"Error fetching data: {e}")
            break

    # Sort by timestamp
    candles = sorted(all_candles.values(), key=lambda x: x["ts"])
    return candles


def calculate_atr(candles, period):
    """Calculate ATR"""
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

    atr = [None] * len(candles)
    atr[period - 1] = sum(tr[:period]) / period

    for i in range(period, len(candles)):
        atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period

    return atr


def calculate_supertrend(candles, period=15, multiplier=9.1):
    """Calculate SuperTrend"""
    n = len(candles)
    if n < period + 1:
        return None

    atr = calculate_atr(candles, period)
    hl2 = [(c["h"] + c["l"]) / 2 for c in candles]

    up = [None] * n
    dn = [None] * n
    trend = [None] * n

    start = period - 1

    up[start] = hl2[start] - multiplier * atr[start]
    dn[start] = hl2[start] + multiplier * atr[start]
    trend[start] = 1

    for i in range(start + 1, n):
        raw_up = hl2[i] - multiplier * atr[i]
        raw_dn = hl2[i] + multiplier * atr[i]

        up[i] = max(raw_up, up[i-1]) if candles[i-1]["c"] > up[i-1] else raw_up
        dn[i] = min(raw_dn, dn[i-1]) if candles[i-1]["c"] < dn[i-1] else raw_dn

        if trend[i-1] == -1 and candles[i]["c"] > dn[i-1]:
            trend[i] = 1
        elif trend[i-1] == 1 and candles[i]["c"] < up[i-1]:
            trend[i] = -1
        else:
            trend[i] = trend[i-1]

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


def backtest_original(candles, leverage=3, margin=10.0):
    """Original: 1.5% TP, close 70%"""
    st = calculate_supertrend(candles, 15, 9.1)
    if not st or not st["signals"]:
        return {"error": "No signals"}

    equity = 10000.0
    position = None
    trades = []
    stops = 0

    tp1_pct = 1.5
    tp1_ratio = 0.7

    for sig in st["signals"]:
        i = sig["i"]
        price = sig["price"]

        # Close position
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

        # Open position
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

        # Check SL/TP
        if position and i < len(candles) - 1:
            for j in range(i + 1, min(i + 20, len(candles))):
                c = candles[j]
                pnl_pct = (c["c"] - position["entry"]) / position["entry"] * 100
                if position["side"] == "short":
                    pnl_pct = -pnl_pct

                # Stop loss
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
                        "reason": "stop"
                    })
                    stops += 1
                    position = None
                    break

                # Take profit
                if not position["tp1_done"] and pnl_pct >= tp1_pct:
                    pnl = position["notional"] * tp1_ratio * pnl_pct / 100
                    equity += pnl
                    position["notional"] *= (1 - tp1_ratio)
                    position["tp1_done"] = True
                    position["stop"] = position["entry"]

    wins = [t for t in trades if t.get("pnl", 0) > 0]
    final = equity
    ret = (final - 10000) / 10000 * 100

    return {
        "final": final,
        "return_pct": ret,
        "trades": len(trades),
        "wins": len(wins),
        "stops": stops,
        "win_rate": len(wins) / len(trades) * 100 if trades else 0,
        "avg_win": sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0,
    }


def backtest_enhanced(candles, leverage=3, margin=10.0):
    """Enhanced: 3-stage TP (1%/2%/3.5%), smart SL"""
    st = calculate_supertrend(candles, 15, 9.1)
    if not st or not st["signals"]:
        return {"error": "No signals"}

    equity = 10000.0
    position = None
    trades = []
    stops = 0

    tp1_pct, tp1_ratio = 1.0, 0.3
    tp2_pct, tp2_ratio = 2.0, 0.4
    tp3_pct, tp3_ratio = 3.5, 0.3

    for sig in st["signals"]:
        i = sig["i"]
        price = sig["price"]

        # Close position
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

        # Open position with smart stop loss
        if not position and sig["type"] in ["buy", "sell"]:
            notional = margin * leverage
            side = "long" if sig["type"] == "buy" else "short"

            # Smart SL: ST line + 0.5*ATR buffer, min 1.2%
            atr_buffer = sig["atr"] * 0.5 if sig["atr"] else 0
            st_stop = sig["line"] - atr_buffer if side == "long" else sig["line"] + atr_buffer
            min_stop_dist = price * 0.012
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

        # Check SL/TP
        if position and i < len(candles) - 1:
            for j in range(i + 1, min(i + 20, len(candles))):
                c = candles[j]
                pnl_pct = (c["c"] - position["entry"]) / position["entry"] * 100
                if position["side"] == "short":
                    pnl_pct = -pnl_pct

                position["max_pnl"] = max(position["max_pnl"], pnl_pct)

                # Profit protection: allow 0.8% drawdown from 1.5% profit
                if position["max_pnl"] >= 1.5:
                    protected_level = position["max_pnl"] - 0.8
                    if pnl_pct < protected_level:
                        pnl = position["notional"] * pnl_pct / 100
                        equity += pnl
                        trades.append({
                            "side": position["side"],
                            "entry": position["entry"],
                            "exit": c["c"],
                            "pnl": pnl,
                            "pnl_pct": pnl_pct,
                            "reason": "profit_protect"
                        })
                        position = None
                        break

                # Stop loss
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
                        "reason": "stop"
                    })
                    stops += 1
                    position = None
                    break

                # 3-stage TP
                if not position.get("tp3_done") and pnl_pct >= tp3_pct:
                    pnl = position["notional"] * tp3_ratio * tp3_pct / 100
                    equity += pnl
                    position["notional"] *= (1 - tp3_ratio)
                    position["tp3_done"] = True
                    if position["notional"] < 1:
                        position = None
                        break

                elif not position.get("tp2_done") and pnl_pct >= tp2_pct:
                    pnl = position["notional"] * tp2_ratio * tp2_pct / 100
                    equity += pnl
                    position["notional"] *= (1 - tp2_ratio)
                    position["tp2_done"] = True
                    position["stop"] = position["entry"] * 1.01 if position["side"] == "long" else position["entry"] * 0.99

                elif not position.get("tp1_done") and pnl_pct >= tp1_pct:
                    pnl = position["notional"] * tp1_ratio * tp1_pct / 100
                    equity += pnl
                    position["notional"] *= (1 - tp1_ratio)
                    position["tp1_done"] = True
                    position["stop"] = position["entry"]

    wins = [t for t in trades if t.get("pnl", 0) > 0]
    final = equity
    ret = (final - 10000) / 10000 * 100

    return {
        "final": final,
        "return_pct": ret,
        "trades": len(trades),
        "wins": len(wins),
        "stops": stops,
        "win_rate": len(wins) / len(trades) * 100 if trades else 0,
        "avg_win": sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0,
    }


def main():
    print("=" * 70)
    print("SuperTrend: Original vs Enhanced Backtest")
    print("=" * 70)

    symbols = [
        ("BTC-USDT", "BTC"),
        ("SOL-USDT", "SOL"),
        ("ETH-USDT", "ETH"),
    ]

    for symbol, label in symbols:
        print(f"\n{'=' * 70}")
        print(f"Symbol: {label} ({symbol})")
        print(f"{'=' * 70}\n")

        print("Fetching data...")
        candles = fetch_okx_candles(symbol, "1h", 2000)  # 2000 hours = ~80 days

        if len(candles) < 100:
            print(f"[ERROR] Insufficient data: {len(candles)} candles")
            continue

        print(f"[OK] Fetched {len(candles)} candles")
        print(f"Period: {datetime.fromtimestamp(candles[0]['ts']/1000).strftime('%Y-%m-%d')} to "
              f"{datetime.fromtimestamp(candles[-1]['ts']/1000).strftime('%Y-%m-%d')}")

        # Check signals
        st_test = calculate_supertrend(candles, 15, 9.1)
        if st_test and st_test.get("signals"):
            print(f"[DEBUG] Found {len(st_test['signals'])} signals\n")
        else:
            print(f"[WARN] No signals found, skipping this symbol\n")
            continue

        # Original backtest
        print("[1] Original Strategy (TP 1.5% close 70%)")
        result_orig = backtest_original(candles)
        if "error" not in result_orig:
            print(f"    Final Equity:  ${result_orig['final']:,.2f}")
            print(f"    Return:        {result_orig['return_pct']:+.2f}%")
            print(f"    Trades:        {result_orig['trades']}")
            print(f"    Win Rate:      {result_orig['win_rate']:.1f}%")
            print(f"    Avg Win:       {result_orig['avg_win']:+.2f}%")
            print(f"    Stop Losses:   {result_orig['stops']}")

        # Enhanced backtest
        print("\n[2] Enhanced Strategy (3-stage TP + Smart SL)")
        result_enh = backtest_enhanced(candles)
        if "error" not in result_enh:
            print(f"    Final Equity:  ${result_enh['final']:,.2f}")
            print(f"    Return:        {result_enh['return_pct']:+.2f}%")
            print(f"    Trades:        {result_enh['trades']}")
            print(f"    Win Rate:      {result_enh['win_rate']:.1f}%")
            print(f"    Avg Win:       {result_enh['avg_win']:+.2f}%")
            print(f"    Stop Losses:   {result_enh['stops']}")

        # Comparison
        if "error" not in result_orig and "error" not in result_enh:
            print("\n[3] Comparison")
            ret_diff = result_enh['return_pct'] - result_orig['return_pct']
            wr_diff = result_enh['win_rate'] - result_orig['win_rate']
            stop_diff = result_enh['stops'] - result_orig['stops']

            print(f"    Return Delta:    {ret_diff:+.2f}%")
            print(f"    Win Rate Delta:  {wr_diff:+.1f}%")
            print(f"    Stop Loss Delta: {stop_diff:+d}")

            if ret_diff > 5:
                print("    [SUCCESS] Enhanced significantly better!")
            elif ret_diff > 0:
                print("    [OK] Enhanced slightly better")
            else:
                print("    [WARN] Enhanced not better")


if __name__ == "__main__":
    main()
