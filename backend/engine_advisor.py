# -*- coding: utf-8 -*-
"""Adaptive Engine Advisor —— 自适应引擎建议器（阶段4：市场结构识别）

定位
----
不是"自动选择器"：它不做任何自动切换，只对给定品种给出**引擎建议 + 证据**，
由人确认后写进该品种的 Engine Profile（cfg.score_engine）。

核心洞见（7 品种 × 1h 实证，2026-03~09）
---------------------------------------
V1 / V2 不是"市场是否有趋势"的分类（SNDK/MU/CL 都是趋势型，但归属不同），
而是**现有过滤强度的分类**：

  conv = v1 线上闸门实际成交数 / SuperTrend flip 数

  - conv ≥ ~0.70（闸门全开，ETH 0.92 / SPCX 0.90 / MU 0.87 / NVDA 0.75）
      → v1 几乎每个翻转都放行 → 噪声摊薄 → 建议 V2(quality_filter_v2) 补二次过滤
  - conv ≤ ~0.50（闸门已内建克制，BTC 0.43 / SNDK 0.39 / CL 0.31）
      → v1 已砍掉一半以上翻转 → 再加过滤是误杀 → 保持 V1(trend_follow_v1)
  - 0.50 < conv < 0.70 为灰色带（7 品种尚无落点）：不武断，走 Shadow 验证

配套的 Gate Tightness（闸门紧度）= 1 - conv，就是"v1 已经主动挑了多少"。
反直觉但被数据确认：wr / 盈利信号占比 不是判据（CL wr 65% 却归 V1，
ETH wr 83% 却归 V2）；flip 密度也不是判据（CL 2.97/100根 归 V1，ETH 1.45 归 V2）。

置信度
------
- flips ≥ 80：high（conv 处边界 ±0.02 时降为 medium）
- 40 ≤ flips < 80：medium
- flips < 40：low —— 建议拉长回看窗口，或直接开 Shadow Mode 边跑边看
"""
from __future__ import annotations

import statistics
from typing import Optional

from indicators import super_trend, ta_atr
from regime import TradeConfig, efficiency_ratio
from backtest import run_backtest

# 实证硬分界（来自 _engine_classify / _engine_signal_eff 的 7 品种结果）
CONV_V2_MIN = 0.70      # ≥ 此值：闸门全开 → 建议 V2
CONV_V1_MAX = 0.50      # ≤ 此值：闸门克制 → 建议 V1
GREY_BAND = (CONV_V1_MAX, CONV_V2_MIN)
BOUNDARY_PAD = 0.02     # 落在分界线 ±0.02 内 → 置信度降一档

V1_ENGINE = "trend_follow_v1"
V2_ENGINE = "quality_filter_v2"


# ───────────────────────── 行情结构画像（metrics） ─────────────────────────

def market_stats(candles: list[dict], params: dict) -> dict:
    """与 _stock_compare / _engine_signal_eff 同口径的行情结构统计（不判单）。"""
    o = [c["o"] for c in candles]
    h = [c["h"] for c in candles]
    l = [c["l"] for c in candles]
    cl = [c["c"] for c in candles]
    st = super_trend(o, h, l, cl,
                     periods=params.get("periods", 15),
                     multiplier=params.get("multiplier", 9.1),
                     src=params.get("src", "hl2"),
                     change_atr=params.get("change_atr", True))
    trend = st["trend"]
    start = next((i for i in range(len(trend)) if trend[i] is not None), 0)
    eff = len(trend) - start
    flips = st["flips"]
    bounds = [start] + [f["i"] for f in flips] + [len(trend)]
    segs = [bounds[i + 1] - bounds[i] for i in range(len(bounds) - 1)]
    # ER 逐根滑动（window=60）均值与状态占比
    ers = [efficiency_ratio(candles[:i + 1]) for i in range(len(candles))]
    valid = [e for e in ers if e is not None]
    er_mean = round(statistics.mean(valid), 4) if valid else None
    # ATR ratio 序列：ATR14 / 过去50根均值（regime_scoring._atr_soft_part 同口径）
    atr14 = ta_atr(h, l, cl, 14)
    ratios = []
    for i in range(len(atr14)):
        if atr14[i] is None:
            continue
        win = [a for a in atr14[max(0, i - 49):i + 1] if a is not None]
        if len(win) >= 15 and win[-1] > 0:
            ratios.append(atr14[i] / (sum(win) / len(win)))
    return {
        "n": len(candles),
        "st": f"p={params.get('periods', 15)}×{params.get('multiplier', 9.1):g}",
        "eff_bars": eff,
        "flips": len(flips),
        "flip_per_100": round(len(flips) / eff * 100, 2) if eff else 0.0,
        "seg_mean": round(statistics.mean(segs), 1) if segs else None,
        "seg_median": statistics.median(segs) if segs else None,
        "er_mean": er_mean,
        "er_trend_pct": round(sum(1 for e in valid if e >= 0.30) / len(valid) * 100, 1)
        if valid else None,
        "er_weak_pct": round(sum(1 for e in valid if 0.15 <= e < 0.30) / len(valid) * 100, 1)
        if valid else None,
        "er_range_pct": round(sum(1 for e in valid if e < 0.15) / len(valid) * 100, 1)
        if valid else None,
        "atr_ratio_mean": round(statistics.mean(ratios), 3) if ratios else None,
        "atr_ratio_std": round(statistics.pstdev(ratios), 3) if ratios else None,
    }


def v1_replay_conv(candles: list[dict], params: dict, cfg: TradeConfig,
                   exit_rules=None, tf: str = "1h",
                   candles_by_tf: Optional[dict] = None,
                   margin_usdt: float = 10.0, leverage: int = 3) -> dict:
    """把 v1 线上闸门重放到缓存上，数出与实证同口径的 trades/wins。

    判单 = run_backtest(live_gate=cfg) —— 该路径已在 _engine_classify 验证
    与 43.108/47.84 两台线上成交数一致（MU 88 / NVDA 107 / SNDK 45）。
    conv 只依赖开仓判定；exit_rules 影响盈亏不影响笔数，可传可不传。
    """
    common = dict(init_cash=100.0, fee_rate=0.0005, allow_short=True,
                  exit_rules=exit_rules, sizing="fixed",
                  margin_usdt=margin_usdt, leverage=leverage,
                  gate_tf=tf, candles_by_tf=candles_by_tf or {tf: candles})
    r = run_backtest(candles, params, live_gate=cfg, **common)
    if "error" in r:
        raise ValueError(f"v1 重放失败: {r['error']}")
    return {"trades": r["trades"], "wins": r.get("wins", 0),
            "win_rate": r["win_rate"], "v1_pnl": r["final"] - 100.0}


# ───────────────────────── 建议规则（纯函数） ─────────────────────────

def recommend_engine(metrics: dict) -> dict:
    """根据 metrics（至少含 flips/conv）输出引擎建议。

    metrics 键：flips, conv（另有 flip_per_100 / seg_mean 等画像可展示，
    但第一版规则只用 conv + 样本量两个维度，避免过拟合 7 品种）。
    """
    flips = metrics.get("flips") or 0
    conv = metrics.get("conv")
    if conv is None:
        return {"engine": None, "short": None, "confidence": "low",
                "regime": "unknown", "reason": ["conv 缺失，无法建议"],
                "advice": "补上 v1 成交/flip 统计后再建议。"}
    # ── 样本量置信度 ──
    if flips >= 80:
        conf = "high"
    elif flips >= 40:
        conf = "medium"
    else:
        conf = "low"
    # ── conv 分界 ──
    near = BOUNDARY_PAD
    if conv >= CONV_V2_MIN:
        engine, short, regime = V2_ENGINE, "v2", "loose_gate"
        reasons = [
            f"conv={conv:.0%} ≥ {CONV_V2_MIN:.0%}：v1 闸门全开，每 {metrics.get('flips', 0)} 个翻转成交 {metrics.get('trades')} 笔",
            f"Gate Tightness = {1 - conv:.0%}：v1 几乎没有内建挑选",
            "建议 V2(quality_filter_v2) 补二次过滤，再按品种网格标定阈值",
        ]
        advice = ("把 cfg.score_engine 设为 quality_filter_v2，"
                  "阈值用 v2 网格(40/45/50/55/60)回测标定后固化。")
    elif conv <= CONV_V1_MAX:
        engine, short, regime = V1_ENGINE, "v1", "strict_gate"
        reasons = [
            f"conv={conv:.0%} ≤ {CONV_V1_MAX:.0%}：v1 闸门已内建克制",
            f"Gate Tightness = {1 - conv:.0%}：超过一半的翻转被 v1 主动拦截",
            "再叠加 V2 过滤属于二次误杀（BTC/SNDK/CL 实测 v2 Δ<0）",
        ]
        advice = "保持 cfg.score_engine 为 trend_follow_v1（或留空），不需要 V2。"
    else:
        engine, short, regime = None, "grey", "grey_band"
        reasons = [
            f"conv={conv:.0%} 落在灰色带 {GREY_BAND[0]:.0%}~{GREY_BAND[1]:.0%}：7 品种尚无此落点",
            "不武断切换：开 Shadow Mode 跑 2~4 周，让主引擎与候选引擎同台对比",
        ]
        advice = ("此区间建议：开 shadow_engine（主 v1 则 shadow v2，反之亦然），"
                  "用影子日志积累样本后再定。")
        return {"engine": engine, "short": short, "confidence": "low",
                "regime": regime, "reason": reasons, "advice": advice}
    # 分界线贴边 → 置信降档
    at_edge = (CONV_V2_MIN - near <= conv <= CONV_V2_MIN + near) or \
              (CONV_V1_MAX - near <= conv <= CONV_V1_MAX + near)
    if at_edge and conf == "high":
        conf = "medium"
    return {"engine": engine, "short": short, "confidence": conf,
            "regime": regime, "reason": reasons, "advice": advice}


def assess_symbol(name: str, candles_by_tf: dict, params: dict, cfg: TradeConfig,
                  exit_rules=None, tf: str = "1h",
                  margin_usdt: float = 10.0, leverage: int = 3) -> dict:
    """一站式入口：行情画像 + v1 重放 + 引擎建议。"""
    candles = candles_by_tf.get(tf) or candles_by_tf.get("1H")
    if not candles or len(candles) < 200:
        raise ValueError(f"{name}: 无 {tf} 数据或不足 200 根")
    stats = market_stats(candles, params)
    rep = v1_replay_conv(candles, params, cfg, exit_rules=exit_rules,
                         tf=tf, candles_by_tf=candles_by_tf,
                         margin_usdt=margin_usdt, leverage=leverage)
    metrics = {**stats, **rep,
               "conv": round(rep["trades"] / stats["flips"], 4) if stats["flips"] else None,
               "gate_tightness": round(1 - rep["trades"] / stats["flips"], 4)
               if stats["flips"] else None}
    advisor = recommend_engine(metrics)
    return {"symbol": name, "metrics": metrics, "advisor": advisor}
