"""归一化对照: 4h MA30 方向门 + 线上评分/TP-SL 出场（OKX 1h / 10x）。

方向门 trend_align: 取每根 1h K 时刻最近的 4h 收盘, 收盘价 > MA30 -> 只多(1),
                     收盘价 < MA30 -> 只空(-1)。与 _live_cfg_btc_103 同框架,
                     仅多一层高周期方向过滤, 用于消 10×3 的逆趋势洗盘。
ST 分别跑 11×4(线上) 与 10×3(覆盖)。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _live_cfg_btc_103 as L
from backtest import run_backtest

LIVE_URL = "http://43.108.10.84:5174/api/trade/symbols"


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "bt/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def build_trend_align(c4h, c1h):
    closes = [c["c"] for c in c4h]
    n = len(c4h)
    ma = [None] * n
    for i in range(29, n):
        ma[i] = sum(closes[i - 29:i + 1]) / 30
    out = {}
    j = 0
    for c in c1h:
        ts = c["ts"]
        while j + 1 < n and c4h[j + 1]["ts"] <= ts:
            j += 1
        m = ma[j] if c4h[j]["ts"] <= ts else None
        if m is None:
            out[ts] = None
        else:
            out[ts] = 1 if c4h[j]["c"] > m else -1
    return out


def run_one(sym, candles, cbtf, p, trend_align):
    return run_backtest(
        candles, p, init_cash=100.0, fee_rate=0.0005, allow_short=True,
        exit_rules=L.exit_rules(sym), sizing="fixed",
        margin_usdt=sym["margin_usdt"], leverage=sym["leverage"],
        live_gate=L.trade_cfg(sym), gate_tf=sym["allow_tfs"][0],
        candles_by_tf=cbtf, trend_align=trend_align,
    )


def main():
    live = _get(LIVE_URL)
    sym = [s for s in live["symbols"] if "BTC" in s["symbol"].upper()][0]
    gate_tf = sym["allow_tfs"][0]
    bars = L.BARS.get(gate_tf, 4500)

    candles = L.fetch_candles(sym["symbol"], gate_tf, bars)
    cbtf = {gate_tf: candles}
    for tf in L.BIAS_TFS:
        if tf == gate_tf:
            continue
        ex = L.fetch_candles(sym["symbol"], tf, min(bars, L.BARS.get(tf, 4500)))
        if ex:
            cbtf[tf] = ex

    # 4h MA30 方向门
    c4h = L.fetch_candles(sym["symbol"], "4h", 1200)
    trend_align = build_trend_align(c4h, candles)
    bull = sum(1 for v in trend_align.values() if v == 1)
    bear = sum(1 for v in trend_align.values() if v == -1)
    flat = sum(1 for v in trend_align.values() if v is None)
    print(f"\n=== 4h MA30 方向门  (多{bull}/空{bear}/无{flat} 根 1h) ===")

    configs = [
        ("11×4 (线上)", {"periods": 11, "multiplier": 4.0, "src": "hl2", "change_atr": True}),
        ("10×3 (覆盖)", {"periods": 10, "multiplier": 3.0, "src": "hl2", "change_atr": True}),
    ]
    results = []
    for label, p in configs:
        r = run_one(sym, candles, cbtf, p, trend_align)
        if "error" in r:
            print(f"  [{label}] ERR {r['error']}")
            continue
        pnl = round(r["final"] - r["init_cash"], 2)
        ab = r.get("align_blocked", r.get("n_align_block", "?"))
        row = {
            "st": label, "trades": r["trades"], "pnl_u": pnl,
            "margin_roi_pct": round(pnl / sym["margin_usdt"] * 100, 1),
            "hold_pct": r["hold_pct"], "max_dd_pct": r["max_dd_pct"],
            "win_rate": r["win_rate"], "avg_win": r["avg_win"],
            "avg_loss": r["avg_loss"], "profit_factor": r["profit_factor"],
            "er_blocked": r["er_blocked"], "align_blocked": ab,
            "tp1": r["tp1_count"], "tp2": r.get("tp2_count", 0),
            "tp3": r.get("tp3_count", 0), "stops": r["stop_count"],
            "reverses": r["reverse_count"],
        }
        results.append(row)
        print(f"\n  ── ST {label} + 4h MA30 方向门 ──")
        print(f"    交易 {row['trades']}  净利润 {pnl}U  保证金ROI {row['margin_roi_pct']}%  "
              f"买入持有 {row['hold_pct']}%")
        print(f"    胜率 {row['win_rate']}%  PF {row['profit_factor']}  "
              f"最大回撤 {row['max_dd_pct']}%")
        print(f"    被评分拦 {row['er_blocked']}  被方向门拦 {row['align_blocked']}  "
              f"TP1/2/3={row['tp1']}/{row['tp2']}/{row['tp3']}  止损 {row['stops']}  反向 {row['reverses']}")

    out_path = os.path.join(os.path.dirname(__file__), "_live_cfg_btc_ma30.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
