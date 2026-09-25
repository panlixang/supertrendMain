"""
分析 st_signals_1h_features.csv 中的808笔信号，对每笔进行行情趋势分类
生成详细的分类统计报告和增强版 CSV
"""

import sys
import os
import pandas as pd
import numpy as np
from datetime import datetime

# 添加父目录到路径以导入模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_regime import classify_market_regime
from indicators import super_trend, ta_adx


def load_btc_1h_data():
    """加载 BTC 1小时数据用于指标计算"""
    # 这里需要加载完整的 K线数据
    # 假设有一个历史数据文件或者从 OKX 拉取
    # 为了演示，我们先使用特征列中的信息
    return None


def calculate_regime_from_features(row, idx, all_data):
    """
    根据特征列计算行情状态（优化版 v2 - 放宽阈值）
    由于没有完整K线，我们用特征列反推关键指标
    """

    # 从特征列提取指标
    er_change = row['前20根_ER变化']
    atr_change = row['前20根_ATR变化']
    adx_change = row['前20根_ADX变化']
    flip_count = row['前50根_ST翻转次数']
    momentum = row['前20根_涨跌幅']

    # 推算当前值（基于变化率）
    # 假设基准值
    er_base = 0.25
    adx_base = 25

    er = max(0, min(1, er_base + er_change))
    adx = max(0, min(100, adx_base + adx_change))
    adx_slope = adx_change  # 直接用ADX变化作为斜率
    atr_trend = atr_change * 100  # 转换为百分比

    signal_direction = row['信号']  # 1=多, -1=空

    # 计算波动率尖峰（用区间范围估算）
    # 假设没有完整数据，用默认值
    volatility_spike = 1.0

    # ========== 六大市场状态判定（优化阈值）==========

    # 1. 恐慌释放期（放宽条件）
    if (abs(momentum) > 4.0 and          # 从 5.0 降到 4.0
        er > 0.35 and                    # 从 0.4 降到 0.35
        adx > 30 and                     # 从 35 降到 30
        adx_slope > 8):                  # 从 10 降到 8
        return {
            "regime": "panic_release",
            "regime_cn": "恐慌释放期",
            "confidence": 75,
            "tradeable": True,
            "er": er,
            "momentum": momentum,
            "atr_trend": atr_trend,
            "adx": adx,
            "adx_slope": adx_slope,
            "flip_count": flip_count,
        }

    # 2. 趋势启动期（质量优先 v2.1）
    if (0.15 < er < 0.5 and              # 保持覆盖率
        12 < adx < 40 and
        adx_slope > 4 and                # v2.1: 从 3 提高到 4
        atr_trend > 5 and                # v2.1: 从 0 提高到 5
        flip_count <= 3 and              # v2.1: 从 4 收紧到 3
        abs(momentum) > 0.8):            # v2.1: 从 0.5 提高到 0.8
        return {
            "regime": "trend_initiation",
            "regime_cn": "趋势启动期",
            "confidence": 75,
            "tradeable": True,
            "er": er,
            "momentum": momentum,
            "atr_trend": atr_trend,
            "adx": adx,
            "adx_slope": adx_slope,
            "flip_count": flip_count,
        }

    # 3. 趋势运行期（质量优先 v2.1）
    if (er > 0.32 and                    # v2.1: 从 0.28 提高到 0.32
        adx > 22 and                     # v2.1: 从 20 提高到 22
        -8 < adx_slope < 15 and
        flip_count <= 2 and              # v2.1: 从 3 收紧到 2
        abs(momentum) > 2.5):            # v2.1: 从 2.0 提高到 2.5
        return {
            "regime": "trend_running",
            "regime_cn": "趋势运行期",
            "confidence": 80,
            "tradeable": True,
            "er": er,
            "momentum": momentum,
            "atr_trend": atr_trend,
            "adx": adx,
            "adx_slope": adx_slope,
            "flip_count": flip_count,
        }

    # 新增：4. 趋势延续期（v2.1: 改为不可交易）
    if (0.2 < er < 0.35 and
        18 < adx < 30 and
        -5 < adx_slope < 5 and           # ADX 平稳
        flip_count <= 3 and
        abs(momentum) > 1.0):
        return {
            "regime": "trend_continuation",
            "regime_cn": "趋势延续期",
            "confidence": 70,
            "tradeable": False,          # v2.1: 改为不可交易
            "er": er,
            "momentum": momentum,
            "atr_trend": atr_trend,
            "adx": adx,
            "adx_slope": adx_slope,
            "flip_count": flip_count,
        }

    # 5. 趋势衰减期（收紧条件）
    if (0.12 < er < 0.3 and              # 范围收紧
        adx > 22 and                     # 从 20 提高到 22
        adx_slope < -6 and               # 从 -5 收紧到 -6
        flip_count >= 3):
        return {
            "regime": "trend_exhaustion",
            "regime_cn": "趋势衰减期",
            "confidence": 70,
            "tradeable": False,
            "er": er,
            "momentum": momentum,
            "atr_trend": atr_trend,
            "adx": adx,
            "adx_slope": adx_slope,
            "flip_count": flip_count,
        }

    # 6. 假突破期（收紧条件）
    if (er < 0.12 and                    # 从 0.15 收紧到 0.12
        flip_count >= 5 and              # 从 4 提高到 5
        adx < 18 and                     # 从 20 降到 18
        atr_trend < 3):                  # 从 5 降到 3
        return {
            "regime": "false_breakout",
            "regime_cn": "假突破期",
            "confidence": 65,
            "tradeable": False,
            "er": er,
            "momentum": momentum,
            "atr_trend": atr_trend,
            "adx": adx,
            "adx_slope": adx_slope,
            "flip_count": flip_count,
        }

    # 7. 震荡吸收期（默认）
    return {
        "regime": "consolidation",
        "regime_cn": "震荡吸收期",
        "confidence": 50,
        "tradeable": False,
        "er": er,
        "momentum": momentum,
        "atr_trend": atr_trend,
        "adx": adx,
        "adx_slope": adx_slope,
        "flip_count": flip_count,
    }


def analyze_signals():
    """分析所有信号并生成报告"""

    # 读取原始数据
    csv_path = os.path.join(os.path.dirname(__file__), 'st_signals_1h_features.csv')
    df = pd.read_csv(csv_path, encoding='utf-8-sig')

    print(f"加载了 {len(df)} 笔信号")
    print(f"列名: {df.columns.tolist()}")

    # 对每笔信号进行分类
    regimes = []
    for idx, row in df.iterrows():
        regime = calculate_regime_from_features(row, idx, df)
        regimes.append(regime)

    # 添加到 DataFrame
    df['行情状态'] = [r['regime'] for r in regimes]
    df['行情状态_中文'] = [r['regime_cn'] for r in regimes]
    df['分类置信度'] = [r['confidence'] for r in regimes]
    df['可交易'] = [r['tradeable'] for r in regimes]
    df['ER估算'] = [round(r['er'], 3) for r in regimes]
    df['ADX估算'] = [round(r['adx'], 1) for r in regimes]
    df['ADX斜率'] = [round(r['adx_slope'], 1) for r in regimes]

    # 保存增强版 CSV
    output_path = os.path.join(os.path.dirname(__file__), 'st_signals_1h_with_regime.csv')
    df.to_csv(output_path, index=False, encoding='utf-8-sig')
    print(f"\n已保存增强版数据到: {output_path}")

    # ========== 统计分析 ==========
    print("\n" + "="*80)
    print("行情状态分布统计")
    print("="*80)

    regime_counts = df['行情状态_中文'].value_counts()
    for regime, count in regime_counts.items():
        pct = count / len(df) * 100
        print(f"{regime:12s}: {count:4d} 笔 ({pct:5.1f}%)")

    print("\n" + "="*80)
    print("各状态盈亏统计")
    print("="*80)

    for regime in regime_counts.index:
        subset = df[df['行情状态_中文'] == regime]
        win_count = len(subset[subset['盈亏'] > 0])
        win_rate = win_count / len(subset) * 100 if len(subset) > 0 else 0
        avg_pnl = subset['盈亏'].mean()

        print(f"\n{regime}:")
        print(f"  信号数: {len(subset)} 笔")
        print(f"  胜率: {win_rate:.1f}%")
        print(f"  平均盈亏: {avg_pnl:.2f}%")
        print(f"  盈亏比: {subset[subset['盈亏'] > 0]['盈亏'].mean() / abs(subset[subset['盈亏'] <= 0]['盈亏'].mean()):.2f}"
              if len(subset[subset['盈亏'] <= 0]) > 0 else "  盈亏比: N/A")

    # 可交易 vs 不可交易对比
    print("\n" + "="*80)
    print("可交易状态 vs 不可交易状态对比")
    print("="*80)

    tradeable = df[df['可交易'] == True]
    non_tradeable = df[df['可交易'] == False]

    print(f"\n可交易状态 ({len(tradeable)} 笔):")
    print(f"  胜率: {len(tradeable[tradeable['盈亏'] > 0]) / len(tradeable) * 100:.1f}%")
    print(f"  平均盈亏: {tradeable['盈亏'].mean():.2f}%")

    print(f"\n不可交易状态 ({len(non_tradeable)} 笔):")
    print(f"  胜率: {len(non_tradeable[non_tradeable['盈亏'] > 0]) / len(non_tradeable) * 100:.1f}%")
    print(f"  平均盈亏: {non_tradeable['盈亏'].mean():.2f}%")

    # 止盈止损分布
    print("\n" + "="*80)
    print("各状态止盈止损分布")
    print("="*80)

    for regime in regime_counts.index:
        subset = df[df['行情状态_中文'] == regime]
        exit_counts = subset['止盈止损'].value_counts()
        print(f"\n{regime}:")
        for exit_type, count in exit_counts.items():
            pct = count / len(subset) * 100
            print(f"  {exit_type}: {count:3d} 笔 ({pct:5.1f}%)")

    # 生成 Excel 报告
    excel_path = os.path.join(os.path.dirname(__file__), 'regime_analysis_report.xlsx')
    with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
        # 完整数据
        df.to_excel(writer, sheet_name='完整数据', index=False)

        # 统计汇总
        summary = pd.DataFrame({
            '行情状态': regime_counts.index,
            '信号数量': regime_counts.values,
            '占比': (regime_counts.values / len(df) * 100).round(1),
        })
        for regime in regime_counts.index:
            subset = df[df['行情状态_中文'] == regime]
            win_rate = len(subset[subset['盈亏'] > 0]) / len(subset) * 100
            avg_pnl = subset['盈亏'].mean()
            summary.loc[summary['行情状态'] == regime, '胜率(%)'] = round(win_rate, 1)
            summary.loc[summary['行情状态'] == regime, '平均盈亏(%)'] = round(avg_pnl, 2)

        summary.to_excel(writer, sheet_name='统计汇总', index=False)

        # 各状态详情
        for regime in regime_counts.index:
            subset = df[df['行情状态_中文'] == regime]
            sheet_name = regime[:31]  # Excel sheet name 限制
            subset.to_excel(writer, sheet_name=sheet_name, index=False)

    print(f"\n已生成 Excel 报告: {excel_path}")

    return df


if __name__ == "__main__":
    df = analyze_signals()
