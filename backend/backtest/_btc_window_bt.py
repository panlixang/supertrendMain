"""BTC 指定历史窗口回测：2025-09-03 ~ 2026-03-03。

口径对比：
  - 改前：er_min=0.15 + 弱档quick规则(0.8%全平) + scoring 60（线上真实口径）
  - 改后：方案A er_min=0.12（弱档并入正常档） + scoring 50（当前线上）
ST 均为 11×4.0。
数据：OKX 在线拉取窗口 + 约 3 周预热（多周期 15m/1h/4h/1d）。
统计：只算 entry_ts 落在窗口内的交易。
"""
from __future__ import annotations

import copy
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest
from _btc_score_opt import score_only_cfg
from _live_cfg_backtest import LIVE_URL, exit_rules, _get

GATE_TF = "1h"
OKX = ["https://www.okx.com", "https://aws.okx.com"]
OKX_BAR = {"15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D"}
BIAS_TFS = ["15m", "1h", "4h", "1d"]

# 窗口
W_START_MS = int(datetime(2025, 9, 3, tzinfo=timezone.utc).timestamp() * 1000)
W_END_MS = int(datetime(2026, 3, 3, tzinfo=timezone.utc).timestamp() * 1000)
# 预热：窗口前多拉 21 天
PRE_MS = int(datetime(2025, 8, 13, tzinfo=timezone.utc).timestamp() * 1000)
DATA_END_MS = int(datetime(2026, 3, 8, tzinfo=timezone.utc).timestamp() * 1000)  # 末尾留 5 天让最后持仓自然离场


def _get(url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers={"User-Agent": "supertrend-bt/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def fetch_range(symbol: str, tf: str, start_ms: int, end_ms: int) -> list[dict]:
    """拉 [start_ms, end_ms] 区间 K 线（OKX history-candles 从新往旧翻页）。"""
    bar = OKX_BAR[tf]
    collected: dict[int, dict] = {}
    after = end_ms
    while True:
        qs = f"instId={symbol}&bar={bar}&limit=300"
        if after:
            qs += f"&after={after}"
        data = None
        for base in OKX:
            try:
                data = _get(f"{base}/api/v5/market/history-candles?{qs}")
                if data and data.get("code") == "0":
                    break
            except Exception:
                continue
        if not data or data.get("code") != "0" or not data.get("data"):
            break
        rows = data["data"]
        got = False
        for row in rows:
            try:
                ts = int(row[0])
                if ts < start_ms:
                    continue
                if ts > end_ms:
                    continue
                got = True
                collected[ts] = {
                    "ts": ts, "o": float(row[1]), "h": float(row[2]),
                    "l": float(row[3]), "c": float(row[4]), "vol": float(row[5]),
                }
            except (IndexError, ValueError):
                continue
        oldest = min(int(r[0]) for r in rows)
        if oldest < start_ms or len(rows) < 300:
            break
        after = rows[-1][0]
        time.sleep(0.06)
    out = [collected[k] for k in sorted(collected)]
    if out and out[0]["ts"] > start_ms + 3600_000 * 4:
        print(f"  ! {tf}: 只拉到 {ts_fmt(out[0]['ts'])}，可能不足", flush=True)
    return out


def ts_fmt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def btc_sym(live: dict) -> dict:
    for s in live["symbols"]:
        if "BTC" in s["symbol"]:
            return copy.deepcopy(s)
    raise RuntimeError("no BTC")


def exit_rules_quick(sym: dict):
    from position_enhanced import EnhancedExitRules
    r = sym["exit_rules_quick"]
    return EnhancedExitRules(
        enabled=r.get("enabled", True),
        tp1_pct=r["tp1_pct"], tp1_ratio=r["tp1_ratio"],
        tp2_pct=r.get("tp2_pct", 100.0), tp2_ratio=r.get("tp2_ratio", 0.0),
        tp3_pct=r.get("tp3_pct", 100.0), tp3_ratio=r.get("tp3_ratio", 0.0),
        tp3_mode=r.get("tp3_mode", "pct"),
        move_sl_to_entry=r.get("move_sl_to_entry", False),
        sl_mode=r.get("sl_mode", "st"), sl_pct=r.get("sl_pct", 1.0),
        trail_with_st=r.get("trail_with_st", False),
        sl_buffer_atr=r.get("sl_buffer_atr", 0.3),
        sl_min_pct=r.get("sl_min_pct", 1.0),
        protect_profit_at=r.get("protect_profit_at", 50.0),
        protect_trail_pct=r.get("protect_trail_pct", 0.0),
        max_loss_enabled=r.get("max_loss_enabled", False),
        max_loss_pct=r.get("max_loss_pct", 10.0),
    )


def run_cfg(sym: dict, cbtf: dict, er_min: float, thr: float, with_quick: bool) -> dict:
    p = {**sym["params"]}
    cfg = score_only_cfg(GATE_TF)
    cfg.er_min = er_min
    cfg.er_weak_min = 0.12
    cfg.quick_enabled = False
    cfg.use_dynamic_threshold = sym.get("use_dynamic_threshold", True)
    cfg.er_hide_below = sym.get("er_hide_below", 0.0)
    kwargs = dict(
        candles=cbtf[GATE_TF], p=p,
        init_cash=100.0, fee_rate=0.0005, allow_short=True,
        exit_rules=exit_rules(sym),
        sizing="fixed", margin_usdt=sym["margin_usdt"], leverage=sym["leverage"],
        live_gate=cfg, gate_tf=GATE_TF, candles_by_tf=cbtf,
        score_only_gate=True, min_total_score=float(thr),
    )
    if with_quick:
        kwargs["exit_rules_quick"] = exit_rules_quick(sym)
    return run_backtest(**kwargs)


def window_stats(r: dict, w_start: int, w_end: int) -> dict:
    """统计 entry_ts 在窗口内的交易；回撤按窗口内平仓逐笔重算。"""
    trades = [t for t in r.get("trade_list", []) if w_start <= t["entry_ts"] < w_end]
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    pnl = sum(t["pnl"] for t in trades)
    gain = sum(t["pnl"] for t in wins)
    loss = abs(sum(t["pnl"] for t in losses))
    eq = 100.0
    peak = eq
    max_dd = 0.0
    for t in sorted(trades, key=lambda x: x["entry_ts"]):
        eq += t["pnl"]
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak * 100)
    return {
        "pnl": round(pnl, 2),
        "trades": len(trades),
        "wins": len(wins),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
        "pf": round(gain / loss, 2) if loss > 0 else None,
        "max_dd": round(max_dd, 2),
        "avg_bars": round(sum(t["bars"] for t in trades) / len(trades), 1) if trades else 0,
        "stop": sum(1 for t in trades if t.get("reason") == "止损"),
        "tp1": sum(1 for t in trades if t.get("reason", "").startswith("止盈")),
        "open": sum(1 for t in trades if t.get("open")),
    }


def main():
    print(f"=== BTC 历史窗口 {ts_fmt(W_START_MS)} ~ {ts_fmt(W_END_MS)} ===", flush=True)
    live = _get(LIVE_URL)
    sym = btc_sym(live)
    print(f"  线上 params: {sym['params']}  er_min={sym.get('er_min')}  "
          f"scoring={sym.get('scoring_full_threshold')}", flush=True)

    # 拉数据（含预热）
    cbtf: dict[str, list] = {}
    for tf in BIAS_TFS:
        arr = fetch_range(sym["symbol"], tf, PRE_MS, DATA_END_MS)
        cbtf[tf] = arr
        print(f"  {tf}: {len(arr)} 根  {ts_fmt(arr[0]['ts'])} ~ {ts_fmt(arr[-1]['ts'])}", flush=True)

    r_old = run_cfg(sym, cbtf, 0.15, 60.0, with_quick=True)   # 改前（真实线上口径）
    r_new = run_cfg(sym, cbtf, 0.12, 50.0, with_quick=False)  # 改后（方案A，当前线上）

    print(f"\n{'口径':30s} {'pnl':>8s} {'trades':>4s} {'wr':>6s} {'PF':>6s} {'dd':>6s} "
          f"{'avg_bars':>6s} {'stop':>4s} {'tp':>4s} {'open':>4s}", flush=True)
    for label, r in (("改前 ER0.15+quick+score60", r_old),
                     ("改后 ER0.12 并入+score50(线上)", r_new)):
        if "error" in r:
            print(f"  {label:30s} ERROR: {r['error']}", flush=True)
            continue
        s = window_stats(r, W_START_MS, W_END_MS)
        pf = "inf" if s["pf"] is None else f"{s['pf']:.2f}"
        print(f"  {label:30s} {s['pnl']:8.2f} {s['trades']:4d} {s['win_rate']:5.1f}% {pf:>6s} "
              f"{s['max_dd']:6.2f} {s['avg_bars']:6.1f} {s['stop']:4d} {s['tp1']:4d} {s['open']:4d}", flush=True)

    # 明细
    for label, r in (("改前", r_old), ("改后", r_new)):
        if "error" in r:
            continue
        ws = [t for t in r.get("trade_list", []) if W_START_MS <= t["entry_ts"] < W_END_MS]
        print(f"\n--- {label} 窗口内 {len(ws)} 笔明细 ---", flush=True)
        for t in sorted(ws, key=lambda x: x["entry_ts"]):
            open_m = " [未平]" if t.get("open") else ""
            print(f"  {ts_fmt(t['entry_ts'])} {t['side']:5s} pnl={t['pnl']:7.2f} "
                  f"bars={t['bars']:3d} {t.get('reason','')}{open_m}", flush=True)

    out = Path(__file__).parent / "_btc_window_result.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "window": [ts_fmt(W_START_MS), ts_fmt(W_END_MS)],
            "old": window_stats(r_old, W_START_MS, W_END_MS) if "error" not in r_old else {"error": r_old["error"]},
            "new": window_stats(r_new, W_START_MS, W_END_MS) if "error" not in r_new else {"error": r_new["error"]},
        }, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {out}", flush=True)


if __name__ == "__main__":
    main()
