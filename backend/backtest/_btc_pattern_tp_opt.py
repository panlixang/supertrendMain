"""BTC-USDT-SWAP 形态识别页策略 · TP1 档位寻优。

信号侧完全对齐形态识别页实盘（pattern_trade.py）：
  - 基础信号 = 原始 SuperTrend（ATR 10 / factor 3.0），即 ST_PERIODS / ST_MULTIPLIER
  - 过滤 = block_4h（4h 形态方向反向拦截），用 run_backtest 的 trend_align 精确编码
  - 无其他闸门（无打分制 / 无 ER 闸门 / 无 range 过滤器）
出场侧 = position.ExitRules 默认档（TP1 平部分 + 保本 + 跟随 ST + 轨道无效硬止损），
只扫 tp1_pct / tp1_ratio 两档，其余字段保持线上默认（sl 2.0 / 保本 / 跟随 ST）。

评分：return_pct / max_dd_pct（与 _btc_tp_opt 同口径），并要求足够成交笔数。
"""
from __future__ import annotations

import bisect
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest
from indicators import super_trend
from pattern_recog import recognize as recognize_pattern
from position import ExitRules
from _live_cfg_backtest import BARS, BIAS_TFS, fetch_candles, ts_fmt

SYMBOL = "BTC-USDT-SWAP"
GATE_TF = "1h"
# 与早晨基准口径一致：2025-01-01 至今的 1h（≈630 天 ≈ 15100 根），多取一点冗余
BARS_N = 15600
MIN_TRADES = 15

ST_PERIODS = 10
ST_MULT = 3.0

# 与早晨基准口径一致：10U × 10x = 名义 100U/笔；收益率 = 名义收益率(pnl/名义本金)
LEV = 10
MARGIN = 10.0
NOTIONAL = MARGIN * LEV
SL_PCT = 2.0
MOVE_BE = True
TRAIL_ST = True

TP1_PCTS = [0.5, 0.8, 1.0, 1.2, 1.5, 1.8, 2.0, 2.5, 3.0, 4.0]
TP1_RATIOS = [20, 30, 40, 50, 60, 70, 80, 100]


def _block4h_align(candles_1h: list[dict], flips: list[dict],
                   candles_4h: list[dict]) -> dict[int, int]:
    """用 trend_align 编码 block_4h：1h 翻转 ts → 期望方向（与 run_backtest 口径一致）。

    block_4h 语义（见 pattern_trade._allow_by_4h）：只拦「4h 明确反向」，
    4h 无明显趋势 / 方向不清一律放行。所以：
      允许 → trend_align[ts] = sig_dir（让 run_backtest 的 aligned 判 True）
      拦截 → trend_align[ts] = -sig_dir（aligned 判 False，不开新仓）
    反向信号平旧仓在 run_backtest 里本就无条件，不受 trend_align 影响，天然一致。
    """
    if not candles_4h:
        return {}
    pat = recognize_pattern(
        [{"ts": c["ts"], "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"]}
         for c in candles_4h]
    )["pattern"]
    pts = [p["ts"] for p in pat]
    dir_by = {p["ts"]: p.get("dir") for p in pat}
    out: dict[int, int] = {}
    for f in flips:
        i = f["i"]
        if i >= len(candles_1h):
            continue
        ts = candles_1h[i]["ts"]
        sig_dir = 1 if f["type"] == "buy" else -1
        idx = bisect.bisect_right(pts, ts) - 1
        pdir = dir_by[pts[idx]] if idx >= 0 else None
        allowed = (pdir is None) or (pdir == 0) or (pdir == sig_dir)
        out[ts] = sig_dir if allowed else -sig_dir
    return out


def _rules(tp1_pct: float, tp1_ratio: float) -> ExitRules:
    return ExitRules(
        tp1_pct=tp1_pct, tp1_ratio=tp1_ratio,
        sl_mode="st", sl_pct=SL_PCT,
        move_sl_to_entry=MOVE_BE, trail_with_st=TRAIL_ST,
    )


def _params() -> dict:
    return {"periods": ST_PERIODS, "multiplier": ST_MULT,
            "src": "hl2", "change_atr": True}


def _summarize(r: dict) -> dict:
    pnl = round(r["final"] - r["init_cash"], 2)
    return {
        "pnl_u": pnl,
        "ret_pct": round(pnl / NOTIONAL * 100, 2),  # 名义收益率 = pnl/名义本金
        "max_dd_pct": r["max_dd_pct"],
        "trades": r["trades"],
        "win_rate": r["win_rate"],
        "profit_factor": r["profit_factor"],
        "avg_win": r["avg_win"], "avg_loss": r["avg_loss"],
        "tp1": r["tp1_count"], "stops": r["stop_count"],
        "reverses": r["reverse_count"],
    }


def _score(s: dict) -> float:
    if s["trades"] < MIN_TRADES or s["max_dd_pct"] <= 0:
        return -1e9
    return s["ret_pct"] / s["max_dd_pct"]


def main():
    print(f"=== {SYMBOL} {GATE_TF} 形态页 TP1 寻优 fetch ===", flush=True)
    candles = fetch_candles(SYMBOL, GATE_TF, BARS_N)
    if len(candles) < ST_PERIODS + 5:
        print("  K线不足", flush=True)
        return
    start, end = candles[0]["ts"], candles[-1]["ts"]
    print(f"  1h bars={len(candles)} {ts_fmt(start)} ~ {ts_fmt(end)}", flush=True)

    # 4h 形态方向（覆盖同区间，1h 15600 根 ≈ 4h 3900 根，多取一点）
    c4 = fetch_candles(SYMBOL, "4h", 4200)
    print(f"  4h bars={len(c4)} for block_4h", flush=True)

    # 1h 翻转信号（与 run_backtest 内部一致），用于编码 block_4h
    st1 = super_trend(
        [c["o"] for c in candles], [c["h"] for c in candles],
        [c["l"] for c in candles], [c["c"] for c in candles],
        periods=ST_PERIODS, multiplier=ST_MULT, change_atr=True,
    )
    flips = st1.get("flips") or []
    align = _block4h_align(candles, flips, c4)
    print(f"  信号翻转 {len(flips)} 个，其中被 block_4h 拦截 "
          f"{sum(1 for f in flips if align.get(candles[f['i']]['ts'], 0) == -(1 if f['type']=='buy' else -1))} 个",
          flush=True)

    p = _params()

    def run(tp1, ratio):
        return run_backtest(
            candles, p,
            init_cash=100.0, fee_rate=0.0005, allow_short=True,
            sizing="fixed", margin_usdt=MARGIN, leverage=LEV,
            exit_rules=_rules(tp1, ratio),
            trend_align=align,
        )

    # 当前线上默认档（tp1 1.5% / 70%）作为基准
    r0 = run(1.5, 70.0)
    s0 = _summarize(r0)
    print(f"  [当前线上 1.5%/70%] pnl={s0['pnl_u']}U ret={s0['ret_pct']}% "
          f"dd={s0['max_dd_pct']}% trades={s0['trades']} "
          f"wr={s0['win_rate']}% PF={s0['profit_factor']}", flush=True)

    combos = [(a, b) for a in TP1_PCTS for b in TP1_RATIOS]
    print(f"  grid {len(combos)} combos …", flush=True)
    rows = []
    t0 = time.time()
    for i, (a, b) in enumerate(combos, 1):
        r = run(a, b)
        if "error" in r:
            continue
        s = _summarize(r)
        s.update({"tp1_pct": a, "tp1_ratio": b, "score": round(_score(s), 3)})
        rows.append(s)
        if i % 40 == 0:
            print(f"  {i}/{len(combos)} …", flush=True)
    rows.sort(key=lambda x: x["score"], reverse=True)
    print(f"  grid done {len(rows)} rows in {time.time()-t0:.0f}s", flush=True)

    # 前后半段稳定性验证
    mid = len(candles) // 2
    c1, c2 = candles[:mid], candles[mid:]
    print("\n=== top8 前后半段稳定性 ===", flush=True)
    for t in rows[:8]:
        a, b = t["tp1_pct"], t["tp1_ratio"]
        r1b = run_backtest(
            c1, p, init_cash=100.0, fee_rate=0.0005, allow_short=True,
            sizing="fixed", margin_usdt=MARGIN, leverage=LEV,
            exit_rules=_rules(a, b), trend_align=align,
        )
        s1 = _summarize(r1b)
        r2b = run_backtest(
            c2, p, init_cash=100.0, fee_rate=0.0005, allow_short=True,
            sizing="fixed", margin_usdt=MARGIN, leverage=LEV,
            exit_rules=_rules(a, b), trend_align=align,
        )
        s2 = _summarize(r2b)
        t["half1"] = s1
        t["half2"] = s2
        t["stable"] = s1["pnl_u"] > 0 and s2["pnl_u"] > 0
        print(f"  {a}%/ {b}%: full={t['pnl_u']}U dd={t['max_dd_pct']}% "
              f"trades={t['trades']} | H1={s1['pnl_u']}U({s1['trades']}) "
              f"H2={s2['pnl_u']}U({s2['trades']}) stable={t['stable']}", flush=True)

    stable = [t for t in rows[:8] if t["stable"]]
    rec = (stable or rows)[0]
    out = {
        "symbol": SYMBOL, "tf": GATE_TF,
        "st": f"{ST_PERIODS}×{ST_MULT}", "filter": "block_4h=True",
        "start": ts_fmt(start), "end": ts_fmt(end), "bars": len(candles),
        "sizing": f"fixed margin={MARGIN}U lev={LEV} notional={NOTIONAL}U/笔, 收益率=名义收益率(pnl/名义)",
        "min_trades": MIN_TRADES,
        "current": {**s0, "tp1_pct": 1.5, "tp1_ratio": 70.0},
        "recommend": rec,
        "top8": rows[:8],
        "all": rows,
    }
    path = os.path.join(os.path.dirname(__file__), "_btc_pattern_tp_opt.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {path}")
    print("\n=== RECOMMEND ===")
    print(json.dumps(rec, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
