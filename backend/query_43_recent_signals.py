# -*- coding: utf-8 -*-
"""Query signals from 43 server for SKHYNIX, MU, SNDK (Oct 18-19)"""
import urllib.request
import json
from datetime import datetime, timedelta

BASE = "http://43.108.10.84:5174"
SYMBOLS = ["SKHYNIX-USDT-SWAP", "MU-USDT-SWAP", "SNDK-USDT-SWAP"]

# Timestamp range: 2026-10-18 00:00:00 to 2026-10-19 23:59:59
START_TS = int(datetime(2026, 10, 18, 0, 0, 0).timestamp() * 1000)
END_TS = int(datetime(2026, 10, 19, 23, 59, 59).timestamp() * 1000)


def get(ep):
    try:
        resp = urllib.request.urlopen(BASE + ep, timeout=15)
        return json.loads(resp.read())
    except Exception as e:
        return {"__error__": repr(e)}


def format_ts(ts_ms):
    """Convert timestamp to readable format"""
    return datetime.fromtimestamp(ts_ms / 1000).strftime("%m-%d %H:%M")


print("=" * 80)
print("Query 43 Server Signal Status")
print(f"Time Range: 2026-10-18 00:00 ~ 2026-10-19 23:59")
print(f"Symbols: {', '.join(SYMBOLS)}")
print("=" * 80)

# 1. Get all signals
all_signals = get("/api/signals?limit=500")

if "__error__" in all_signals:
    print(f"\n[ERROR] Failed to get signals: {all_signals['__error__']}")
    exit(1)

# 2. Filter and analyze by symbol
for symbol in SYMBOLS:
    print(f"\n{'=' * 80}")
    print(f"Symbol: {symbol}")
    print("=" * 80)

    # Filter signals for this symbol within time range
    symbol_signals = [
        s for s in all_signals
        if s.get("symbol") == symbol and START_TS <= s.get("ts", 0) <= END_TS
    ]

    if not symbol_signals:
        print(f"  [NONE] No signals in this time period")
        continue

    print(f"  [OK] Total {len(symbol_signals)} signals\n")

    # Count tradable signals
    tradable_signals = [s for s in symbol_signals if s.get("will_trade")]
    hidden_signals = [s for s in symbol_signals if s.get("hidden")]
    alert_only = [s for s in symbol_signals if not s.get("will_trade") and not s.get("hidden")]

    print(f"  Signal Statistics:")
    print(f"     - Tradable (will_trade=True): {len(tradable_signals)}")
    print(f"     - Hidden (hidden=True): {len(hidden_signals)}")
    print(f"     - Alert Only: {len(alert_only)}")

    # Show tradable signal details
    if tradable_signals:
        print(f"\n  [TRADABLE] Signals that meet trading conditions:")
        for idx, sig in enumerate(tradable_signals, 1):
            time_str = format_ts(sig.get("ts", 0))
            typ = sig.get("type", "?").upper()
            tf = sig.get("tf", "?")
            price = sig.get("price", 0)
            grade = sig.get("grade", "?")
            score = sig.get("score", "?")
            regime_label = sig.get("regime", {}).get("label", "?")
            profile = sig.get("profile", "normal")
            trade_half = sig.get("trade_half", False)

            print(f"\n     [{idx}] {time_str} | {typ:4s} | {tf:3s} | {price:.4f}")
            print(f"         Grade: {grade} | Score: {score}/3 | Regime: {regime_label}")
            print(f"         Profile: {profile} | Size: {'Half' if trade_half else 'Full'}")

            # Show score details if available
            score_detail = sig.get("score_detail")
            if score_detail and isinstance(score_detail, dict):
                total = score_detail.get("total_score", 0)
                print(f"         Total Score: {total:.1f}")

    # Show blocked signal reasons
    if alert_only:
        print(f"\n  [ALERT] Alert-only signals (first 3 block reasons):")
        for idx, sig in enumerate(alert_only[:3], 1):
            time_str = format_ts(sig.get("ts", 0))
            typ = sig.get("type", "?").upper()
            tf = sig.get("tf", "?")
            reasons = sig.get("gate_reasons", [])
            print(f"     [{idx}] {time_str} | {typ:4s} | {tf:3s}")
            if reasons:
                print(f"         Blocked: {'; '.join(reasons[:2])}")

# 3. Query current positions
print(f"\n{'=' * 80}")
print("Current Positions")
print("=" * 80)

positions = get("/api/trade/positions")
if "__error__" in positions:
    print(f"  [ERROR] Failed to get positions: {positions['__error__']}")
else:
    pos_dict = positions.get("positions", {})
    if not pos_dict:
        print("  - No open positions")
    else:
        for sym, pos in pos_dict.items():
            if sym in SYMBOLS:
                side = pos.get("side", "?")
                entry = pos.get("entry", 0)
                qty = pos.get("qty", 0)
                pnl = pos.get("realized", 0)
                roe = pos.get("roe_pct", 0)
                print(f"  {sym}:")
                print(f"    Side: {side} | Entry: {entry:.4f} | Qty: {qty}")
                print(f"    ROE: {roe:+.2f}% | Realized: {pnl:+.2f}U")

# 4. Query recent trade history
print(f"\n{'=' * 80}")
print("Recent Trade History (Oct 18 - Now)")
print("=" * 80)

for symbol in SYMBOLS:
    history = get(f"/api/trade/history?sym={symbol}&recent=50")
    if "__error__" in history:
        continue

    summary = history.get("summary", {})
    trades = summary.get("trades", [])

    # Filter trades within time range
    recent_trades = [
        t for t in trades
        if t.get("entry_ts", 0) >= START_TS
    ]

    if recent_trades:
        print(f"\n  {symbol}: {len(recent_trades)} trades")
        for t in recent_trades[:5]:  # Show max 5 trades
            entry_time = format_ts(t.get("entry_ts", 0))
            exit_time = format_ts(t.get("exit_ts", 0))
            pnl = t.get("pnl", 0)
            pnl_pct = t.get("pnl_pct", 0)
            side = t.get("side", "?")
            print(f"    {entry_time}~{exit_time} | {side:5s} | {pnl:+7.2f}U ({pnl_pct:+.2f}%)")

print(f"\n{'=' * 80}")
print("Query Complete")
print("=" * 80)
