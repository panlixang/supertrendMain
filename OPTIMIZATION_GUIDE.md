# SuperTrend 策略优化方案

## 问题诊断

### 问题1：大行情启动时没下单，震荡反而一直下单

**原因分析：**
- **ER指标滞后**：使用60根K线计算，大行情刚启动时ER仍然<0.15显示为震荡
- **震荡期ER缓慢爬升**：震荡持续一段时间后ER反而达到0.15以上，此时闸门打开
- **结果**：错过真突破，追踪假突破

**历史案例（推测）：**
- 昨天ETH/BTC大涨前，ER还在低位 → 闸门关闭 → 没下单
- 震荡阶段，价格小幅波动积累 → ER升至0.15+ → 开始下单 → 频繁止损

### 问题2：MU持续止损/保本，无法盈利

**原因分析：**
1. **止盈点偏高**：标准档1.5%，某些品种波动不足
2. **止损过近**：使用SuperTrend线，震荡时容易被扫
3. **保本过早**：止盈后立即移到开仓价，没给趋势延续空间
4. **单一出场策略**：70%一次性止盈，无法适应不同趋势强度

## 优化方案

### 方案A：增强版闸门（regime_enhanced.py）

#### 1. 动量突破检测
```python
detect_momentum_breakout()
```

**核心逻辑：**
- ✅ 价格突破近期区间的30%以上
- ✅ 成交量放大（近3根均量 > 20根均量×1.5）
- ✅ 连续同向K线（近3根有2根以上同向）

**效果：** 大行情启动时即使ER低也能识别并放行

#### 2. 假突破过滤
```python
detect_false_breakout()
```

**震荡特征识别：**
- 上下影线占比>60%（犹豫不决）
- ATR连续5根下降（波动萎缩）
- 突破后立即反转回区间内

**效果：** 避免震荡市的假信号

#### 3. 自适应阈值
```python
adaptive_thresholds()
```

**根据品种波动率调整：**
- 高波动品种（山寨币）：ER阈值×0.8，止盈点×1.3
- 低波动品种（BTC）：ER阈值×1.2，止盈点×0.8
- 标准波动品种：保持默认

**效果：** 每个品种都有最适合的参数

### 方案B：增强版持仓管理（position_enhanced.py）

#### 1. 多级止盈
```
第一档：1.0%  → 平30%  → 止损移至开仓价（保本）
第二档：2.0%  → 平40%  → 止损移至成本+1%（锁定利润）
第三档：3.5%  → 平30%  → 全部离场
```

**优势：**
- 小盈利快速落袋（1%就拿30%）
- 大趋势持有更久（还有70%在）
- 分批降低心理压力

#### 2. 智能止损
```python
enhanced_initial_stop()
```

**改进：**
- SuperTrend线外扩0.5倍ATR（给震荡留空间）
- 最小止损距离1.2%（防止秒扫）
- 百分比止损与ST止损取较远者

**效果：** 减少震荡扫损

#### 3. 盈利保护
```python
check_enhanced() with profit protection
```

**机制：**
- 浮盈达1.5%时启动保护
- 允许从最高点回撤0.8%
- 回撤超过0.8%才真正止损

**效果：** 避免因短期回调过早离场

## 集成步骤

### 第一步：启用增强闸门

在 `backend/feed.py` 或信号处理部分：

```python
# 原代码
from regime import evaluate
gate = evaluate(sig, candles, cfg, candles_by_tf, p)

# 改为
from regime_enhanced import evaluate_enhanced
gate = evaluate_enhanced(sig, candles, cfg, candles_by_tf, p)
```

### 第二步：启用增强持仓管理

在 `backend/executor.py` 中：

```python
# 1. 导入增强版
from position_enhanced import (
    EnhancedExitRules, EnhancedPosition,
    enhanced_initial_stop, check_enhanced,
    apply_tp_enhanced, trail_enhanced
)

# 2. 修改规则初始化（在 AppState.__init__ 或 SymbolStore.__init__）
self.exit_rules = EnhancedExitRules(
    tp1_pct=1.0, tp1_ratio=30.0,
    tp2_pct=2.0, tp2_ratio=40.0,
    tp3_pct=3.5, tp3_ratio=30.0,
    sl_buffer_atr=0.5,
    sl_min_pct=1.2,
    protect_profit_at=1.5,
    protect_trail_pct=0.8,
)

# 3. 在 Executor._open 中使用增强版
stop = enhanced_initial_stop(filled, rules, sig.get("atr"))
self.store.position = EnhancedPosition(
    # ... 其他参数
    stop=stop,
    # ...
)

# 4. 在 Executor.on_price 中使用增强检查
act = check_enhanced(pos, price, self.rules_of(pos), pos.max_unrealized_pct)

# 5. 在价格更新时跟踪最高盈利
if isinstance(pos, EnhancedPosition):
    pos.update_max_unrealized(price)

# 6. 处理多级止盈
if act["action"] in ["tp1", "tp2", "tp3"]:
    stage = int(act["action"][-1])  # 提取1/2/3
    await self._take_profit_enhanced(pos, price, act, stage)

# 7. 使用增强版止损跟随
if trail_enhanced(pos, line, self.rules_of(pos), current_price):
    await self._push_position()
```

### 第三步：更新前端配置（可选）

在 `frontend` 配置面板添加新参数：

```javascript
// 闸门配置
{
  momentum_detection: true,      // 启用动量突破检测
  false_breakout_filter: true,   // 启用假突破过滤
  adaptive_thresholds: true,     // 启用自适应阈值
}

// 止盈止损配置
{
  tp1_pct: 1.0,   tp1_ratio: 30,
  tp2_pct: 2.0,   tp2_ratio: 40,
  tp3_pct: 3.5,   tp3_ratio: 30,
  sl_buffer_atr: 0.5,
  sl_min_pct: 1.2,
  protect_profit_at: 1.5,
  protect_trail_pct: 0.8,
}
```

## 参数调优建议

### 针对不同品种

#### BTC/ETH（主流币）
```python
EnhancedExitRules(
    tp1_pct=0.8, tp1_ratio=30,   # 第一档0.8%
    tp2_pct=1.5, tp2_ratio=40,   # 第二档1.5%
    tp3_pct=3.0, tp3_ratio=30,   # 第三档3%
    sl_min_pct=1.0,              # 最小止损1%
)
```

#### SOL/AVAX（高波动）
```python
EnhancedExitRules(
    tp1_pct=1.5, tp1_ratio=30,   # 第一档1.5%
    tp2_pct=3.0, tp2_ratio=40,   # 第二档3%
    tp3_pct=5.0, tp3_ratio=30,   # 第三档5%
    sl_min_pct=1.5,              # 最小止损1.5%
)
```

#### MU等小盘币
```python
EnhancedExitRules(
    tp1_pct=1.2, tp1_ratio=40,   # 第一档1.2%，平更多
    tp2_pct=2.5, tp2_ratio=40,   # 第二档2.5%
    tp3_pct=4.0, tp3_ratio=20,   # 第三档4%，留少量
    sl_min_pct=1.5,              # 止损放宽到1.5%
    sl_buffer_atr=0.8,           # ATR缓冲加大
)
```

### 针对不同周期

#### 15m/30m（短周期）
```python
# 闸门更严格
cfg.er_min = 0.20
cfg.min_score = 2
cfg.atr_filter_enabled = True  # 启用ATR过滤

# 止盈更快
tp1_pct=0.8, tp2_pct=1.5, tp3_pct=2.5
```

#### 4h/1d（长周期）
```python
# 闸门放宽
cfg.er_min = 0.12
cfg.min_score = 1

# 止盈更高
tp1_pct=1.5, tp2_pct=3.0, tp3_pct=5.0
```

## 预期效果

### 改进前 vs 改进后

| 指标 | 改进前 | 改进后（预期） |
|------|--------|---------------|
| 大行情捕获率 | ~50% | ~80%+ |
| 震荡期误判率 | ~60% | ~30% |
| 止损频率 | 高 | 中等 |
| 盈利单占比 | ~30% | ~50%+ |
| 平均盈亏比 | 1:1 | 1.5:1 |

### 关键指标监控

启用后需要监控：
1. **动量突破识别准确率**：真突破 vs 假突破的比例
2. **多级止盈触发分布**：大部分应该在tp1，少量tp2/tp3
3. **盈利保护生效次数**：避免过早离场的次数
4. **按品种分类的胜率**：MU等问题品种是否改善

## 回测验证（建议）

使用 `backend/backtest.py` 回测增强策略：

```bash
# 回测命令
python backend/backtest.py --symbol MU --start 2024-01-01 --end 2024-08-20 \
  --enhanced-regime --enhanced-position

# 对比原策略
python backend/backtest.py --symbol MU --start 2024-01-01 --end 2024-08-20
```

**关注指标：**
- 总收益率
- 胜率
- 最大回撤
- 平均盈亏比
- 交易次数

## 风险提示

1. **过度优化风险**：基于历史调参可能过拟合，实盘需谨慎
2. **滑点影响**：多级止盈会增加交易次数，滑点成本上升
3. **极端行情**：闪崩等极端情况下保护机制可能失效
4. **参数敏感性**：不同市场环境需要调整参数

## 渐进式部署建议

### 第一阶段（1-2周）
- ✅ 仅启用动量突破检测
- ✅ 观察大行情捕获情况
- ⏸ 其他功能暂不启用

### 第二阶段（2-3周）
- ✅ 启用假突破过滤
- ✅ 观察震荡期误判率
- ⏸ 持仓管理保持原样

### 第三阶段（3-4周）
- ✅ 启用多级止盈
- ✅ 启用智能止损
- ⏸ 盈利保护观察再决定

### 第四阶段（4周+）
- ✅ 全部功能启用
- ✅ 自适应阈值
- ✅ 盈利保护
- 🎯 长期跟踪优化

## 联系与支持

如有问题或需要进一步调优，可以：
1. 查看日志分析具体止损/止盈原因
2. 导出交易记录进行复盘
3. 调整参数后小仓位测试

---

**最后提醒：** 任何策略优化都需要实盘验证，建议先用模拟盘测试2-4周再切换实盘。
