"""
动态ER阈值 - 解决ER滞后问题

核心思想：
    ER基于60根K线，从震荡转趋势时会滞后10-20根。
    不应该用固定阈值0.15，而应该：
    1. 检测ER是否在上升（震荡→趋势的转折点）
    2. 检测价格是否突破近期区间（启动征兆）
    3. 动态调整阈值：启动阶段放宽，稳定趋势收紧

实测场景：
    - BTC从横盘突破3%，ER可能才0.12，但这是最该抓的时机
    - BTC已经单边涨了15%，ER到0.35，但追高风险大
"""

from regime import efficiency_ratio, ER_WINDOW


def er_trend(candles: list[dict], window: int = ER_WINDOW) -> dict:
    """ER的趋势性：是在上升（震荡→趋势）还是下降（趋势→震荡）

    返回：{
        "current": 0.12,
        "prev": 0.10,        # 10根前的ER
        "slope": 0.02,       # ER斜率
        "accelerating": True # ER加速上升
    }
    """
    if len(candles) < window + 20:
        return {"current": None, "prev": None, "slope": 0, "accelerating": False}

    er_now = efficiency_ratio(candles[-window:])
    er_10ago = efficiency_ratio(candles[-window-10:-10])
    er_20ago = efficiency_ratio(candles[-window-20:-20])

    if er_now is None or er_10ago is None:
        return {"current": er_now, "prev": None, "slope": 0, "accelerating": False}

    slope = er_now - er_10ago

    # 加速判断：最近10根的ER增量 > 前10根的增量
    accel = False
    if er_20ago is not None:
        recent_delta = er_now - er_10ago
        prev_delta = er_10ago - er_20ago
        accel = recent_delta > prev_delta and recent_delta > 0.02

    return {
        "current": er_now,
        "prev": er_10ago,
        "slope": round(slope, 4),
        "accelerating": accel
    }


def breakout_intensity(candles: list[dict], lookback: int = 30) -> dict:
    """突破强度：价格突破近期区间的程度

    返回：{
        "range_high": 50000,
        "range_low": 48000,
        "current": 50500,
        "breakout_pct": 1.0,  # 突破幅度%（0表示在区间内）
        "volume_surge": 1.8   # 量能倍数
    }
    """
    if len(candles) < lookback + 5:
        return {"breakout_pct": 0, "volume_surge": 1.0}

    # 区间：lookback根前到5根前（排除最近5根，避免当前突破污染区间）
    range_candles = candles[-(lookback+5):-5]
    recent = candles[-5:]

    range_high = max(c["h"] for c in range_candles)
    range_low = min(c["l"] for c in range_candles)
    current = candles[-1]["c"]

    # 突破幅度
    breakout_pct = 0
    if current > range_high:
        breakout_pct = (current - range_high) / range_high * 100
    elif current < range_low:
        breakout_pct = (range_low - current) / range_low * 100

    # 量能激增
    base_vol = sum(c["vol"] for c in range_candles) / len(range_candles)
    recent_vol = sum(c["vol"] for c in recent) / len(recent)
    volume_surge = recent_vol / base_vol if base_vol > 0 else 1.0

    return {
        "range_high": range_high,
        "range_low": range_low,
        "current": current,
        "breakout_pct": round(breakout_pct, 2),
        "volume_surge": round(volume_surge, 2)
    }


def adaptive_er_threshold(candles: list[dict], base_er: float = 0.15) -> dict:
    """自适应ER阈值

    正常情况：要求ER >= 0.15
    突破启动：ER >= 0.10 即可（降低5个点）
    趋势末期：ER >= 0.20（提高5个点，防止追高）

    返回：{
        "threshold": 0.12,      # 当前应该用的阈值
        "reason": "突破启动阶段",
        "base": 0.15,
        "adjustment": -0.03,
        "tradable": True
    }
    """
    er_info = er_trend(candles)
    breakout = breakout_intensity(candles)

    er_now = er_info["current"]
    if er_now is None:
        return {
            "threshold": base_er,
            "reason": "数据不足",
            "base": base_er,
            "adjustment": 0,
            "tradable": False
        }

    adjustment = 0
    reason = "标准阈值"

    # 场景1：突破启动（ER在上升 + 价格突破区间 + 量能配合）
    if (er_info["slope"] > 0.02 and
        breakout["breakout_pct"] > 0.5 and
        breakout["volume_surge"] > 1.3):
        adjustment = -0.05  # 降低阈值
        reason = "突破启动阶段，ER滞后可接受"

        # 如果还加速上升，再放宽
        if er_info["accelerating"]:
            adjustment = -0.07
            reason = "突破加速阶段，大幅放宽"

    # 场景2：ER快速上升但还未突破区间（可能是假突破前兆）
    elif er_info["slope"] > 0.03 and breakout["breakout_pct"] < 0.3:
        adjustment = -0.03
        reason = "ER上升但价格未确认，小幅放宽"

    # 场景3：ER下降（趋势→震荡）
    elif er_info["slope"] < -0.02:
        adjustment = 0.05  # 收紧阈值
        reason = "ER下降，趋势减弱，收紧过滤"

    # 场景4：ER高位但价格远离区间（可能是趋势末期）
    elif er_now > 0.30 and abs(breakout["breakout_pct"]) > 5:
        adjustment = 0.05
        reason = "趋势末期，防止追高"

    threshold = base_er + adjustment
    threshold = max(0.08, min(0.25, threshold))  # 限制在合理范围

    return {
        "threshold": round(threshold, 2),
        "reason": reason,
        "base": base_er,
        "adjustment": round(adjustment, 2),
        "tradable": er_now >= threshold,
        "er_current": er_now,
        "er_slope": er_info["slope"],
        "breakout_pct": breakout["breakout_pct"],
        "volume_surge": breakout["volume_surge"]
    }


def evaluate_dynamic(sig: dict, candles: list[dict], cfg) -> dict:
    """使用动态阈值评估信号

    可直接替换 regime.evaluate()，或作为补充检查
    """
    adapt = adaptive_er_threshold(candles, cfg.er_min)

    reasons = []
    if not adapt["tradable"]:
        reasons.append(
            f"ER {adapt['er_current']:.2f} 低于动态阈值 {adapt['threshold']:.2f} "
            f"({adapt['reason']})"
        )

    # 其他检查...
    if sig.get("grade") not in cfg.allow_grades:
        reasons.append(f"等级 {sig.get('grade')} 不在范围")

    return {
        "trade": not reasons,
        "reasons": reasons,
        "adaptive_threshold": adapt,
        "hidden": False
    }
