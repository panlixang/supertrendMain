# -*- coding: utf-8 -*-
"""ETH-USDT-SWAP 参数寻优：periods×multiplier、ER 档位、分数拦截。
出场规则固定为线上 ETH 增强档（tp1=0.8/30, tp2=2.0/40, tp3=3.5/100 reverse,
sl=st 3.0, max_loss=3.0）。分阶段贪心。

支持历史窗口 OOS 验证：
  python _sweep_eth.py --start 2025-09-01 --end 2026-03-02
不传参数 = 拉取最新 4500 根 1h。"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest  # noqa: E402
from position_enhanced import EnhancedExitRules  # noqa: E402
from regime import TradeConfig  # noqa: E402

from _live_cfg_backtest import (  # noqa: E402
    OKX, OKX_BAR, _get, fetch_candles, ts_fmt,
)

SYM = "ETH-USDT-SWAP"
TF = "1h"
BARS = 4500
BIAS_TFS = ["15m", "1h", "4h", "1d"]
BARS_BY_TF = {"15m": 9000, "1h": 4500, "4h": 4500, "1d": 4500}

# 线上 ETH 增强档出场规则（固定，不参与寻优）
ER = dict(
    enabled=True, tp1_pct=0.8, tp1_ratio=30.0, tp2_pct=2.0, tp2_ratio=40.0,
    tp3_pct=3.5, tp3_ratio=100.0, tp3_mode="reverse_signal",
    move_sl_to_entry=True, sl_mode="st", sl_pct=3.0, trail_with_st=True,
    sl_buffer_atr=0.5, sl_min_pct=1.2, protect_profit_at=1.5,
    protect_trail_pct=0.8, max_loss_enabled=True, max_loss_pct=3.0,
)

BASE_CFG = dict(
    enabled=True, leverage=10, amount_usdt=10.0, er_hide_below=0.1,
    er_weak_min=0.12, er_min=0.12, er_trend=0.3, quick_enabled=False,
    allow_grades=["A", "B", "C"], min_score=0, allow_tfs=[TF],
    cooldown_sec=300, atr_filter_enabled=False, atr_vol_min=0.7,
    range_filter_enabled=False, range_size_max=0.15, range_touches_min=3,
    mtf_filter_enabled=False, mtf_consistency_min=0.6, mtf_flip_max=5,
    adx_filter_enabled=False, adx_min=20.0, adx_period=14,
    use_scoring=True, scoring_full_threshold=35.0,
    scoring_half_threshold=35.0, scoring_alert_threshold=30.0,
    use_dynamic_threshold=True,
)


def parse_ms(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d")
               .replace(tzinfo=timezone.utc).timestamp() * 1000)


def fetch_window(symbol: str, tf: str, start_ms: int, end_ms: int,
                 limit: int) -> list[dict]:
    """从 end_ms 往前翻页抓取 [start_ms, end_ms] 窗口内的已收盘 K 线。"""
    collected: dict[int, dict] = {}
    after = end_ms
    while len(collected) < limit:
        page_n = min(300, limit - len(collected))
        qs = f"instId={symbol}&bar={OKX_BAR[tf]}&limit={page_n}&after={after}"
        data = None
        for base in OKX:
            try:
                data = _get(f"{base}/api/v5/market/history-candles?{qs}")
                if data and data.get("code") == "0" and data.get("data"):
                    break
            except Exception:
                continue
        if not data or data.get("code") != "0" or not data.get("data"):
            break
        rows = data["data"]
        hit_start = False
        for row in rows:
            if len(row) > 8 and row[8] != "1":
                continue
            ts = int(row[0])
            if ts > end_ms:
                continue
            if ts < start_ms:
                hit_start = True
                continue
            collected[ts] = {
                "ts": ts, "o": float(row[1]), "h": float(row[2]),
                "l": float(row[3]), "c": float(row[4]), "vol": float(row[5]),
            }
        after = rows[-1][0]
        if hit_start or len(rows) < page_n:
            break
        time.sleep(0.06)
    return [collected[k] for k in sorted(collected)]


def make_exit() -> EnhancedExitRules:
    return EnhancedExitRules(**ER)


def make_cfg(**over) -> TradeConfig:
    c = dict(BASE_CFG)
    c.update(over)
    return TradeConfig(**c)


def one(p: dict, cfg: TradeConfig, candles, cbtf) -> dict:
    r = run_backtest(
        candles, p, init_cash=100.0, fee_rate=0.0005, allow_short=True,
        exit_rules=make_exit(), sizing="fixed", margin_usdt=10.0, leverage=10,
        live_gate=cfg, gate_tf=TF, candles_by_tf=cbtf,
    )
    if "error" in r:
        return None
    return {
        "pnl": round(r["final"] - 100.0, 2),
        "trades": r["trades"], "wr": r["win_rate"], "pf": r["profit_factor"],
        "dd": r["max_dd_pct"], "blocked": r["er_blocked"],
    }


def show(tag: str, rows: list[dict], n: int = 12) -> None:
    print(f"\n=== {tag} TOP {n} ===", flush=True)
    for i, x in enumerate(sorted(rows, key=lambda r: r["pnl"], reverse=True)[:n], 1):
        print(
            f"  {i:2d}. p={x['params']} er={x['er']} score={x['score']} "
            f"| pnl={x['pnl']}U trades={x['trades']} wr={x['wr']}% "
            f"PF={x['pf']} dd={x['dd']}% blocked={x['blocked']}",
            flush=True,
        )


def _pf(x) -> float:
    return 999.0 if x["pf"] is None else x["pf"]


def rank(rows: list[dict], top: int) -> list[dict]:
    good = [r for r in rows if r["pnl"] > 0 and _pf(r) >= 1.2 and r["dd"] <= 15]
    good.sort(key=lambda r: (r["pnl"], _pf(r)), reverse=True)
    return good[:top] or sorted(rows, key=lambda r: (r["pnl"], _pf(r)),
                                reverse=True)[:top]


def main():
    args = sys.argv[1:]
    start_s = args[args.index("--start") + 1] if "--start" in args else None
    end_s = args[args.index("--end") + 1] if "--end" in args else None
    window_mode = bool(start_s and end_s)

    t0 = time.time()
    if window_mode:
        s_ms, e_ms = parse_ms(start_s), parse_ms(end_s)
        candles = fetch_window(SYM, TF, s_ms, e_ms, 5000)
        if not candles:
            print("窗口抓取失败", flush=True)
            return
        cbtf: dict[str, list] = {TF: candles}
        for t in BIAS_TFS:
            if t == TF:
                continue
            extra = fetch_window(SYM, t, s_ms, e_ms, BARS_BY_TF[t])
            if extra:
                cbtf[t] = extra
        print(f"K线就绪(窗口): {TF}={len(candles)} 根, "
              f"{ts_fmt(candles[0]['ts'])} ~ {ts_fmt(candles[-1]['ts'])}", flush=True)
    else:
        candles = fetch_candles(SYM, TF, BARS)
        if not candles:
            print("拉取 K 线失败", flush=True)
            return
        cbtf: dict[str, list] = {TF: candles}
        for t in BIAS_TFS:
            if t == TF:
                continue
            extra = fetch_candles(SYM, t, BARS_BY_TF[t])
            if extra:
                cbtf[t] = extra
        print(f"K线就绪: {TF}={len(candles)} 根, "
              f"{ts_fmt(candles[0]['ts'])} ~ {ts_fmt(candles[-1]['ts'])}", flush=True)

    # 基线：线上当前配置 7×4.0 / er 0.12/0.12/0.3 / scoring 35/35/30 m0
    base = one({"periods": 7, "multiplier": 4.0, "src": "hl2", "change_atr": True,
                "fast_len": 20, "slow_len": 50, "ma_type": "EMA"},
               make_cfg(), candles, cbtf)
    if base:
        print(f"\n[线上当前配置基线] pnl={base['pnl']}U trades={base['trades']} "
              f"wr={base['wr']}% PF={base['pf']} dd={base['dd']}%", flush=True)

    # ── 阶段1：periods × multiplier ──
    periods = list(range(5, 21, 2))        # 5,7,...,19（含当前7）
    mults = [2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0]
    rows1 = []
    for per in periods:
        for mul in mults:
            p = {"periods": per, "multiplier": mul, "src": "hl2",
                 "change_atr": True, "fast_len": 20, "slow_len": 50, "ma_type": "EMA"}
            r = one(p, make_cfg(), candles, cbtf)
            if r:
                r.update(params=f"{per}×{int(mul)}", er="0.12/0.12/0.3",
                         score="d:35/35/30 m0")
                rows1.append(r)
    show("阶段1 params", rows1)
    top1 = rank(rows1, 8)

    # ── 阶段2：ER 档位 ──
    rows2 = []
    for base_ in top1:
        per, mul = (int(x) for x in base_["params"].split("×"))
        p = {"periods": per, "multiplier": mul, "src": "hl2",
             "change_atr": True, "fast_len": 20, "slow_len": 50, "ma_type": "EMA"}
        for er_min in (0.10, 0.12, 0.15, 0.20, 0.25):
            for er_weak in (0.10, 0.12, 0.15):
                if er_weak > er_min:
                    continue
                for er_trend in (0.25, 0.35):
                    r = one(p, make_cfg(er_min=er_min, er_weak_min=er_weak,
                                        er_trend=er_trend), candles, cbtf)
                    if r:
                        r.update(params=base_["params"],
                                 er=f"{er_min}/{er_weak}/{er_trend}",
                                 score="d:35/35/30 m0")
                        rows2.append(r)
    show("阶段2 ER", rows2)
    top2 = rank(rows2, 5)

    # ── 阶段3：分数拦截（min_score + scoring 阈值）──
    rows3 = []
    for base_ in top2:
        per, mul = (int(x) for x in base_["params"].split("×"))
        er_min, er_weak, er_trend = (float(x) for x in base_["er"].split("/"))
        p = {"periods": per, "multiplier": mul, "src": "hl2",
             "change_atr": True, "fast_len": 20, "slow_len": 50, "ma_type": "EMA"}
        for m_score in (0, 1, 2):
            for full in (35.0, 45.0, 60.0):
                for half in (30.0, 35.0, 45.0):
                    for alert in (25.0, 30.0, 35.0):
                        r = one(p, make_cfg(er_min=er_min, er_weak_min=er_weak,
                                            er_trend=er_trend, min_score=m_score,
                                            scoring_full_threshold=full,
                                            scoring_half_threshold=half,
                                            scoring_alert_threshold=alert),
                                candles, cbtf)
                        if r:
                            r.update(params=base_["params"], er=base_["er"],
                                     score=f"d:{full:g}/{half:g}/{alert:g} m{m_score}")
                            rows3.append(r)
    show("阶段3 分数拦截", rows3)

    final = sorted(rows3, key=lambda x: (x["pnl"], _pf(x)), reverse=True)
    print(f"\n=== ETH 最优 TOP 15 ===", flush=True)
    for i, x in enumerate(final[:15], 1):
        flag = " <样本少>" if x["trades"] < 10 else ""
        print(
            f"  {i:2d}. p={x['params']} er={x['er']} score={x['score']} "
            f"| pnl={x['pnl']}U trades={x['trades']} wr={x['wr']}% "
            f"PF={x['pf']} dd={x['dd']}%{flag}",
            flush=True,
        )

    tag = f"_oos_{start_s.replace('-', '')}_{end_s.replace('-', '')}" if window_mode else ""
    out = os.path.join(os.path.dirname(__file__), f"_sweep_eth{tag}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"window": f"{start_s}~{end_s}" if window_mode else "latest",
                   "baseline": base, "stage1": rows1, "stage2": rows2,
                   "stage3": rows3}, f, ensure_ascii=False, indent=1)
    print(f"\nWrote {out} | 总耗时 {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
