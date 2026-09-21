"""同窗口 4 组合对照 (OKX 1h / 10x / 线上评分 + TP-SL):

  ① 11×4 无方向门      ② 11×4 + 4h MA30 方向门
  ③ 10×3 无方向门      ④ 10×3 + 4h MA30 方向门

一次拉取 4500 根 1h + 多周期 + 4h, 四组共用同一数据窗口, 杜绝端点漂移。
"""
from __future__ import annotations

import json
import os
import sys
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
        out[ts] = (1 if c4h[j]["c"] > m else -1) if m is not None else None
    return out


def ts_fmt(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


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
    c4h = L.fetch_candles(sym["symbol"], "4h", 1200)
    trend_align = build_trend_align(c4h, candles)

    win_start = ts_fmt(candles[0]["ts"])
    win_end = ts_fmt(candles[-1]["ts"])
    print(f"\n=== 窗口 {win_start} ~ {win_end}  1h {len(candles)} 根 / 10x / 线上评分+TP-SL ===\n")

    configs = [
        ("① 11×4 无门",      {"periods": 11, "multiplier": 4.0, "src": "hl2", "change_atr": True}, None),
        ("② 11×4 +MA30门",   {"periods": 11, "multiplier": 4.0, "src": "hl2", "change_atr": True}, trend_align),
        ("③ 10×3 无门",      {"periods": 10, "multiplier": 3.0, "src": "hl2", "change_atr": True}, None),
        ("④ 10×3 +MA30门",   {"periods": 10, "multiplier": 3.0, "src": "hl2", "change_atr": True}, trend_align),
    ]
    results = []
    for label, p, ta in configs:
        r = run_one(sym, candles, cbtf, p, ta)
        if "error" in r:
            print(f"  [{label}] ERR {r['error']}")
            continue
        pnl = round(r["final"] - r["init_cash"], 2)
        row = {
            "label": label, "params": f"{p['periods']}×{p['multiplier']}",
            "gate": "MA30" if ta else "无",
            "trades": r["trades"], "pnl_u": pnl,
            "margin_roi_pct": round(pnl / sym["margin_usdt"] * 100, 1),
            "hold_pct": r["hold_pct"], "max_dd_pct": r["max_dd_pct"],
            "win_rate": r["win_rate"], "profit_factor": r["profit_factor"],
            "er_blocked": r["er_blocked"],
            "align_blocked": r.get("align_blocked", r.get("n_align_block", "?")),
            "tp1": r["tp1_count"], "tp2": r.get("tp2_count", 0),
            "tp3": r.get("tp3_count", 0), "stops": r["stop_count"],
            "reverses": r["reverse_count"],
        }
        results.append(row)
        print(f"  {label:<14} 交易 {row['trades']:>3}  净利润 {pnl:>6.2f}U  "
              f"ROI {row['margin_roi_pct']:>6.1f}%  胜率 {row['win_rate']:>5.1f}%  "
              f"PF {row['profit_factor']:>4.2f}  回撤 {row['max_dd_pct']:>5.2f}%  "
              f"评分拦 {row['er_blocked']} 方向拦 {row['align_blocked']}")

    print(f"\n  买入持有(基准): {results[0]['hold_pct']}%")
    out_path = os.path.join(os.path.dirname(__file__), "_live_cfg_btc_4way.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"  Wrote {out_path}")


if __name__ == "__main__":
    main()
