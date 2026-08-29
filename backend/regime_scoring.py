"""
信号置信度打分系统 - 解决震荡过滤与趋势捕捉的矛盾

核心思想：
    不是"拦或不拦"的二元判断，而是给信号打0-100分，
    然后根据分数区间做差异化处理：
        80-100分 → 自动下单（高置信）
        60-79分  → 下单但减半仓位（中置信）
        40-59分  → 仅提醒不下单（低置信）
        <40分    → 静默（噪音）

优势：
    1. 真趋势即使在震荡末期ER还没起来，只要其他维度强，照样能下单
    2. 假信号即使ER勉强达标，其他维度弱也会被降级
    3. 用户能看到"为什么是这个分数"，可针对性调参
"""

from regime import (
    efficiency_ratio, classify, atr_volatility, range_bound,
    adx_latest, TradeConfig, ER_WINDOW
)


def score_signal(sig: dict, candles: list[dict], cfg: TradeConfig,
                 candles_by_tf: dict = None, p: dict = None) -> dict:
    """给信号打分（0-100），返回详细的分项和建议。

    返回：{
        "total_score": 75,
        "confidence": "medium",  # high/medium/low/noise
        "action": "trade_half",  # trade_full/trade_half/alert_only/silent
        "breakdown": {
            "signal_quality": 25,  # 信号自身强度（score 0-3 → 0-30分）
            "er_momentum": 20,      # ER趋势性（0-25分）
            "volatility": 15,       # 波动率扩张（0-15分）
            "mtf_alignment": 10,    # 多周期共振（0-20分）
            "breakout_boost": 20,   # 突破启动加成（0-20分）
            "penalties": -5         # 震荡特征扣分
        },
        "reasons": ["ER偏低但突破干脆", "MTF方向一致"],
        "suggestion": "中等置信度，建议半仓试探"
    }
    """
    er = efficiency_ratio(candles)
    regime = classify(er, cfg.er_min, cfg.er_trend, cfg.er_weak_min, cfg.quick_enabled)

    breakdown = {}
    reasons = []

    # ===== 1. 信号自身质量（0-30分）=====
    sig_score = sig.get("score", 0)
    breakdown["signal_quality"] = sig_score * 10  # 0/1/2/3 → 0/10/20/30
    if sig_score >= 2:
        reasons.append("✓ 翻转干脆有力")
    elif sig_score == 1:
        reasons.append("⚠ 翻转力度一般")
    else:
        reasons.append("✗ 翻转力度弱")

    # ===== 2. ER趋势性（0-25分）=====
    # 如果启用动态阈值，使用自适应的阈值
    effective_er_min = cfg.er_min
    adaptive_info = None
    if cfg.use_dynamic_threshold:
        from regime_dynamic import adaptive_er_threshold
        adaptive_info = adaptive_er_threshold(candles, cfg.er_min)
        effective_er_min = adaptive_info["threshold"]
        if adaptive_info.get("adjustment") != 0:
            reasons.append(f"ℹ️ 动态阈值{adaptive_info['threshold']:.2f} ({adaptive_info['reason']})")

    if er is None:
        breakdown["er_momentum"] = 0
        reasons.append("✗ K线不足")
    elif er >= cfg.er_trend:  # 0.30+
        breakdown["er_momentum"] = 25
        reasons.append("✓ 强趋势行情")
    elif er >= effective_er_min:    # 达到动态阈值
        # 根据距离阈值的程度给分
        if er >= cfg.er_trend * 0.7:  # 接近强趋势
            breakdown["er_momentum"] = 20
            reasons.append("✓ 中强趋势")
        else:
            breakdown["er_momentum"] = 15
            reasons.append("⚠ 弱趋势")
    elif cfg.er_weak_min and er >= cfg.er_weak_min:  # 弱档区间
        breakdown["er_momentum"] = 8
        reasons.append("⚠ 震荡边缘")
    else:  # 低于所有阈值
        breakdown["er_momentum"] = 0
        reasons.append("✗ 深度震荡")

    # ===== 3. 波动率状态（0-15分）=====
    atr_vol = atr_volatility(candles) if cfg.atr_filter_enabled else None
    if atr_vol is None:
        breakdown["volatility"] = 7  # 中性分，不扣不加
    elif atr_vol >= 1.2:  # 波动率扩张
        breakdown["volatility"] = 15
        reasons.append("✓ ATR扩张")
    elif atr_vol >= 0.9:
        breakdown["volatility"] = 10
    elif atr_vol >= 0.7:
        breakdown["volatility"] = 5
        reasons.append("⚠ ATR萎缩")
    else:
        breakdown["volatility"] = 0
        reasons.append("✗ ATR过度萎缩")

    # ===== 4. 多周期共振（0-20分）=====
    mtf_score = 10  # 默认中性分
    if cfg.mtf_filter_enabled and candles_by_tf and p:
        from strategy import mtf_st_consistency
        mtf = mtf_st_consistency(candles_by_tf, p, sig.get("tf"))
        consistency = mtf.get("consistency", 0)
        big_flips = mtf.get("big_tf_flips", 0)

        if consistency >= 0.8 and big_flips <= 2:
            mtf_score = 20
            reasons.append("✓ 多周期强共振")
        elif consistency >= 0.6:
            mtf_score = 15
            reasons.append("⚠ 多周期弱共振")
        elif big_flips > 5:
            mtf_score = 0
            reasons.append("✗ 大周期频繁震荡")
        else:
            mtf_score = 5
            reasons.append("⚠ 多周期方向分歧")
    breakdown["mtf_alignment"] = mtf_score

    # ===== 5. 突破启动加成（0-20分）=====
    # 这是关键：即使ER低，只要突破特征明显，也给高分
    quality_flip = sig_score >= 2
    on_trade_tf = sig.get("tf") in cfg.allow_tfs
    breakout_boost = 0

    if quality_flip and on_trade_tf:
        # 检测价格突破区间 + 量能配合
        range_check = range_bound(candles)
        range_pct = range_check.get("range_pct", 0.5)

        # 如果在区间边缘翻转（上轨看多/下轨看空）→ 突破启动
        is_breakout = False
        if sig["type"] == "buy" and range_pct < 0.3:  # 下轨附近看多
            is_breakout = True
        elif sig["type"] == "sell" and range_pct > 0.7:  # 上轨附近看空
            is_breakout = True

        if is_breakout:
            breakout_boost = 20
            reasons.append("✓✓ 突破启动！")
        elif quality_flip:
            breakout_boost = 10
            reasons.append("✓ 翻转质量高")
    breakdown["breakout_boost"] = breakout_boost

    # ===== 6. 震荡特征扣分（-20到0）=====
    penalties = 0

    # 区间震荡检测
    if cfg.range_filter_enabled:
        range_check = range_bound(candles)
        if (range_check["range_size_pct"] < cfg.range_size_max * 100
            and range_check["touches"] >= cfg.range_touches_min):
            penalties -= 10
            reasons.append("✗ 区间震荡明显")

    # ADX过低
    if cfg.adx_filter_enabled:
        adx_val = adx_latest(candles, cfg.adx_period)
        if adx_val is not None and adx_val < cfg.adx_min:
            penalties -= 5
            reasons.append("✗ ADX过低")

    breakdown["penalties"] = penalties

    # ===== 汇总分数 =====
    total = sum(breakdown.values())
    total = max(0, min(100, total))  # 限制在0-100

    # ===== 决策逻辑（阈值读取配置；未设置/为 0 时回退默认 80/60/40）=====
    full_thr  = getattr(cfg, "scoring_full_threshold",  80.0) or 80.0
    half_thr  = getattr(cfg, "scoring_half_threshold",  60.0) or 60.0
    alert_thr = getattr(cfg, "scoring_alert_threshold", 40.0) or 40.0
    if not cfg.enabled:
        confidence = "disabled"
        action = "alert_only"
        suggestion = "自动下单未开启"
    elif total >= full_thr:
        confidence = "high"
        action = "trade_full"
        suggestion = f"高置信度，全仓操作（≥{full_thr:g}分）"
    elif total >= half_thr:
        confidence = "medium"
        action = "trade_half"
        suggestion = f"中等置信度，建议半仓试探（≥{half_thr:g}分）"
    elif total >= alert_thr:
        confidence = "low"
        action = "alert_only"
        suggestion = "低置信度，仅提醒观察"
    else:
        confidence = "noise"
        action = "silent"
        suggestion = "噪音信号，静默"

    # 检查其他硬性条件
    if sig.get("grade") not in cfg.allow_grades:
        action = "alert_only"
        reasons.append(f"✗ 等级{sig.get('grade')}不在范围")
    if sig.get("tf") not in cfg.allow_tfs:
        action = "alert_only"
        reasons.append(f"✗ 周期{sig.get('tf')}不在范围")

    return {
        "total_score": round(total, 1),
        "confidence": confidence,
        "action": action,
        "breakdown": breakdown,
        "reasons": reasons,
        "suggestion": suggestion,
        "regime": regime,
        "hidden": action == "silent"
    }


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
