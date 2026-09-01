# -*- coding: utf-8 -*-
"""BTC-USDT-SWAP 完整寻优：periods×multiplier、ER 档位、分数拦截、TP/SL 出场。
K线半年（2026-03-01 ~ 2026-09-01）。分阶段贪心。
信号侧对齐线上：1h、11×4.0、ER 0.12/0.12/0.3、打分制 50/50/55 动态、min_score=1、
range 过滤器(touches>=2)、10U×10x。出场固定为线上 BTC 增强档（tp1=0.5/30,
tp2=1.5/40, tp3=3.0/100 reverse, sl=st 3.0, max_loss=3.0）。"""
from __future__ import annotations

import json
import os
import re
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

SYM = "BTC-USDT-SWAP"
TF = "1h"
BARS = 4380                       # 半年 1h
YEAR_START, YEAR_END = "2026-03-01", "2026-09-01"
BIAS_TFS = ["15m", "1h", "4h", "1d"]
BARS_BY_TF = {"15m": 17520, "1h": 4380, "4h": 1095, "1d": 183}
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "_live_data", "btc_half_cache.json")

# 线上 BTC 增强档出场规则（TP/SL 阶段可 override）
ER = dict(
    enabled=True, tp1_pct=0.5, tp1_ratio=30.0, tp2_pct=1.5, tp2_ratio=40.0,
    tp3_pct=3.0, tp3_ratio=100.0, tp3_mode="reverse_signal",
    move_sl_to_entry=True, sl_mode="st", sl_pct=3.0, trail_with_st=True,
    sl_buffer_atr=0.5, sl_min_pct=1.2, protect_profit_at=1.5,
    protect_trail_pct=0.8, max_loss_enabled=True, max_loss_pct=3.0,
)

BASE_CFG = dict(
    enabled=True, leverage=10, amount_usdt=10.0, er_hide_below=0.1,
    er_weak_min=0.12, er_min=0.12, er_trend=0.3, quick_enabled=False,
    allow_grades=["A", "B", "C"], min_score=1, allow_tfs=[TF],
    cooldown_sec=300, atr_filter_enabled=False, atr_vol_min=0.7,
    range_filter_enabled=True, range_size_max=0.15, range_touches_min=2,
    mtf_filter_enabled=False, mtf_consistency_min=0.6, mtf_flip_max=5,
    adx_filter_enabled=False, adx_min=20.0, adx_period=14,
    use_scoring=True, scoring_full_threshold=50.0,
    scoring_half_threshold=50.0, scoring_alert_threshold=55.0,
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


def make_exit(**over) -> EnhancedExitRules:
    return EnhancedExitRules(**{**ER, **over})


def make_cfg(**over) -> TradeConfig:
    c = dict(BASE_CFG)
    c.update(over)
    return TradeConfig(**c)


def one(p: dict, cfg: TradeConfig, candles, cbtf, er_over: dict | None = None) -> dict:
    r = run_backtest(
        candles, p, init_cash=100.0, fee_rate=0.0005, allow_short=True,
        exit_rules=make_exit(**(er_over or {})), sizing="fixed",
        margin_usdt=10.0, leverage=10,
        live_gate=cfg, gate_tf=TF, candles_by_tf=cbtf,
    )
    if "error" in r:
        return None
    return {
        "pnl": round(r["final"] - 100.0, 2),
        "trades": r["trades"], "wr": r["win_rate"], "pf": r["profit_factor"],
        "dd": r["max_dd_pct"], "blocked": r["er_blocked"],
    }


def parse_tag(tag: str) -> dict:
    out = {}
    m = re.search(r"p=([\d.]+)×([\d.]+)", tag)
    out["per"] = int(float(m.group(1)))
    out["mul"] = float(m.group(2))
    m = re.search(r"er=([\d./]+)", tag)
    emin, ew, et = m.group(1).split("/")
    out.update(er_min=float(emin), er_weak=float(ew), er_trend=float(et))
    m = re.search(r"score=d:([\d.]+)/([\d.]+)/([\d.]+) m(\d)", tag)
    out.update(full=float(m.group(1)), half=float(m.group(2)),
               alert=float(m.group(3)), m_score=int(m.group(4)))
    m = re.search(r"tp=([\d.]+)/([\d.]+)/([\d.]+)", tag)
    if m:
        out.update(tp1=float(m.group(1)), tp2=float(m.group(2)), tp3=float(m.group(3)))
    m = re.search(r"sl=([\d.]+) ml=([\d.]+)", tag)
    if m:
        out.update(sl=float(m.group(1)), ml=float(m.group(2)))
    return out


def show(tag: str, rows: list[dict], n: int = 12) -> None:
    print(f"\n=== {tag} TOP {n} ===", flush=True)
    for i, x in enumerate(sorted(rows, key=lambda r: r["pnl"], reverse=True)[:n], 1):
        print(
            f"  {i:2d}. {x['tag']} "
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
    t0 = time.time()
    s_ms, e_ms = parse_ms(YEAR_START), parse_ms(YEAR_END)

    # ── 带本地缓存的数据加载（二次运行免抓取）──
    cbtf: dict[str, list] = {}
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            cached = json.load(f)
        cbtf = {t: cached[t] for t in BIAS_TFS if t in cached}
        print(f"使用缓存 K 线 {CACHE_PATH}", flush=True)
    if not cbtf:
        candles = fetch_window(SYM, TF, s_ms, e_ms, BARS)
        if not candles:
            print("抓取 K 线失败", flush=True)
            return
        cbtf = {TF: candles}
        for t in BIAS_TFS:
            if t == TF:
                continue
            extra = fetch_window(SYM, t, s_ms, e_ms, BARS_BY_TF[t])
            if extra:
                cbtf[t] = extra
        try:
            os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(cbtf, f)
        except Exception as e:
            print(f"缓存写盘失败: {e}", flush=True)
    candles = cbtf[TF]
    print(f"K线就绪: {TF}={len(candles)} 根, "
          f"{ts_fmt(candles[0]['ts'])} ~ {ts_fmt(candles[-1]['ts'])}", flush=True)

    # 基线：线上当前 11×4.0 / er 0.12/0.12/0.3 / scoring 50/50/55 m1 / tp 0.5/1.5/3.0 sl3 ml3
    base = one({"periods": 11, "multiplier": 4.0, "src": "hl2", "change_atr": True,
                "fast_len": 20, "slow_len": 50, "ma_type": "EMA"},
               make_cfg(), candles, cbtf)
    if base:
        print(f"\n[线上当前配置基线] pnl={base['pnl']}U trades={base['trades']} "
              f"wr={base['wr']}% PF={base['pf']} dd={base['dd']}%", flush=True)

    # ── 阶段1：periods × multiplier ──
    periods = list(range(7, 20, 2))        # 7,9,11,13,15,17,19（含当前11）
    mults = [2.5, 3.0, 4.0, 5.0, 6.0, 8.0]
    rows1 = []
    for per in periods:
        for mul in mults:
            p = {"periods": per, "multiplier": mul, "src": "hl2",
                 "change_atr": True, "fast_len": 20, "slow_len": 50, "ma_type": "EMA"}
            r = one(p, make_cfg(), candles, cbtf)
            if r:
                r.update(tag=f"p={per}×{mul:g} er=0.12/0.12/0.3 score=d:50/50/55 m1")
                rows1.append(r)
    show("阶段1 params", rows1)
    top1 = rank(rows1, 6)

    # ── 阶段2：ER 档位 ──
    rows2 = []
    for base_ in top1:
        t = parse_tag(base_["tag"])
        p = {"periods": t["per"], "multiplier": t["mul"], "src": "hl2",
             "change_atr": True, "fast_len": 20, "slow_len": 50, "ma_type": "EMA"}
        for er_min in (0.12, 0.15, 0.20, 0.25):
            for er_weak in (0.08, 0.10, 0.12, 0.15):
                if er_weak > er_min:
                    continue
                for er_trend in (0.25, 0.35):
                    r = one(p, make_cfg(er_min=er_min, er_weak_min=er_weak,
                                        er_trend=er_trend), candles, cbtf)
                    if r:
                        r.update(tag=f"p={t['per']}×{t['mul']:g} "
                                     f"er={er_min:g}/{er_weak:g}/{er_trend:g} "
                                     f"score=d:50/50/55 m1")
                        rows2.append(r)
    show("阶段2 ER", rows2)
    top2 = rank(rows2, 4)

    # ── 阶段3：分数拦截（min_score + scoring 阈值）──
    rows3 = []
    for base_ in top2:
        t = parse_tag(base_["tag"])
        p = {"periods": t["per"], "multiplier": t["mul"], "src": "hl2",
             "change_atr": True, "fast_len": 20, "slow_len": 50, "ma_type": "EMA"}
        for m_score in (0, 1, 2):
            for full in (40.0, 50.0, 65.0):
                for half in (40.0, 50.0, 65.0):
                    for alert in (40.0, 50.0, 60.0):
                        r = one(p, make_cfg(er_min=t["er_min"], er_weak_min=t["er_weak"],
                                            er_trend=t["er_trend"], min_score=m_score,
                                            scoring_full_threshold=full,
                                            scoring_half_threshold=half,
                                            scoring_alert_threshold=alert),
                                candles, cbtf)
                        if r:
                            r.update(tag=f"p={t['per']}×{t['mul']:g} "
                                         f"er={t['er_min']:g}/{t['er_weak']:g}/{t['er_trend']:g} "
                                         f"score=d:{full:g}/{half:g}/{alert:g} m{m_score}")
                            rows3.append(r)
    show("阶段3 分数拦截", rows3)
    top3 = rank(rows3, 4)

    # ── 阶段4：TP 幅度（tp1×tp2×tp3，a<b<c，ratio 固定 30/40/100，sl3/ml3）──
    tp1s = [0.5, 0.8, 1.0, 1.2, 1.5]
    tp2s = [1.5, 2.0, 2.5, 3.0]
    tp3s = [3.0, 3.5, 4.0, 4.5]
    rows4 = []
    combos = [(a, b, c) for a in tp1s for b in tp2s for c in tp3s if a < b < c]
    for base_ in top3:
        t = parse_tag(base_["tag"])
        p = {"periods": t["per"], "multiplier": t["mul"], "src": "hl2",
             "change_atr": True, "fast_len": 20, "slow_len": 50, "ma_type": "EMA"}
        cfg = make_cfg(er_min=t["er_min"], er_weak_min=t["er_weak"],
                       er_trend=t["er_trend"], min_score=t["m_score"],
                       scoring_full_threshold=t["full"],
                       scoring_half_threshold=t["half"],
                       scoring_alert_threshold=t["alert"])
        for a, b, c in combos:
            r = one(p, cfg, candles, cbtf,
                    er_over={"tp1_pct": a, "tp2_pct": b, "tp3_pct": c})
            if r:
                r.update(tag=f"p={t['per']}×{t['mul']:g} "
                             f"er={t['er_min']:g}/{t['er_weak']:g}/{t['er_trend']:g} "
                             f"score=d:{t['full']:g}/{t['half']:g}/{t['alert']:g} m{t['m_score']} "
                             f"tp={a:g}/{b:g}/{c:g}")
                rows4.append(r)
    show("阶段4 TP", rows4)
    top4 = rank(rows4, 2)

    # ── 阶段5：SL / max_loss ──
    rows5 = []
    for base_ in top4:
        t = parse_tag(base_["tag"])
        p = {"periods": t["per"], "multiplier": t["mul"], "src": "hl2",
             "change_atr": True, "fast_len": 20, "slow_len": 50, "ma_type": "EMA"}
        cfg = make_cfg(er_min=t["er_min"], er_weak_min=t["er_weak"],
                       er_trend=t["er_trend"], min_score=t["m_score"],
                       scoring_full_threshold=t["full"],
                       scoring_half_threshold=t["half"],
                       scoring_alert_threshold=t["alert"])
        for sl in (2.0, 3.0, 4.0, 5.0):
            for ml in (2.0, 3.0, 5.0, 8.0):
                r = one(p, cfg, candles, cbtf,
                        er_over={"tp1_pct": t["tp1"], "tp2_pct": t["tp2"],
                                 "tp3_pct": t["tp3"], "sl_pct": sl,
                                 "max_loss_pct": ml})
                if r:
                    r.update(tag=f"p={t['per']}×{t['mul']:g} "
                                 f"er={t['er_min']:g}/{t['er_weak']:g}/{t['er_trend']:g} "
                                 f"score=d:{t['full']:g}/{t['half']:g}/{t['alert']:g} m{t['m_score']} "
                                 f"tp={t['tp1']:g}/{t['tp2']:g}/{t['tp3']:g} sl={sl:g} ml={ml:g}")
                    rows5.append(r)
    show("阶段5 SL/ML", rows5)

    final = sorted(rows5, key=lambda x: (x["pnl"], _pf(x)), reverse=True)
    print(f"\n=== BTC 最优 TOP 20 ===", flush=True)
    for i, x in enumerate(final[:20], 1):
        flag = " <样本少>" if x["trades"] < 10 else ""
        print(
            f"  {i:2d}. {x['tag']} "
            f"| pnl={x['pnl']}U trades={x['trades']} wr={x['wr']}% "
            f"PF={x['pf']} dd={x['dd']}%{flag}",
            flush=True,
        )

    out = os.path.join(os.path.dirname(__file__), "_sweep_btc.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"window": f"{YEAR_START}~{YEAR_END}", "baseline": base,
                   "stage1": rows1, "stage2": rows2, "stage3": rows3,
                   "stage4": rows4, "stage5": rows5}, f, ensure_ascii=False, indent=1)
    print(f"\nWrote {out} | 总耗时 {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
