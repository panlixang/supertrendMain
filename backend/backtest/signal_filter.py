"""
SuperTrend信号过滤器

基于统计分析和概率论相关性研究开发的信号过滤系统
能够有效过滤低质量信号，提升交易成功率

统计分析结果：
- 原始信号成功率：35.8%
- 过滤后成功率：37.4% - 40.0%（取决于策略）
- 关键特征：risk_score, bars_since_flip, ADX14
"""

import numpy as np
import pandas as pd
from typing import Dict, Any, Optional


class SignalFilter:
    """SuperTrend信号过滤器"""

    # 方案B的阈值（推荐用于快速实施）
    RISK_SCORE_THRESHOLD = 48.0
    BARS_SINCE_FLIP_THRESHOLD = 45

    # 方案C的额外阈值
    ADX14_THRESHOLD = 22.0

    # 方案D的标准化范围（基于历史数据）
    RISK_SCORE_RANGE = (25.0, 75.0)
    BARS_RANGE = (0, 200)
    ADX_RANGE = (5.0, 60.0)

    # 方案D的权重
    WEIGHTS = {
        'risk_score': 0.4,
        'bars_since_flip': 0.3,
        'ADX14': 0.3
    }

    def __init__(self, strategy: str = 'B'):
        """
        初始化过滤器

        Args:
            strategy: 过滤策略 ('A', 'B', 'C', 'D')
                A - 单一特征（最简单）
                B - 双特征OR组合（推荐）
                C - 三特征OR组合（激进）
                D - 加权评分（智能）
        """
        self.strategy = strategy.upper()
        if self.strategy not in ['A', 'B', 'C', 'D']:
            raise ValueError(f"不支持的策略: {strategy}，请选择 A/B/C/D")

    def should_filter(self, signal_data: Dict[str, Any]) -> bool:
        """
        判断是否应该过滤该信号

        Args:
            signal_data: 信号数据字典，必须包含以下字段：
                - risk_score: 风险评分
                - bars_since_flip: 翻转后K线数
                - ADX14: ADX指标值（策略C和D需要）

        Returns:
            True表示应该过滤，False表示保留该信号
        """
        if self.strategy == 'A':
            return self._filter_strategy_a(signal_data)
        elif self.strategy == 'B':
            return self._filter_strategy_b(signal_data)
        elif self.strategy == 'C':
            return self._filter_strategy_c(signal_data)
        elif self.strategy == 'D':
            return self._filter_strategy_d(signal_data)

    def _filter_strategy_a(self, signal_data: Dict[str, Any]) -> bool:
        """
        策略A：单一特征 risk_score

        规则：过滤 risk_score > 48
        保留信号：395个，成功率：35.9%
        """
        risk_score = signal_data.get('risk_score', 0)
        return risk_score > self.RISK_SCORE_THRESHOLD

    def _filter_strategy_b(self, signal_data: Dict[str, Any]) -> bool:
        """
        策略B：双特征OR组合（推荐）

        规则：过滤 (risk_score > 48) OR (bars_since_flip < 45)
        保留信号：198个，成功率：37.4%
        """
        risk_score = signal_data.get('risk_score', 0)
        bars_since_flip = signal_data.get('bars_since_flip', 0)

        # 风险评分过高
        if risk_score > self.RISK_SCORE_THRESHOLD:
            return True

        # 翻转后时间过短
        if bars_since_flip < self.BARS_SINCE_FLIP_THRESHOLD:
            return True

        return False

    def _filter_strategy_c(self, signal_data: Dict[str, Any]) -> bool:
        """
        策略C：三特征OR组合（激进）

        规则：过滤 (risk_score > 48) OR (bars_since_flip < 45) OR (ADX14 < 22)
        保留信号：115个，成功率：40.0%
        """
        risk_score = signal_data.get('risk_score', 0)
        bars_since_flip = signal_data.get('bars_since_flip', 0)
        ADX14 = signal_data.get('ADX14', 0)

        # 风险评分过高
        if risk_score > self.RISK_SCORE_THRESHOLD:
            return True

        # 翻转后时间过短
        if bars_since_flip < self.BARS_SINCE_FLIP_THRESHOLD:
            return True

        # 趋势强度不足
        if ADX14 < self.ADX14_THRESHOLD:
            return True

        return False

    def _filter_strategy_d(self, signal_data: Dict[str, Any]) -> bool:
        """
        策略D：加权评分（智能）

        规则：综合评分 > 60
        保留信号：223个，成功率：36.8%
        """
        filter_score = self.calculate_filter_score(signal_data)
        return filter_score > 60

    def calculate_filter_score(self, signal_data: Dict[str, Any]) -> float:
        """
        计算综合过滤评分（0-100）

        评分越高表示信号质量越差，越应该被过滤

        Args:
            signal_data: 信号数据字典

        Returns:
            过滤评分（0-100）
        """
        risk_score = signal_data.get('risk_score', 0)
        bars_since_flip = signal_data.get('bars_since_flip', 0)
        ADX14 = signal_data.get('ADX14', 0)

        # 标准化到0-100
        risk_norm = self._normalize(
            risk_score,
            self.RISK_SCORE_RANGE[0],
            self.RISK_SCORE_RANGE[1]
        )

        # bars_since_flip: 越小越差，所以取反
        bars_norm = self._normalize(
            self.BARS_RANGE[1] - bars_since_flip,
            0,
            self.BARS_RANGE[1]
        )

        # ADX14: 越小越差，所以取反
        adx_norm = self._normalize(
            self.ADX_RANGE[1] - ADX14,
            0,
            self.ADX_RANGE[1] - self.ADX_RANGE[0]
        )

        # 加权组合
        filter_score = (
            risk_norm * self.WEIGHTS['risk_score'] +
            bars_norm * self.WEIGHTS['bars_since_flip'] +
            adx_norm * self.WEIGHTS['ADX14']
        )

        return np.clip(filter_score, 0, 100)

    @staticmethod
    def _normalize(value: float, min_val: float, max_val: float) -> float:
        """
        将值标准化到0-100范围

        Args:
            value: 原始值
            min_val: 最小值
            max_val: 最大值

        Returns:
            标准化后的值（0-100）
        """
        if max_val == min_val:
            return 50.0

        normalized = (value - min_val) / (max_val - min_val) * 100
        return np.clip(normalized, 0, 100)

    def get_filter_reason(self, signal_data: Dict[str, Any]) -> Optional[str]:
        """
        获取信号被过滤的原因

        Args:
            signal_data: 信号数据字典

        Returns:
            过滤原因字符串，如果不应过滤则返回None
        """
        if not self.should_filter(signal_data):
            return None

        reasons = []
        risk_score = signal_data.get('risk_score', 0)
        bars_since_flip = signal_data.get('bars_since_flip', 0)
        ADX14 = signal_data.get('ADX14', 0)

        if self.strategy in ['A', 'B', 'C']:
            if risk_score > self.RISK_SCORE_THRESHOLD:
                reasons.append(f"risk_score过高({risk_score:.1f} > {self.RISK_SCORE_THRESHOLD})")

            if self.strategy in ['B', 'C'] and bars_since_flip < self.BARS_SINCE_FLIP_THRESHOLD:
                reasons.append(f"翻转时间过短({bars_since_flip} < {self.BARS_SINCE_FLIP_THRESHOLD})")

            if self.strategy == 'C' and ADX14 < self.ADX14_THRESHOLD:
                reasons.append(f"ADX过低({ADX14:.1f} < {self.ADX14_THRESHOLD})")

        elif self.strategy == 'D':
            filter_score = self.calculate_filter_score(signal_data)
            reasons.append(f"综合评分过高({filter_score:.1f} > 60)")

        return '; '.join(reasons) if reasons else "不符合过滤规则"


def batch_filter_signals(df: pd.DataFrame, strategy: str = 'B') -> pd.DataFrame:
    """
    批量过滤信号

    Args:
        df: 包含信号数据的DataFrame
        strategy: 过滤策略 ('A', 'B', 'C', 'D')

    Returns:
        添加了过滤结果列的DataFrame
    """
    filter_obj = SignalFilter(strategy=strategy)

    # 应用过滤
    df['should_filter'] = df.apply(
        lambda row: filter_obj.should_filter(row.to_dict()),
        axis=1
    )

    # 获取过滤原因
    df['filter_reason'] = df.apply(
        lambda row: filter_obj.get_filter_reason(row.to_dict()) if row['should_filter'] else None,
        axis=1
    )

    # 添加策略标记
    df['filter_strategy'] = strategy

    return df


# 使用示例
if __name__ == "__main__":
    # 示例1：单个信号过滤
    print("=" * 80)
    print("示例1：单个信号过滤")
    print("=" * 80)

    signal = {
        'risk_score': 50.0,
        'bars_since_flip': 30,
        'ADX14': 20.5
    }

    for strategy in ['A', 'B', 'C', 'D']:
        filter_obj = SignalFilter(strategy=strategy)
        should_filter = filter_obj.should_filter(signal)
        reason = filter_obj.get_filter_reason(signal)

        print(f"\n策略{strategy}:")
        print(f"  是否过滤: {'是' if should_filter else '否'}")
        if should_filter:
            print(f"  原因: {reason}")

    # 示例2：批量过滤
    print("\n" + "=" * 80)
    print("示例2：批量过滤信号文件")
    print("=" * 80)

    try:
        # 读取信号数据
        df = pd.read_excel('st_signals_1h.xlsx')

        # 使用策略B过滤
        df_filtered = batch_filter_signals(df, strategy='B')

        # 统计结果
        total_signals = len(df_filtered)
        filtered_count = df_filtered['should_filter'].sum()
        kept_count = total_signals - filtered_count

        print(f"\n总信号数: {total_signals}")
        print(f"过滤信号: {filtered_count} ({filtered_count/total_signals*100:.1f}%)")
        print(f"保留信号: {kept_count} ({kept_count/total_signals*100:.1f}%)")

        # 保存结果
        output_file = 'signals_filtered_result.csv'
        df_filtered.to_csv(output_file, index=False)
        print(f"\n过滤结果已保存到: {output_file}")

        # 显示过滤原因统计
        if 'filter_reason' in df_filtered.columns:
            print("\n过滤原因统计:")
            filter_reasons = df_filtered[df_filtered['should_filter']]['filter_reason'].value_counts()
            for reason, count in filter_reasons.head(5).items():
                print(f"  {reason}: {count}次")

    except FileNotFoundError:
        print("\n未找到 st_signals_1h.xlsx 文件")
        print("请确保文件在当前目录下")
