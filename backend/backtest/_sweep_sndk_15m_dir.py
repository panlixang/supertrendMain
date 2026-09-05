# -*- coding: utf-8 -*-
"""SNDK 15m 近半年回测：验证「看 1h/4h 周期信号方向，15m 开仓，只开同向」的收益/胜率。

流程：
  A. 15m 参数寻优（periods×multiplier，纯翻转 + 线上 SNDK 增强出场档），近半年窗口。
  B. 对 TOP 参数施加高周期方向过滤，多模式对比：
       none    无过滤（全部 15m 翻转都开）
       h1      只开与 1h 方向同向的 15m 信号
       h4      只开与 4h 方向同向的 15m 信号
       h1h4    与 1h 且 4h 方向都同向（严格）
       h1or4   至少一个高周期同向才开（1h/4h 冲突时全开）

高周期方向取“信号判定时刻已收盘的最近一根”的 supertrend 方向（ts+tf<=信号收盘时刻，
无未来泄漏）。出场规则固定线上 SNDK 增强档。"""
from __future__ import annotations

import bisect
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest  # noqa: E402
from position_enhanced import EnhancedExitRules  # noqa: E402
from indicators import super_trend  # noqa: E402
from _live_cfg_backtest import fetch_candles  # noqa: E402

SYM = "SNDK-USDT-SWAP"
MS15, MS1H, MS4H = 15 * 60 * 1000, 3600 * 1000, 4 * 3600 * 1000
BARS15 = 17600          # 近半年 ≈ 183 天 × 96 根
BARS1H = 5000           # 覆盖全程 + warmup
BARS4H = 1500

# 线上 SNDK 增强档出场规则（固定，不参与寻优）
ER = dict(
    enabled=True, tp1_pct=2.0, tp1_ratio=30.0, tp2_pct=2.5, tp2_ratio=40.0,
    tp3_pct=3.5, tp3_ratio=100.0, tp3_mode="reverse_signal",
    move_sl_to_entry=True, sl_mode="st", sl_pct=3.0, trail_with_st=True,
    sl_buffer_atr=0.5, sl_min_pct=1.2, protect_profit_at=1.5,
    protect_trail_pct=0.8, max_loss_enabled=True, max_loss_pct=3.0,
)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def ts_fmt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def make_exit() -> EnhancedExitRules:
    return EnhancedExitRules(**ER)


def one(candles, p, align_map=None):
    r = run_backtest(
        candles, p, init_cash=100.0, fee_rate=0.0005, allow_short=True,
        exit_rules=make_exit(), sizing="fixed", margin_usdt=10.0, leverage=10,
        trend_align=align_map,
    )
    if "error" in r:
        return None
    return {
        "pnl": round(r["final"] - 100.0, 2), "trades": r["trades"],
        "wr": r["win_rate"], "pf": r["profit_factor"], "dd": r["max_dd_pct"],
        "blocked": r["er_blocked"], "align_blocked": r["align_blocked"],
        "avg_win": r["avg_win"], "avg_loss": r["avg_loss"],
        "start": ts_fmt(r["start_ts"]), "end": ts_fmt(r["end_ts"]),
    }


def st_dirs(ht_candles: list[dict], tf_ms: int, p: dict) -> tuple[list, list]:
    """高周期每根收盘时刻 ends 与对应收盘后的趋势方向。"""
    st = super_trend(
        [c["o"] for c in ht_candles], [c["h"] for c in ht_candles],
        [c["l"] for c in ht_candles], [c["c"] for c in ht_candles],
        periods=p["periods"], multiplier=p.get("multiplier", 3.0),
        src=p.get("src", "hl2"), change_atr=p.get("change_atr", True),
    )
    ends = [c["ts"] + tf_ms for c in ht_candles]
    return ends, st["trend"]


def dir_at(ends: list, dirs: list, ts15: int) -> int | None:
    """信号(ts15 收盘判定)时刻最近已收盘高周期方向。"""
    k = bisect.bisect_right(ends, ts15 + MS15) - 1
    if k < 0:
        return None
    return dirs[k]


def build_maps(ends1, dirs1, ends4, dirs4, sigs):
    maps = {"h1": {}, "h4": {}, "h1h4": {}, "h1or4": {}}
    for ts in sigs:
        d1 = dir_at(ends1, dirs1, ts)
        d4 = dir_at(ends4, dirs4, ts)
        if d1 in (1, -1):
            maps["h1"][ts] = d1
        if d4 in (1, -1):
            maps["h4"][ts] = d4
        # 严格双同向：方向冲突或缺失按 0（全拦）
        if d1 == 1 and d4 == 1:
            maps["h1h4"][ts] = 1
        elif d1 == -1 and d4 == -1:
            maps["h1h4"][ts] = -1
        elif d1 is not None and d4 is not None:
            maps["h1h4"][ts] = 0
        elif d1 is not None:
            maps["h1h4"][ts] = d1
        elif d4 is not None:
            maps["h1h4"][ts] = d4
        # 至少一同向：只拦“双周期都反向”；冲突/缺失周期不算拦
        if d1 is not None and d4 is not None and d1 == d4:
            maps["h1or4"][ts] = d1
        elif d1 is not None and d4 is None:
            maps["h1or4"][ts] = d1
        elif d4 is not None and d1 is None:
            maps["h1or4"][ts] = d4
    return maps


def show(title: str, rows: list[dict]) -> None:
    print(f"\n=== {title} ===", flush=True)
    print("  #  param  mode   | trades  wr%    pnlU    PF     dd%   avgWin% avgLoss%", flush=True)
    for i, x in enumerate(rows, 1):
        flag = " <样本少>" if x["trades"] < 15 else ""
        print(
            f"  {i:2d} {x['param']:<6} {x['mode']:<5} | {x['trades']:5d} {x['wr']:6.1f}"
            f" {x['pnl']:7.2f} {x['pf'] if x['pf'] is not None else 0:6.2f}"
            f" {x['dd']:6.2f} {x['avg_win']:7.2f} {x['avg_loss']:8.2f}{flag}",
            flush=True,
        )


def main():
    t0 = time.time()
    candles15 = fetch_candles(SYM, "15m", BARS15)
    candles1h = fetch_candles(SYM, "1h", BARS1H)
    candles4h = fetch_candles(SYM, "4h", BARS4H)
    if not candles15 or not candles1h or not candles4h:
        print("K 线拉取失败", flush=True)
        return
    print(f"15m={len(candles15)}根 {ts_fmt(candles15[0]['ts'])}~{ts_fmt(candles15[-1]['ts'])} "
          f"1h={len(candles1h)} 4h={len(candles4h)}", flush=True)

    # ── A. 15m 参数寻优（无方向过滤） ──
    rows1 = []
    periods = list(range(7, 29, 2))
    mults = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0, 10.0]
    for per in periods:
        for mul in mults:
            p = {"periods": per, "multiplier": mul, "src": "hl2",
                 "change_atr": True, "fast_len": 20, "slow_len": 50,
                 "ma_type": "EMA"}
            r = one(candles15, p)
            if r:
                r.update(param=f"{per}×{mul:g}", mode="none")
                rows1.append(r)
    def _pf(x):
        return 999.0 if x["pf"] is None else x["pf"]
    good = [r for r in rows1 if r["pnl"] > 0 and _pf(r) >= 1.2 and r["dd"] <= 15]
    good.sort(key=lambda r: (r["pnl"], _pf(r)), reverse=True)
    top = (good or sorted(rows1, key=lambda r: (r["pnl"], _pf(r)),
                          reverse=True))[:3]
    print("\n=== 15m 无过滤寻优 TOP 12（近半年） ===", flush=True)
    for i, x in enumerate((good or sorted(rows1, key=lambda r: (r["pnl"], _pf(r)),
                                          reverse=True))[:12], 1):
        flag = " <样本少>" if x["trades"] < 15 else ""
        print(f"  {i:2d}. {x['param']} | pnl={x['pnl']}U trades={x['trades']} "
              f"wr={x['wr']}% PF={x['pf']} dd={x['dd']}%{flag}", flush=True)

    # ── B. 高周期方向同向过滤对比 ──
    out = []
    for t in top:
        per, mul = (float(x) for x in t["param"].split("×"))
        p = {"periods": int(per), "multiplier": mul, "src": "hl2",
             "change_atr": True, "fast_len": 20, "slow_len": 50, "ma_type": "EMA"}
        st15 = super_trend(
            [c["o"] for c in candles15], [c["h"] for c in candles15],
            [c["l"] for c in candles15], [c["c"] for c in candles15],
            periods=int(per), multiplier=mul, src="hl2", change_atr=True)
        sigs = [candles15[f["i"]]["ts"] for f in (st15.get("flips") or [])]
        ends1, dirs1 = st_dirs(candles1h, MS1H, p)
        ends4, dirs4 = st_dirs(candles4h, MS4H, p)
        maps = build_maps(ends1, dirs1, ends4, dirs4, sigs)
        for mode, m in [("none", None), ("h1", maps["h1"]),
                        ("h4", maps["h4"]), ("h1h4", maps["h1h4"]),
                        ("h1or4", maps["h1or4"])]:
            r = one(candles15, p, m)
            if r is None:
                continue
            r.update(param=t["param"], mode=mode)
            out.append(r)
    show("15m 寻优 TOP3 × 方向模式 对比（近半年）", out)

    res = {"candles15_start": candles15[0]["ts"], "candles15_end": candles15[-1]["ts"],
           "top": top, "rows": out}
    op = os.path.join(os.path.dirname(__file__), "_sweep_sndk_15m_dir.json")
    with open(op, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(f"\nWrote {op} | 耗时 {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
