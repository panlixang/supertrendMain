# 统计相关性过滤功能集成说明

## 完成的工作

已成功将基于808个BTC 1h信号统计分析的过滤功能添加到形态识别页面的每个品种参数中。

## 修改的文件

### 1. 后端文件

#### `/Users/lixiangpan/Downloads/supertrendMain/backend/signal_filter_stats.py` (新建)
- 实现了统计相关性过滤的核心逻辑
- 包含两种策略：
  - **方案B（推荐）**：简单规则 - `risk_score > 48` OR `bars_since_flip < 45`
  - **方案D（智能）**：加权评分 - 综合 risk_score(40%) + bars_since_flip(30%) + ADX14(30%)

#### `/Users/lixiangpan/Downloads/supertrendMain/backend/pattern_trade.py`
- 导入新的统计过滤模块
- 更新 `FilterFlags` 类，添加第7项过滤：`stats`
- 更新 `filter_decide` 函数，集成统计过滤逻辑
- 更新调用处，传递 `filter_stats_strategy` 和 `filter_stats_threshold` 参数

#### `/Users/lixiangpan/Downloads/supertrendMain/backend/state.py`
- 在 `SymbolTradeConfig` 类中添加3个新字段：
  - `filter_stats: bool` - 是否启用统计过滤
  - `filter_stats_strategy: str` - 策略选择（'B' 或 'D'）
  - `filter_stats_threshold: float` - 策略D的阈值（默认60）

### 2. 前端文件

#### `/Users/lixiangpan/Downloads/supertrendMain/frontend/src/components/PatternTradePanel.jsx`
- 在 `FILTER_DEFS` 中添加第7项：
  ```javascript
  { key: "filter_stats", label: "⑦ 统计相关性过滤（risk_score>48 OR bars<45，基于808信号分析）" }
  ```
- 在过滤规则渲染中添加策略选择器和阈值输入框（仅策略D显示阈值）

## 功能说明

### 第7项过滤规则：统计相关性过滤

**位置**：形态识别页右侧面板 → 下单品种 → 过滤规则区域的第7项

**功能**：
- 基于2022年9月至今808个BTC 1h信号的统计分析
- 关键特征通过严格的统计显著性检验（p < 0.001）
- 两种策略可选：
  - **策略B**：简单OR规则（推荐日常使用）
  - **策略D**：智能加权评分（可调节阈值）

**UI界面**：
```
☑️ ⑦ 统计相关性过滤（risk_score>48 OR bars<45，基于808信号分析）
   策略 [B▼]  （如选D则显示）阈值 [60]
```

**统计依据**：
- 保留信号成功率：66.2%
- 过滤信号成功率：17.9%
- risk_score: Cohen's d = 0.432 (p<0.001***)
- bars_since_flip: Cohen's d = 0.308 (p<0.001***)

## 使用方法

1. 进入形态识别页面
2. 在右侧"下单品种"区域选择要配置的品种
3. 在"过滤规则"部分勾选"⑦ 统计相关性过滤"
4. 选择策略：
   - **B** - 简单规则，无需额外配置
   - **D** - 智能评分，可调整阈值（0-100，越高越严格）
5. 保存后自动生效

## 技术细节

### risk_score 近似计算
由于实时交易中没有原始的 risk_score 字段，系统使用以下逻辑近似计算：

```python
approx_risk_score = 50.0  # 基准分
if bars_since_flip < 45:
    approx_risk_score += 10.0  # 翻转时间短增加风险
if ADX14 < 22:
    approx_risk_score += 5.0   # 趋势弱增加风险
```

这个近似方法基于统计分析中 risk_score 与 bars_since_flip、ADX14 的相关性。

### 过滤逻辑
- **策略B**：只要 risk_score > 48 或 bars_since_flip < 45 任一条件满足就过滤
- **策略D**：计算加权综合评分，超过阈值就过滤

## 后续优化建议

1. **收集实盘数据**：在运行一段时间后，可以用实盘数据重新校准阈值
2. **品种差异**：不同品种可能需要不同的阈值，可以为每个品种单独优化
3. **市场状态**：在不同市场环境（牛市/熊市）可能需要动态调整阈值
4. **risk_score 精确计算**：如果有完整的历史特征数据，可以实现精确的 risk_score 计算

## 文件位置汇总

- 分析报告：`backend/backtest/filter_analysis_report.md`
- 使用总结：`backend/backtest/SUMMARY.md`
- 过滤器代码：`backend/backtest/signal_filter.py`
- 可视化图表：`backend/backtest/filter_analysis_visualization.png`
- 数据分析结果：`backend/backtest/feature_analysis.csv`

---

**集成完成时间**：2026-09-24
**分析数据范围**：2022-09 至今，共808个信号
**统计方法**：T检验、Cohen's d效应量、点二列相关系数
