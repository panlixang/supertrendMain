"""
行情趋势分类算法
识别六种市场状态：趋势启动期、趋势运行期、趋势衰减期、震荡吸收期、假突破期、恐慌释放期
"""

import math
from typing import Optional


def calculate_er(closes: list, period: int = 20) -> float:
    """计算效率比率 (Efficiency Ratio)"""
    if len(closes) < period + 1:
        return 0.0

    net_change = abs(closes[-1] - closes[-period - 1])
    total_movement = sum(abs(closes[i] - closes[i - 1]) for i in range(-period, 0))

    return net_change / total_movement if total_movement > 0 else 0.0


def calculate_atr_trend(atr_values: list, lookback: int = 20) -> float:
    """计算ATR变化趋势 (正值=波动增加, 负值=波动减少)"""
    if len(atr_values) < lookback + 1:
        return 0.0

    recent = atr_values[-lookback:]
    if not recent or recent[0] == 0:
        return 0.0

    return (recent[-1] - recent[0]) / recent[0] * 100


def calculate_price_momentum(closes: list, period: int = 20) -> float:
    """计算价格动量 (%)"""
    if len(closes) < period + 1:
        return 0.0

    old_price = closes[-period - 1]
    new_price = closes[-1]

    return (new_price - old_price) / old_price * 100 if old_price else 0.0


def calculate_adx_slope(adx_values: list, lookback: int = 10) -> float:
    """计算ADX斜率 (判断趋势强度变化)"""
    if len(adx_values) < lookback + 1:
        return 0.0

    recent = adx_values[-lookback:]
    if not recent:
        return 0.0

    return recent[-1] - recent[0]


def count_flips_in_window(flip_indices: list, current_idx: int, window: int = 50) -> int:
    """统计窗口内的ST翻转次数"""
    start_idx = max(0, current_idx - window)
    return sum(1 for idx in flip_indices if start_idx < idx <= current_idx)


def calculate_volatility_spike(highs: list, lows: list, atr: float, lookback: int = 10) -> float:
    """计算最近的波动率尖峰 (最大单根K线振幅 / ATR)"""
    if len(highs) < lookback or atr == 0:
        return 0.0

    recent_ranges = [(highs[i] - lows[i]) / atr for i in range(-lookback, 0)]
    return max(recent_ranges) if recent_ranges else 0.0


def classify_market_regime(
    closes: list,
    highs: list,
    lows: list,
    atr_values: list,
    adx_values: list,
    flip_indices: list,
    current_idx: int,
    signal_direction: int,  # 1=多, -1=空
) -> dict:
    """
    分类当前信号所处的市场状态（优化版 v2 - 基于 808 笔数据调优）

    返回: {
        "regime": str,  # 六种状态之一
        "confidence": float,  # 0-100 分类置信度
        "tradeable": bool,  # 是否适合开仓
        "metrics": dict,  # 诊断指标
    }
    """

    # ========== 特征计算 ==========
    er = calculate_er(closes, 20)
    momentum = calculate_price_momentum(closes, 20)
    atr_trend = calculate_atr_trend(atr_values, 20)
    adx = adx_values[current_idx] if current_idx < len(adx_values) and adx_values[current_idx] else 0.0
    adx_slope = calculate_adx_slope(adx_values[:current_idx + 1], 10)
    flip_count = count_flips_in_window(flip_indices, current_idx, 50)
    atr_current = atr_values[current_idx] if current_idx < len(atr_values) else 0.0
    volatility_spike = calculate_volatility_spike(highs[:current_idx + 1], lows[:current_idx + 1], atr_current, 10)

    # 价格与信号方向一致性
    momentum_aligned = (momentum > 0 and signal_direction > 0) or (momentum < 0 and signal_direction < 0)

    # ========== 六大市场状态判定（优化阈值）==========

    # 1. 恐慌释放期 (优先级最高)
    # 特征: 超大波动 + 快速单边 + 高ER + ADX飙升
    # 调整：降低波动尖峰要求 3.0 → 2.5
    if (volatility_spike > 2.5 and
        abs(momentum) > 4.0 and
        er > 0.35 and
        adx > 30 and
        adx_slope > 8):
        return {
            "regime": "panic_release",
            "regime_cn": "恐慌释放期",
            "confidence": min(100, 60 + volatility_spike * 5),
            "tradeable": True,  # 恐慌释放后的反弹机会
            "metrics": {
                "er": round(er, 3),
                "momentum": round(momentum, 2),
                "atr_trend": round(atr_trend, 2),
                "adx": round(adx, 1),
                "adx_slope": round(adx_slope, 1),
                "flip_count": flip_count,
                "volatility_spike": round(volatility_spike, 2),
            }
        }

    # 2. 趋势启动期（放宽版 v2.0 - 最终版）
    # 数据验证：58 笔，+1.08%，盈亏比 3.62 ✅
    # v2.1 收紧后表现反而变差，回滚到 v2.0
    if (0.15 < er < 0.5 and           # 从 0.25 降到 0.15
        12 < adx < 40 and             # 从 15-35 扩大到 12-40
        adx_slope > 3 and             # 从 5 降到 3
        atr_trend > 0 and             # 从 10 降到 0（波动率上升即可）
        flip_count <= 4 and           # 从 3 放宽到 4
        abs(momentum) > 0.5):         # 有明显动量
        return {
            "regime": "trend_initiation",
            "regime_cn": "趋势启动期",
            "confidence": 70 + min(30, adx_slope * 3),
            "tradeable": True,
            "metrics": {
                "er": round(er, 3),
                "momentum": round(momentum, 2),
                "atr_trend": round(atr_trend, 2),
                "adx": round(adx, 1),
                "adx_slope": round(adx_slope, 1),
                "flip_count": flip_count,
                "volatility_spike": round(volatility_spike, 2),
            }
        }

    # 3. 趋势运行期（放宽版 v2.0 - 标记待优化）
    # 数据验证：36 笔，-0.55%，胜率 30.6% ❌
    # 表现不佳，但 v2.1 收紧后更差，暂时保持 v2.0 设置
    # TODO: 需要更精细的特征工程或机器学习模型
    if (er > 0.28 and                 # 从 0.35 降到 0.28
        adx > 20 and                  # 从 25 降到 20
        -8 < adx_slope < 15 and       # ADX 斜率范围扩大
        flip_count <= 3 and           # 从 2 放宽到 3
        abs(momentum) > 2.0):         # 从 3.0 降到 2.0
        return {
            "regime": "trend_running",
            "regime_cn": "趋势运行期",
            "confidence": 75 + min(25, er * 40),
            "tradeable": True,
            "metrics": {
                "er": round(er, 3),
                "momentum": round(momentum, 2),
                "atr_trend": round(atr_trend, 2),
                "adx": round(adx, 1),
                "adx_slope": round(adx_slope, 1),
                "flip_count": flip_count,
                "volatility_spike": round(volatility_spike, 2),
            }
        }

    # 新增：4. 趋势延续期（v2.0 新增，v2.0 标记为可交易）
    # 特征：中等 ER + ADX 高位盘整 + 翻转少 + 有动量
    # 数据验证：33 笔，-0.33%，暂标记为可交易（贡献覆盖率）
    # 注意：单独表现不佳，但在整体可交易组合中可能有价值
    if (0.2 < er < 0.35 and
        18 < adx < 30 and
        -5 < adx_slope < 5 and        # ADX 平稳
        flip_count <= 3 and
        abs(momentum) > 1.0):
        return {
            "regime": "trend_continuation",
            "regime_cn": "趋势延续期",
            "confidence": 70,
            "tradeable": True,           # v2.0: 保持可交易
            "metrics": {
                "er": round(er, 3),
                "momentum": round(momentum, 2),
                "atr_trend": round(atr_trend, 2),
                "adx": round(adx, 1),
                "adx_slope": round(adx_slope, 1),
                "flip_count": flip_count,
                "volatility_spike": round(volatility_spike, 2),
            }
        }

    # 5. 趋势衰减期（收紧条件，更精准识别）
    # 调整：提高 ADX 要求，确保是从高位回落
    if (0.12 < er < 0.3 and           # ER 范围收紧
        adx > 22 and                  # 从 20 提高到 22
        adx_slope < -6 and            # 从 -5 收紧到 -6
        flip_count >= 3):
        return {
            "regime": "trend_exhaustion",
            "regime_cn": "趋势衰减期",
            "confidence": 65 + min(35, abs(adx_slope) * 2),
            "tradeable": False,  # 不开仓
            "metrics": {
                "er": round(er, 3),
                "momentum": round(momentum, 2),
                "atr_trend": round(atr_trend, 2),
                "adx": round(adx, 1),
                "adx_slope": round(adx_slope, 1),
                "flip_count": flip_count,
                "volatility_spike": round(volatility_spike, 2),
            }
        }

    # 6. 假突破期（收紧条件）
    # 调整：提高翻转次数要求，避免误判
    if (er < 0.12 and                 # 从 0.15 收紧到 0.12
        flip_count >= 5 and           # 从 4 提高到 5
        adx < 18 and                  # 从 20 降到 18
        atr_trend < 3):               # 从 5 降到 3
        return {
            "regime": "false_breakout",
            "regime_cn": "假突破期",
            "confidence": 60 + flip_count * 5,
            "tradeable": False,  # 不开仓
            "metrics": {
                "er": round(er, 3),
                "momentum": round(momentum, 2),
                "atr_trend": round(atr_trend, 2),
                "adx": round(adx, 1),
                "adx_slope": round(adx_slope, 1),
                "flip_count": flip_count,
                "volatility_spike": round(volatility_spike, 2),
            }
        }

    # 7. 震荡吸收期（默认/不开仓）
    # 其他所有情况
    return {
        "regime": "consolidation",
        "regime_cn": "震荡吸收期",
        "confidence": 50,
        "tradeable": False,  # 不开仓
        "metrics": {
            "er": round(er, 3),
            "momentum": round(momentum, 2),
            "atr_trend": round(atr_trend, 2),
            "adx": round(adx, 1),
            "adx_slope": round(adx_slope, 1),
            "flip_count": flip_count,
            "volatility_spike": round(volatility_spike, 2),
        }
    }
