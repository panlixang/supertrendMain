"""
可视化分析结果生成器

生成过滤效果对比图表
"""

import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np

# 设置中文字体和样式
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
sns.set_style("whitegrid")

# 读取数据
df = pd.read_csv('signals_with_predictions.csv')

# 创建图表
fig = plt.figure(figsize=(16, 12))

# 1. 成功率对比
ax1 = plt.subplot(2, 3, 1)
categories = ['All Signals', 'Filtered Out', 'Kept']
success_rates = [35.8, 17.9, 66.2]
colors = ['#95a5a6', '#e74c3c', '#2ecc71']
bars = ax1.bar(categories, success_rates, color=colors, alpha=0.7, edgecolor='black', linewidth=1.5)
ax1.set_ylabel('Success Rate (%)', fontsize=12, fontweight='bold')
ax1.set_title('1. Success Rate Comparison', fontsize=14, fontweight='bold')
ax1.set_ylim(0, 80)
for bar, rate in zip(bars, success_rates):
    height = bar.get_height()
    ax1.text(bar.get_x() + bar.get_width()/2., height,
             f'{rate:.1f}%', ha='center', va='bottom', fontsize=11, fontweight='bold')
ax1.grid(axis='y', alpha=0.3)

# 2. 信号数量分布
ax2 = plt.subplot(2, 3, 2)
signal_counts = [808, 509, 299]
bars2 = ax2.bar(categories, signal_counts, color=colors, alpha=0.7, edgecolor='black', linewidth=1.5)
ax2.set_ylabel('Number of Signals', fontsize=12, fontweight='bold')
ax2.set_title('2. Signal Count Distribution', fontsize=14, fontweight='bold')
for bar, count in zip(bars2, signal_counts):
    height = bar.get_height()
    ax2.text(bar.get_x() + bar.get_width()/2., height,
             f'{count}\n({count/808*100:.1f}%)', ha='center', va='bottom', fontsize=10, fontweight='bold')
ax2.grid(axis='y', alpha=0.3)

# 3. 特征重要性排名
ax3 = plt.subplot(2, 3, 3)
features = ['risk_score', 'bars_since_flip', 'pause_ST', 'ADX14', 'sc_adx', 'sc_atr']
cohens_d = [0.432, 0.308, 0.261, 0.212, 0.177, 0.162]
bars3 = ax3.barh(features, cohens_d, color='#3498db', alpha=0.7, edgecolor='black', linewidth=1.5)
ax3.set_xlabel("Cohen's d (Effect Size)", fontsize=12, fontweight='bold')
ax3.set_title("3. Feature Importance Ranking", fontsize=14, fontweight='bold')
ax3.invert_yaxis()
for bar, value in zip(bars3, cohens_d):
    width = bar.get_width()
    ax3.text(width, bar.get_y() + bar.get_height()/2.,
             f'{value:.3f}', ha='left', va='center', fontsize=10, fontweight='bold',
             bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.8))
ax3.axvline(x=0.2, color='green', linestyle='--', alpha=0.5, label='Small effect')
ax3.axvline(x=0.5, color='orange', linestyle='--', alpha=0.5, label='Medium effect')
ax3.legend(fontsize=9)
ax3.grid(axis='x', alpha=0.3)

# 4. 关键特征分布对比 - risk_score
ax4 = plt.subplot(2, 3, 4)
kept_risk = df[df['to_filter'] == 0]['risk_score'].dropna()
filtered_risk = df[df['to_filter'] == 1]['risk_score'].dropna()
ax4.hist(kept_risk, bins=20, alpha=0.6, label='Kept Signals', color='#2ecc71', edgecolor='black')
ax4.hist(filtered_risk, bins=20, alpha=0.6, label='Filtered Signals', color='#e74c3c', edgecolor='black')
ax4.axvline(x=48, color='red', linestyle='--', linewidth=2, label='Threshold=48')
ax4.set_xlabel('risk_score', fontsize=12, fontweight='bold')
ax4.set_ylabel('Frequency', fontsize=12, fontweight='bold')
ax4.set_title('4. risk_score Distribution', fontsize=14, fontweight='bold')
ax4.legend(fontsize=9)
ax4.grid(axis='y', alpha=0.3)

# 5. 关键特征分布对比 - bars_since_flip
ax5 = plt.subplot(2, 3, 5)
kept_bars = df[df['to_filter'] == 0]['bars_since_flip'].dropna()
filtered_bars = df[df['to_filter'] == 1]['bars_since_flip'].dropna()
ax5.hist(kept_bars, bins=20, alpha=0.6, label='Kept Signals', color='#2ecc71', edgecolor='black')
ax5.hist(filtered_bars, bins=20, alpha=0.6, label='Filtered Signals', color='#e74c3c', edgecolor='black')
ax5.axvline(x=45, color='red', linestyle='--', linewidth=2, label='Threshold=45')
ax5.set_xlabel('bars_since_flip', fontsize=12, fontweight='bold')
ax5.set_ylabel('Frequency', fontsize=12, fontweight='bold')
ax5.set_title('5. bars_since_flip Distribution', fontsize=14, fontweight='bold')
ax5.legend(fontsize=9)
ax5.grid(axis='y', alpha=0.3)

# 6. 各策略效果对比
ax6 = plt.subplot(2, 3, 6)
strategies = ['Strategy A\n(risk_score)', 'Strategy B\n(2 features)', 'Strategy C\n(3 features)', 'Strategy D\n(weighted)']
kept_signals = [395, 198, 115, 223]
success_rates_strategy = [35.9, 37.4, 40.0, 36.8]

x = np.arange(len(strategies))
width = 0.35

bars_left = ax6.bar(x - width/2, kept_signals, width, label='Kept Signals', color='#3498db', alpha=0.7, edgecolor='black')
ax6_right = ax6.twinx()
bars_right = ax6_right.bar(x + width/2, success_rates_strategy, width, label='Success Rate (%)', color='#2ecc71', alpha=0.7, edgecolor='black')

ax6.set_xlabel('Strategy', fontsize=12, fontweight='bold')
ax6.set_ylabel('Kept Signals Count', fontsize=11, fontweight='bold', color='#3498db')
ax6_right.set_ylabel('Success Rate (%)', fontsize=11, fontweight='bold', color='#2ecc71')
ax6.set_title('6. Strategy Comparison', fontsize=14, fontweight='bold')
ax6.set_xticks(x)
ax6.set_xticklabels(strategies, fontsize=9)
ax6.tick_params(axis='y', labelcolor='#3498db')
ax6_right.tick_params(axis='y', labelcolor='#2ecc71')

# 添加数值标签
for bar, value in zip(bars_left, kept_signals):
    height = bar.get_height()
    ax6.text(bar.get_x() + bar.get_width()/2., height,
             f'{value}', ha='center', va='bottom', fontsize=9, fontweight='bold')

for bar, value in zip(bars_right, success_rates_strategy):
    height = bar.get_height()
    ax6_right.text(bar.get_x() + bar.get_width()/2., height,
                   f'{value:.1f}%', ha='center', va='bottom', fontsize=9, fontweight='bold')

ax6.grid(axis='y', alpha=0.3)

# 总标题
fig.suptitle('SuperTrend Signal Filter Analysis - Statistical Results',
             fontsize=18, fontweight='bold', y=0.995)

plt.tight_layout()
plt.savefig('filter_analysis_visualization.png', dpi=300, bbox_inches='tight')
print("✅ 可视化图表已保存: filter_analysis_visualization.png")

# 创建第二个图：详细的箱线图对比
fig2, axes = plt.subplots(2, 3, figsize=(15, 10))
fig2.suptitle('Feature Distribution Comparison (Kept vs Filtered)',
              fontsize=16, fontweight='bold')

features_to_plot = [
    ('risk_score', 'Risk Score'),
    ('bars_since_flip', 'Bars Since Flip'),
    ('ADX14', 'ADX14'),
    ('ATR_pct', 'ATR %'),
    ('ER20', 'ER20'),
    ('vol_ratio', 'Volume Ratio')
]

for idx, (feature, title) in enumerate(features_to_plot):
    ax = axes[idx // 3, idx % 3]

    if feature in df.columns:
        data_to_plot = [
            df[df['to_filter'] == 0][feature].dropna(),
            df[df['to_filter'] == 1][feature].dropna()
        ]

        bp = ax.boxplot(data_to_plot, labels=['Kept', 'Filtered'],
                        patch_artist=True, showmeans=True)

        bp['boxes'][0].set_facecolor('#2ecc71')
        bp['boxes'][1].set_facecolor('#e74c3c')
        bp['boxes'][0].set_alpha(0.6)
        bp['boxes'][1].set_alpha(0.6)

        ax.set_title(title, fontsize=12, fontweight='bold')
        ax.set_ylabel('Value', fontsize=10)
        ax.grid(axis='y', alpha=0.3)

plt.tight_layout()
plt.savefig('feature_boxplot_comparison.png', dpi=300, bbox_inches='tight')
print("✅ 箱线图对比已保存: feature_boxplot_comparison.png")

print("\n" + "="*80)
print("📊 可视化分析完成！")
print("="*80)
print("\n生成的图表文件:")
print("1. filter_analysis_visualization.png - 6个关键分析图表")
print("2. feature_boxplot_comparison.png - 特征分布箱线图对比")
print("\n这些图表展示了:")
print("  ✓ 过滤前后成功率对比")
print("  ✓ 信号数量分布")
print("  ✓ 特征重要性排名")
print("  ✓ 关键特征的分布差异")
print("  ✓ 各过滤策略的效果对比")
