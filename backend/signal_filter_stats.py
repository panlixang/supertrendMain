"""
基于统计相关性分析的信号过滤器（2026-09 BTC 1h 回测分析）

数据来源：808个信号（2022-09至今），保留信号成功率66.2%，过滤信号成功率17.9%
统计方法：T检验、Cohen's d效应量、点二列相关系数
关键特征：risk_score, bars_since_flip, ADX14（均通过p<0.05显著性检验）
"""

import math
from typing import Dict, Any, Tuple, List


# 方案B：双特征OR组合（推荐，简单实用）
RISK_SCORE_THRESHOLD = 48.0
BARS_SINCE_FLIP_THRESHOLD = 45

# 方案D：加权评分（智能方案）
RISK_SCORE_RANGE = (25.0, 75.0)
BARS_RANGE = (0, 200)
ADX_RANGE = (5.0, 60.0)

WEIGHTS = {
    'risk_score': 0.4,
    'bars_since_flip': 0.3,
    'ADX14': 0.3
}


def normalize(value: float, min_val: float, max_val: float) -> float:
    """将值标准化到0-100范围"""
    if max_val == min_val:
        return 50.0
    normalized = (value - min_val) / (max_val - min_val) * 100
    return max(0.0, min(100.0, normalized))


def calculate_filter_score_simple(risk_score: float, bars_since_flip: int) -> Tuple[bool, List[str]]:
    """
    方案B：双特征OR组合（推荐）

    规则：
    - risk_score > 48 过滤
    - bars_since_flip < 45 过滤（翻转时间过短）

    返回：(是否过滤, 原因列表)
    """
    reasons = []

    if risk_score > RISK_SCORE_THRESHOLD:
        reasons.append(f"风险评分过高({risk_score:.1f}>{RISK_SCORE_THRESHOLD})")

    if bars_since_flip < BARS_SINCE_FLIP_THRESHOLD:
        reasons.append(f"翻转时间过短({bars_since_flip}<{BARS_SINCE_FLIP_THRESHOLD})")

    should_filter = len(reasons) > 0
    return should_filter, reasons


def calculate_filter_score_weighted(risk_score: float, bars_since_flip: int,
                                   ADX14: float) -> Tuple[float, bool, List[str]]:
    """
    方案D：加权评分（智能方案）

    综合评分公式：
    - risk_score权重 × 40%
    - (100 - bars_since_flip)权重 × 30%
    - (100 - ADX14)权重 × 30%

    返回：(综合评分, 是否过滤, 原因列表)
    """
    # 标准化到0-100
    risk_norm = normalize(risk_score, RISK_SCORE_RANGE[0], RISK_SCORE_RANGE[1])

    # bars_since_flip: 越小越差，所以取反
    bars_norm = normalize(
        BARS_RANGE[1] - bars_since_flip,
        0,
        BARS_RANGE[1]
    )

    # ADX14: 越小越差，所以取反
    adx_norm = normalize(
        ADX_RANGE[1] - ADX14,
        0,
        ADX_RANGE[1] - ADX_RANGE[0]
    )

    # 加权组合
    filter_score = (
        risk_norm * WEIGHTS['risk_score'] +
        bars_norm * WEIGHTS['bars_since_flip'] +
        adx_norm * WEIGHTS['ADX14']
    )

    return filter_score, filter_score > 60.0, []


def apply_statistical_filter(features: Dict[str, Any], strategy: str = 'B',
                            threshold: float = 60.0) -> Tuple[bool, List[str], float]:
    """
    应用统计相关性过滤

    Args:
        features: 信号特征字典，需包含：
            - risk_score: 风险评分
            - bars_since_flip: 翻转后K线数
            - ADX14: ADX指标（策略D需要）
        strategy: 'B'=双特征OR, 'D'=加权评分
        threshold: 方案D的阈值（默认60）

    Returns:
        (是否过滤, 原因列表, 评分)
    """
    risk_score = features.get('risk_score', 0)
    bars_since_flip = features.get('bars_since_flip', 0)
    ADX14 = features.get('ADX14', 0)

    if strategy == 'B':
        should_filter, reasons = calculate_filter_score_simple(risk_score, bars_since_flip)
        return should_filter, reasons, 0.0

    elif strategy == 'D':
        score, should_filter, _ = calculate_filter_score_weighted(
            risk_score, bars_since_flip, ADX14
        )
        reasons = []
        if should_filter:
            reasons.append(f"统计评分过高({score:.1f}>{threshold})")
        return should_filter, reasons, score

    return False, [], 0.0
