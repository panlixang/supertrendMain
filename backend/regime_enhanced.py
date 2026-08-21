"""
行情状态判定增强版 - 解决大行情滞后和震荡误判问题

核心改进：
1. 动量突破检测：大行情启动时即使ER低也能识别
2. 多维度震荡识别：避免假突破
3. 品种自适应：根据历史波动率调整阈值
"""

from __future__ import annotations
import regime
from regime import efficiency_ratio, classify, TradeConfig
from indicators import ta_atr


def detect_momentum_breakout(candles: list[dict], sig: dict,
                              lookback: int = 20) -> dict | None:
    """动量突破检测：识别大行情启动

    特征：
    1. 价格快速突破近期区间
    2. 成交量放大
    3. 连续同向K线

    返回：None=不是突破，dict=突破信息
    """
    if len(candles) < lookback + 1:
        return None

    recent = candles[-lookback-1:-1]
    current = candles[-1]

    # 1. 区间突破：当前价超出近期区间的X%
    high_range = max(c["h"] for c in recent)
    low_range = min(c["l"] for c in recent)
    range_size = (high_range - low_range) / low_range

    is_buy = sig["type"] == "buy"
    if is_buy:
        breakout_dist = (current["c"] - high_range) / high_range
        valid_breakout = breakout_dist > range_size * 0.3  # 突破幅度 > 区间的30%
    else:
        breakout_dist = (low_range - current["c"]) / low_range
        valid_breakout = breakout_dist > range_size * 0.3

    if not valid_breakout:
        return None

    # 2. 成交量确认：近3根均量 > 近20根均量的1.5倍
    vols = [c["vol"] for c in candles]
    vol_recent3 = sum(vols[-3:]) / 3 if len(vols) >= 3 else 0
    vol_base = sum(vols[-lookback:]) / lookback if len(vols) >= lookback else 0
    volume_surge = vol_recent3 > vol_base * 1.5 if vol_base > 0 else False

    # 3. 连续同向K线：近3根有2根以上同向
    same_dir = 0
    for c in candles[-3:]:
        if (is_buy and c["c"] > c["o"]) or (not is_buy and c["c"] < c["o"]):
            same_dir += 1
    momentum_confirmed = same_dir >= 2

    # 至少满足2个条件
    score = sum([valid_breakout, volume_surge, momentum_confirmed])
    if score >= 2:
        return {
            "type": "momentum",
            "breakout_dist": round(breakout_dist * 100, 2),
            "volume_surge": volume_surge,
            "momentum": momentum_confirmed,
            "score": score,
            "reason": f"动量突破（区间{breakout_dist*100:.1f}%，量能{'放大' if volume_surge else '正常'}）"
        }

    return None


def detect_false_breakout(candles: list[dict], sig: dict) -> bool:
    """假突破检测：震荡市的假信号

    特征：
    1. 突破后立即回落
    2. 上下影线过长（犹豫）
    3. 波动率持续萎缩
    """
    if len(candles) < 30:
        return False

    current = candles[-1]
    prev = candles[-2] if len(candles) >= 2 else current

    # 1. 当根K线上下影线占比
    body = abs(current["c"] - current["o"])
    total = current["h"] - current["l"]
    shadow_ratio = (total - body) / total if total > 0 else 0

    # 影线占比 > 60% 表示犹豫不决
    hesitant = shadow_ratio > 0.6

    # 2. ATR持续萎缩（连续5根ATR下降）
    highs = [c["h"] for c in candles]
    lows = [c["l"] for c in candles]
    closes = [c["c"] for c in candles]
    atr_series = ta_atr(highs, lows, closes, 14)

    atr_declining = False
    if len(atr_series) >= 6:
        recent_atr = [a for a in atr_series[-6:] if a is not None]
        if len(recent_atr) >= 5:
            declining_count = sum(1 for i in range(1, len(recent_atr))
                                 if recent_atr[i] < recent_atr[i-1])
            atr_declining = declining_count >= 4

    # 3. 快速反转：突破后下一根立即回到区间内
    is_buy = sig["type"] == "buy"
    quick_reversal = False
    if len(candles) >= 3:
        prev2 = candles[-3]
        if is_buy:
            quick_reversal = current["c"] < prev2["h"]  # 突破后又回到前面的高点下方
        else:
            quick_reversal = current["c"] > prev2["l"]

    # 满足2个以上假突破特征
    false_signals = sum([hesitant, atr_declining, quick_reversal])
    return false_signals >= 2


def adaptive_thresholds(candles: list[dict], base_cfg: TradeConfig) -> dict:
    """根据品种历史波动率自适应调整阈值

    波动率高的品种（如山寨币）：
    - ER阈值降低（更容易识别趋势）
    - 止盈点提高

    波动率低的品种（如BTC）：
    - ER阈值提高（避免假信号）
    - 止盈点降低
    """
    if len(candles) < 100:
        return {}

    # 计算近期价格波动率
    closes = [c["c"] for c in candles[-100:]]
    returns = [(closes[i] - closes[i-1]) / closes[i-1]
               for i in range(1, len(closes))]
    volatility = (sum(r**2 for r in returns) / len(returns)) ** 0.5

    # 标准化：BTC日波动率约2-3%作为基准
    vol_ratio = volatility / 0.025  # 0.025 = 2.5%基准

    adjustments = {}

    # 高波动品种：降低ER要求，提高止盈
    if vol_ratio > 1.5:  # 波动率是BTC的1.5倍以上
        adjustments["er_min"] = max(0.10, base_cfg.er_min * 0.8)
        adjustments["er_trend"] = max(0.20, base_cfg.er_trend * 0.8)
        adjustments["tp_multiplier"] = 1.3  # 止盈点×1.3
    # 低波动品种：提高ER要求，降低止盈
    elif vol_ratio < 0.7:
        adjustments["er_min"] = min(0.25, base_cfg.er_min * 1.2)
        adjustments["er_trend"] = min(0.40, base_cfg.er_trend * 1.2)
        adjustments["tp_multiplier"] = 0.8  # 止盈点×0.8
    else:
        adjustments["tp_multiplier"] = 1.0

    adjustments["volatility"] = round(volatility * 100, 2)
    adjustments["vol_ratio"] = round(vol_ratio, 2)

    return adjustments


def evaluate_enhanced(sig: dict, candles: list[dict], cfg: TradeConfig,
                      candles_by_tf: dict = None, p: dict = None) -> dict:
    """增强版闸门评估

    核心改进：
    1. 优先检测动量突破（大行情启动）
    2. 过滤假突破（震荡陷阱）
    3. 自适应阈值调整
    """
    # 1. 动量突破优先：即使ER低也放行
    momentum = detect_momentum_breakout(candles, sig, lookback=20)
    if momentum:
        # 但仍需检查假突破
        if detect_false_breakout(candles, sig):
            return {
                "trade": False,
                "regime": {"regime": "false_breakout", "label": "假突破"},
                "reasons": ["检测到假突破特征（上下影线长/ATR萎缩/快速反转）"],
                "hidden": False,
                "momentum": momentum,
                "filters": {}
            }

        # 真突破：只放行「允许交易的周期/等级」。1m 上 20 根就是 20 分钟噪声，
        # 以前这里直接 trade=True，等于无视 allow_tfs，中午三笔空单就是这么开出来的。
        reasons = []
        if not cfg.enabled:
            reasons.append("自动挂单未开启")
        tf = sig.get("tf")
        if tf not in cfg.allow_tfs:
            reasons.append(f"周期 {tf} 不在允许范围")
        grade = sig.get("grade")
        if grade not in cfg.allow_grades:
            reasons.append(f"信号等级 {grade} 不在允许范围 {'/'.join(cfg.allow_grades)}")
        if (sig.get("score") or 0) < cfg.min_score:
            reasons.append(f"强度 {sig.get('score')}/3 低于阈值 {cfg.min_score}/3")
        return {
            "trade": not reasons,
            "regime": {"regime": "momentum", "label": "动量突破",
                       "er": efficiency_ratio(candles)},
            "reasons": reasons,
            "hidden": False,
            "profile": "normal" if not reasons else None,
            "momentum": momentum,
            "filters": {"momentum_score": momentum["score"]},
        }

    # 2. 假突破检测：震荡市陷阱
    if detect_false_breakout(candles, sig):
        er = efficiency_ratio(candles)
        regime_info = classify(er, cfg.er_min, cfg.er_trend, cfg.er_weak_min, cfg.quick_enabled)
        return {
            "trade": False,
            "regime": regime_info,
            "reasons": ["检测到假突破特征，疑似震荡陷阱"],
            "hidden": False,
            "filters": {"false_breakout": True}
        }

    # 3. 自适应阈值调整
    adjustments = adaptive_thresholds(candles, cfg)
    if adjustments:
        # 创建调整后的配置
        from dataclasses import replace
        adjusted_cfg = replace(
            cfg,
            er_min=adjustments.get("er_min", cfg.er_min),
            er_trend=adjustments.get("er_trend", cfg.er_trend)
        )
    else:
        adjusted_cfg = cfg

    # 4. 使用原有评估逻辑（但用调整后的阈值）
    result = regime.evaluate(sig, candles, adjusted_cfg, candles_by_tf, p)

    # 附加自适应信息
    if adjustments:
        result["filters"]["adaptive"] = adjustments

    return result
