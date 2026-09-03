"""
信号置信度打分系统 - 解决震荡过滤与趋势捕捉的矛盾

核心思想：
    不是"拦或不拦"的二元判断，而是给信号打0-100分，
    然后根据分数区间做差异化处理：
        80-100分 → 自动下单（高置信）
        60-79分  → 下单但减半仓位（中置信）
        40-59分  → 仅提醒不下单（低置信）
        <40分    → 静默（噪音）

Score V2（cfg.score_v2=True，默认关）：
    把"二元/阶梯"的评分项改成连续软分，目标是把每个已存在的信息
    从二元开关变成连续概率：
      - signal_quality：0-3 离散强度 → body_atr / vol_ratio / dist_atr 三项
        饱和映射（各 0-10，合计 0-30），低值不奖励、高值封顶不追分；
      - volatility：ATR ratio 软分（0-15），不再被 atr_filter_enabled 绑架
        （关掉硬过滤时信息不浪费，恒 7 中性分被替换）；
      - mtf_alignment：大周期方向软分（0-20），4h 同向 20 / 中性 12 /
        刚反向(切换中) 8 / 稳定强反向 4；
      - er_momentum / breakout_boost / penalties：与 v1 完全一致。
    raw 满分 = 30+25+15+20+20 = 110 → total = (raw + penalties) / 110 × 100。
    ⚠️ 分数口径变化后 scoring_full/half/alert 阈值须重新标定再上线。
"""

from regime import (
    efficiency_ratio, classify, atr_volatility, range_bound,
    adx_latest, TradeConfig, ER_WINDOW
)


# ===== 评分引擎（Engine Profile，阶段2）=====

_ENGINE_ALIASES = {
    # 规范化名 → 实际公式族。新增引擎（如未来 V3 / Volatility Context）
    # 只需在此注册并给出对应分项函数。
    "v1": ("v1", "trend_follow_v1", "trend_follow"),
    "v2": ("v2", "quality_filter_v2", "quality_filter"),
}


def resolve_engine(cfg: TradeConfig) -> str:
    """决定实际使用的评分引擎："v1" / "v2"。

    优先级：显式 score_engine（推荐）> score_v2 bool（旧配置兼容）> v1。
    score_engine 为未知名字时回退 v1 并保持行为不静默变化。
    """
    raw = (getattr(cfg, "score_engine", "") or "").strip().lower()
    if raw:
        for canonical, aliases in _ENGINE_ALIASES.items():
            if raw in aliases:
                return canonical
        # 未知引擎名：回退 v1（宁可旧行为，不静默切到未验证公式）
        return "v1"
    if getattr(cfg, "score_v2", False):
        return "v2"
    return "v1"


# ===== 共用分项：ER / breakout / penalties（v1 与 v2 完全一致）=====

def _er_part(sig: dict, candles: list[dict], cfg: TradeConfig) -> tuple[int, list[str]]:
    """ER 趋势性（0-25）。含动态阈值（cfg.use_dynamic_threshold）。"""
    reasons: list[str] = []
    er = efficiency_ratio(candles)
    effective_er_min = cfg.er_min
    if cfg.use_dynamic_threshold:
        from regime_dynamic import adaptive_er_threshold
        adaptive_info = adaptive_er_threshold(candles, cfg.er_min)
        effective_er_min = adaptive_info["threshold"]
        if adaptive_info.get("adjustment") != 0:
            reasons.append(f"ℹ️ 动态阈值{adaptive_info['threshold']:.2f} ({adaptive_info['reason']})")

    if er is None:
        return 0, reasons + ["✗ K线不足"]
    if er >= cfg.er_trend:
        return 25, reasons + ["✓ 强趋势行情"]
    if er >= effective_er_min:
        if er >= cfg.er_trend * 0.7:
            return 20, reasons + ["✓ 中强趋势"]
        return 15, reasons + ["⚠ 弱趋势"]
    if cfg.er_weak_min and er >= cfg.er_weak_min:
        return 8, reasons + ["⚠ 震荡边缘"]
    return 0, reasons + ["✗ 深度震荡"]


def _breakout_part(sig: dict, candles: list[dict], cfg: TradeConfig,
                   quality_flip: bool) -> tuple[int, list[str]]:
    """突破启动加成（0-20）。v1/v2 相同：score>=2 + 区间边缘翻转。"""
    reasons: list[str] = []
    if not quality_flip:
        return 0, reasons
    if sig.get("tf") not in cfg.allow_tfs:
        return 0, reasons
    rc = range_bound(candles)
    range_pct = rc.get("range_pct", 0.5)
    is_breakout = (
        (sig["type"] == "buy" and range_pct < 0.3) or
        (sig["type"] == "sell" and range_pct > 0.7)
    )
    if is_breakout:
        return 20, reasons + ["✓✓ 突破启动！"]
    return 10, reasons + ["✓ 翻转质量高"]


def _penalty_part(sig: dict, candles: list[dict], cfg: TradeConfig) -> tuple[int, list[str]]:
    """震荡特征扣分（-20~0）。v1/v2 相同，仅 -10 以内。"""
    reasons: list[str] = []
    penalties = 0
    if cfg.range_filter_enabled:
        rc = range_bound(candles)
        if (rc["range_size_pct"] < cfg.range_size_max * 100
                and rc["touches"] >= cfg.range_touches_min):
            penalties -= 10
            reasons.append("✗ 区间震荡明显")
    if cfg.adx_filter_enabled:
        adx_val = adx_latest(candles, cfg.adx_period)
        if adx_val is not None and adx_val < cfg.adx_min:
            penalties -= 5
            reasons.append("✗ ADX过低")
    return penalties, reasons


# ===== v1 分项（原逻辑逐字保留）=====

def _parts_v1(sig: dict, candles: list[dict], cfg: TradeConfig,
              candles_by_tf: dict = None, p: dict = None) -> tuple[dict, list[str], float]:
    breakdown: dict[str, float] = {}
    reasons: list[str] = []

    # 1. 信号自身质量（0-30）：score 0/1/2/3 → 0/10/20/30
    sig_score = sig.get("score", 0)
    breakdown["signal_quality"] = float(sig_score * 10)
    if sig_score >= 2:
        reasons.append("✓ 翻转干脆有力")
    elif sig_score == 1:
        reasons.append("⚠ 翻转力度一般")
    else:
        reasons.append("✗ 翻转力度弱")

    # 2. ER 趋势性（0-25）
    er_score, er_reasons = _er_part(sig, candles, cfg)
    breakdown["er_momentum"] = float(er_score)
    reasons += er_reasons

    # 3. 波动率状态（0-15）
    atr_vol = atr_volatility(candles) if cfg.atr_filter_enabled else None
    if atr_vol is None:
        breakdown["volatility"] = 7.0  # 中性分，不扣不加
    elif atr_vol >= 1.2:
        breakdown["volatility"] = 15.0
        reasons.append("✓ ATR扩张")
    elif atr_vol >= 0.9:
        breakdown["volatility"] = 10.0
    elif atr_vol >= 0.7:
        breakdown["volatility"] = 5.0
        reasons.append("⚠ ATR萎缩")
    else:
        breakdown["volatility"] = 0.0
        reasons.append("✗ ATR过度萎缩")

    # 4. 多周期共振（0-20）
    mtf_score = 10.0  # 默认中性分
    if cfg.mtf_filter_enabled and candles_by_tf and p:
        from strategy import mtf_st_consistency
        mtf = mtf_st_consistency(candles_by_tf, p, sig.get("tf"))
        consistency = mtf.get("consistency", 0)
        big_flips = mtf.get("big_tf_flips", 0)
        if consistency >= 0.8 and big_flips <= 2:
            mtf_score = 20.0
            reasons.append("✓ 多周期强共振")
        elif consistency >= 0.6:
            mtf_score = 15.0
            reasons.append("⚠ 多周期弱共振")
        elif big_flips > 5:
            mtf_score = 0.0
            reasons.append("✗ 大周期频繁震荡")
        else:
            mtf_score = 5.0
            reasons.append("⚠ 多周期方向分歧")
    breakdown["mtf_alignment"] = mtf_score

    # 5. 突破启动加成（0-20）
    boost, boost_reasons = _breakout_part(sig, candles, cfg, sig_score >= 2)
    breakdown["breakout_boost"] = float(boost)
    reasons += boost_reasons

    # 6. 震荡特征扣分
    penalties, penalty_reasons = _penalty_part(sig, candles, cfg)
    breakdown["penalties"] = float(penalties)
    reasons += penalty_reasons

    total = sum(breakdown.values())
    total = max(0.0, min(100.0, total))
    return breakdown, reasons, total


# ===== v2 分项 =====

def _sat(x: float | None, anchors: list[tuple[float, float]]) -> float:
    """饱和分段线性：低于首锚点取下限，高于末锚点封顶，中间线性插值。"""
    if x is None:
        return anchors[0][1]
    if x <= anchors[0][0]:
        return anchors[0][1]
    if x >= anchors[-1][0]:
        return anchors[-1][1]
    for (x1, y1), (x2, y2) in zip(anchors, anchors[1:]):
        if x1 <= x <= x2:
            return y1 + (y2 - y1) * (x - x1) / (x2 - x1)
    return anchors[-1][1]


def _signal_parts_v2(sig: dict) -> tuple[float, dict]:
    """signal_quality 连续化（0-30）＝ body 10 + volume 10 + dist 10。

    锚点思路（v1 的二元条件被换成饱和曲线，>1.5 ATR 后边际价值递减）：
      body  : 实体/ATR，<0.2→0，0.5→5，1.0→8，>1.5→10
      volume: 量/前20均量，<1.0→0（缩量不给分），1.2→4，2.0→8，>2.5→10
      dist  : 距被突破轨/ATR，<0.3→0，0.5→4，1.0→7，>2.0→10
    原始度量来自 indicators.st_signals（body_atr/vol_ratio/dist_atr）。
    """
    body = _sat(sig.get("body_atr"), [(0.2, 0.0), (0.5, 5.0), (1.0, 8.0), (1.5, 10.0)])
    volume = _sat(sig.get("vol_ratio"), [(1.0, 0.0), (1.2, 4.0), (2.0, 8.0), (2.5, 10.0)])
    dist = _sat(sig.get("dist_atr"), [(0.3, 0.0), (0.5, 4.0), (1.0, 7.0), (2.0, 10.0)])
    detail = {
        "body": round(body, 2), "volume": round(volume, 2), "dist": round(dist, 2),
        "raw": {"body_atr": sig.get("body_atr"), "vol_ratio": sig.get("vol_ratio"),
                "dist_atr": sig.get("dist_atr")},
    }
    return round(body + volume + dist, 2), detail


def _atr_soft_part(candles: list[dict]) -> tuple[float, dict]:
    """波动率软分（0-15）：ATR14 / SMA(ATR14, 50)。

    与 v1 不同：不再挂在 atr_filter_enabled 硬过滤开关上 —— 关过滤只意味着
    "不拦单"，不代表波动信息要浪费。数据不足时给中性 7。
    ATR ratio 阶梯（趋势启动通常在 1.1~1.2 已有意义）：
        <0.75 → 0（波动萎缩，趋势难立）；0.75-1.0 → 5；
        1.0-1.25 → 10；≥1.25 → 15
    """
    r = atr_volatility(candles, atr_window=14, lookback=50)
    if r is None:
        return 7.0, {"atr_ratio": None, "note": "数据不足，中性7"}
    if r < 0.75:
        return 0.0, {"atr_ratio": r}
    if r < 1.0:
        return 5.0, {"atr_ratio": r}
    if r < 1.25:
        return 10.0, {"atr_ratio": r}
    return 15.0, {"atr_ratio": r}


def _mtf_soft_part(sig: dict, candles_by_tf: dict, p: dict,
                   cfg: TradeConfig | None = None) -> tuple[float, dict]:
    """多周期软分（0-20）：大周期方向 vs 信号方向。

    依据 cfg.mtf_filter_enabled 的硬过滤一刀切会杀掉趋势初期小周期领先的
    信号，这里只按 4h（target=1h 时）方向给分差。档位取自 cfg 的 MTF 软分
    档位表（Profile 化参数，默认 = v2 现行值）：
        大周期与信号同向            → mtf_align_score（默认 20）
        大周期无数据 / 无翻转(seed)  → mtf_neutral_score（默认 12）
        大周期刚反向（切换中）      → mtf_recent_reverse_score（默认 8）
        大周期稳定反向              → mtf_strong_reverse_score（默认 4）
    """
    g = lambda k, d: float(getattr(cfg, k, None) or d)
    s_align = g("mtf_align_score", 20.0)
    s_neutral = g("mtf_neutral_score", 12.0)
    s_recent = g("mtf_recent_reverse_score", 8.0)
    s_strong = g("mtf_strong_reverse_score", 4.0)
    target_tf = sig.get("tf")
    big_tf = "4h" if target_tf != "4h" else "1d"
    arr = (candles_by_tf or {}).get(big_tf)
    if not arr or len(arr) < (p or {}).get("periods", 15) + 5:
        return s_neutral, {"big_tf": big_tf, "trend": None, "note": "无大周期数据，中性分"}

    from indicators import super_trend
    st = super_trend(
        [c["o"] for c in arr], [c["h"] for c in arr],
        [c["l"] for c in arr], [c["c"] for c in arr],
        periods=(p or {}).get("periods", 15),
        multiplier=(p or {}).get("multiplier", 9.1),
        src=(p or {}).get("src", "hl2"),
        change_atr=(p or {}).get("change_atr", True),
    )
    trend = st.get("trend") or []
    if not trend or trend[-1] is None:
        return s_neutral, {"big_tf": big_tf, "trend": None, "note": "大周期无方向，中性分"}
    big = trend[-1]
    want = 1 if sig.get("type") == "buy" else -1
    info = {"big_tf": big_tf, "trend": big}
    if big == want:
        return s_align, {**info, "note": "大周期同向"}
    if not st.get("flips"):
        return s_neutral, {**info, "note": "大周期仅初始方向(seed)，不可靠，中性分"}
    bars = 0
    for v in reversed(trend):
        if v == big:
            bars += 1
        else:
            break
    info["bars"] = bars
    if bars <= 3:
        return s_recent, {**info, "note": "大周期刚反向(切换中)，轻微反"}
    return s_strong, {**info, "note": "大周期稳定反向，强反"}


def _parts_v2(sig: dict, candles: list[dict], cfg: TradeConfig,
              candles_by_tf: dict = None, p: dict = None) -> tuple[dict, list[str], float, dict]:
    breakdown: dict[str, float] = {}
    reasons: list[str] = []
    detail: dict = {}

    # 1. signal_quality（0-30）连续化
    sig_score = sig.get("score", 0)
    sq, sq_detail = _signal_parts_v2(sig)
    breakdown["signal_quality"] = sq
    detail["signal"] = sq_detail
    if sq >= 20:
        reasons.append(f"✓ 翻转干脆（sq={sq:g}）")
    elif sq >= 10:
        reasons.append(f"⚠ 翻转力度一般（sq={sq:g}）")
    else:
        reasons.append(f"✗ 翻转力度弱（sq={sq:g}）")

    # 2. ER 趋势性（0-25）—— 与 v1 相同
    er_score, er_reasons = _er_part(sig, candles, cfg)
    breakdown["er_momentum"] = float(er_score)
    reasons += er_reasons

    # 3. 波动率软分（0-15）—— 不再依赖 atr_filter_enabled
    atr_score, atr_detail = _atr_soft_part(candles)
    breakdown["volatility"] = float(atr_score)
    detail["atr"] = atr_detail
    if atr_detail.get("atr_ratio") is not None:
        if atr_score >= 10:
            reasons.append(f"✓ ATR扩张({atr_detail['atr_ratio']:.2f})")
        elif atr_score <= 0:
            reasons.append(f"✗ ATR萎缩({atr_detail['atr_ratio']:.2f})")

    # 4. 多周期软分（0-20）—— 大周期方向给分，不下硬拦截
    mtf_score, mtf_detail = _mtf_soft_part(sig, candles_by_tf, p, cfg)
    breakdown["mtf_alignment"] = float(mtf_score)
    detail["mtf"] = mtf_detail
    if mtf_detail.get("note"):
        tag = "✓" if mtf_score >= 15 else ("⚠" if mtf_score >= 8 else "✗")
        reasons.append(f"{tag} {mtf_detail['note']}")

    # 5. 突破启动加成（0-20）—— 与 v1 相同（仍用 0-3 score 判断 quality flip）
    boost, boost_reasons = _breakout_part(sig, candles, cfg, sig_score >= 2)
    breakdown["breakout_boost"] = float(boost)
    reasons += boost_reasons

    # 6. 震荡特征扣分 —— 与 v1 相同
    penalties, penalty_reasons = _penalty_part(sig, candles, cfg)
    breakdown["penalties"] = float(penalties)
    reasons += penalty_reasons

    # 归一：raw 满分 110 → 100
    raw = sum(v for k, v in breakdown.items() if k != "penalties")
    total = max(0.0, min(100.0, (raw + penalties) / 110.0 * 100.0))
    detail["raw"] = round(raw, 2)
    return breakdown, reasons, total, detail


def score_signal(sig: dict, candles: list[dict], cfg: TradeConfig,
                 candles_by_tf: dict = None, p: dict = None) -> dict:
    """给信号打分（0-100），返回详细的分项和建议。

    返回：{
        "total_score": 75,
        "confidence": "medium",  # high/medium/low/noise
        "action": "trade_full/trade_half/alert_only/silent",
        "breakdown": {"signal_quality", "er_momentum", "volatility",
                      "mtf_alignment", "breakout_boost", "penalties"},
        "reasons": [...], "suggestion": "...", "regime": {...},
        "hidden": bool
    }
    评分引擎由 cfg.score_engine（优先）或 cfg.score_v2（兼容）决定
    （见 resolve_engine）。返回 extra：engine / score_version / detail(v2)。
    """
    er = efficiency_ratio(candles)
    regime = classify(er, cfg.er_min, cfg.er_trend, cfg.er_weak_min, cfg.quick_enabled)

    engine = resolve_engine(cfg)
    v2 = engine == "v2"
    if v2:
        breakdown, reasons, total, detail = _parts_v2(sig, candles, cfg,
                                                      candles_by_tf, p)
    else:
        breakdown, reasons, total = _parts_v1(sig, candles, cfg,
                                              candles_by_tf, p)
        detail = None

    full_thr = getattr(cfg, "scoring_full_threshold", 80.0) or 80.0
    half_thr = getattr(cfg, "scoring_half_threshold", 60.0) or 60.0
    alert_thr = getattr(cfg, "scoring_alert_threshold", 40.0) or 40.0

    if not cfg.enabled:
        confidence, action = "disabled", "alert_only"
        suggestion = "自动下单未开启"
    elif total >= full_thr:
        confidence, action = "high", "trade_full"
        suggestion = f"高置信度，全仓操作（≥{full_thr:g}分）"
    elif total >= half_thr:
        confidence, action = "medium", "trade_half"
        suggestion = f"中等置信度，建议半仓试探（≥{half_thr:g}分）"
    elif total >= alert_thr:
        confidence, action = "low", "alert_only"
        suggestion = "低置信度，仅提醒观察"
    else:
        confidence, action = "noise", "silent"
        suggestion = "噪音信号，静默"

    # 硬性降级：grade / tf 不符 → 一律只提醒
    if sig.get("grade") not in cfg.allow_grades:
        action = "alert_only"
        reasons.append(f"✗ 等级{sig.get('grade')}不在范围")
    if sig.get("tf") not in cfg.allow_tfs:
        action = "alert_only"
        reasons.append(f"✗ 周期{sig.get('tf')}不在范围")

    out = {
        "total_score": round(total, 1),
        "confidence": confidence,
        "action": action,
        "breakdown": breakdown,
        "reasons": reasons,
        "suggestion": suggestion,
        "regime": regime,
        "hidden": action == "silent",
        "engine": engine,
        "score_version": "v2" if v2 else "v1",
    }
    if v2:
        out["detail"] = detail
    return out


def evaluate_enhanced(sig: dict, candles: list[dict], cfg: TradeConfig,
                      candles_by_tf: dict = None, p: dict = None) -> dict:
    """增强版评估，替代原来的 regime.evaluate()

    兼容原接口，但返回更丰富的信息供UI展示和执行器使用。
    """
    result = score_signal(sig, candles, cfg, candles_by_tf, p)

    # 转换为原接口格式
    return {
        "trade": result["action"] in ["trade_full", "trade_half"],
        "trade_half": result["action"] == "trade_half",  # 新增：半仓标记
        "regime": result["regime"],
        "reasons": result["reasons"],
        "hidden": result["hidden"],
        "profile": result["regime"].get("profile") if result["action"] != "silent" else None,
        # 新增字段
        "score_detail": {
            "total": result["total_score"],
            "confidence": result["confidence"],
            "breakdown": result["breakdown"],
            "suggestion": result["suggestion"]
        }
    }
