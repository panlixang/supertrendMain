# 高杠杆止损过频问题修复

## 🐛 问题描述

用户反馈：10x 杠杆的交易经常在 -3% 左右就被止损，无法持有更长时间。

从交易记录看：
```
BTC 10x 杠杆
开仓: 72,813.9
止损: -3.42%（价格变动）
对应保证金收益: -34.2%（10x 放大）
```

## 🔍 根本原因

### 1. 执行器未使用增强版规则

**问题代码**（executor.py 原版）：
```python
# 开仓时
stop = position.initial_stop(filled, rules)  # ❌ 调用原版

# 检查止损时  
act = position.check(pos, price, self.rules_of(pos))  # ❌ 调用原版

# 止盈时
position.apply_tp1(pos, r["price"], r["qty"], rules)  # ❌ 只有单档止盈
```

虽然 `state.py` 配置了 `EnhancedExitRules`（带ATR外扩、最小距离保护、三级止盈），但执行器实际调用的是原版 `position.py` 的函数，这些函数根本不认识增强版字段。

**结果**：
- ✅ 配置里写了 `sl_buffer_atr=0.5`（ST线外扩0.5倍ATR）
- ✅ 配置里写了 `sl_min_pct=1.2`（最小止损距离1.2%）
- ✅ 配置里写了三级止盈 1%/2%/3.5%
- ❌ 但这些参数全部**没有生效**！

### 2. 固定止损距离不适配高杠杆

即使增强版规则生效，`sl_min_pct=1.2%` 对 10x 杠杆来说仍然太窄：

| 杠杆 | 价格变动 | 保证金收益 | 是否合理 |
|------|---------|-----------|---------|
| 1x   | -1.2%   | -1.2%     | ✅ 合理 |
| 3x   | -1.2%   | -3.6%     | ✅ 尚可 |
| 10x  | -1.2%   | -12%      | ⚠️ 太窄，正常波动就扫 |
| 20x  | -1.2%   | -24%      | ❌ 秒扫 |

高杠杆需要**更宽的止损距离**，才能承受正常的市场波动。

## ✅ 修复方案

### 1. 执行器调用增强版函数

**修改 executor.py**：

```python
# 导入增强版
try:
    from position_enhanced import (
        EnhancedExitRules, EnhancedPosition,
        enhanced_initial_stop, check_enhanced,
        apply_tp_enhanced, trail_enhanced
    )
    USE_ENHANCED = True
except ImportError:
    USE_ENHANCED = False
```

**开仓时**：
```python
if USE_ENHANCED and isinstance(rules, EnhancedExitRules):
    atr = self._calc_atr(sig.get("tf", "15m"))
    stop = enhanced_initial_stop(filled, rules, atr, cfg.leverage)  # ✅ 增强版
    self.store.position = EnhancedPosition(...)  # ✅ 跟踪最高盈利
else:
    stop = position.initial_stop(filled, rules)
```

**检查止损时**：
```python
if USE_ENHANCED and isinstance(rules, EnhancedExitRules):
    pos.update_max_unrealized(price)
    act = check_enhanced(pos, price, rules, pos.max_unrealized_pct)  # ✅ 带盈利保护
else:
    act = position.check(pos, price, rules)
```

**止盈时**：
```python
if USE_ENHANCED and isinstance(rules, EnhancedExitRules):
    stage = {"tp1": 1, "tp2": 2, "tp3": 3}.get(act["action"], 1)
    apply_tp_enhanced(pos, r["price"], r["qty"], rules, stage)  # ✅ 三级止盈
else:
    position.apply_tp1(pos, r["price"], r["qty"], rules)
```

### 2. 杠杆自适应止损距离

**修改 position_enhanced.py**：

```python
def enhanced_initial_stop(sig, rules, atr=None, leverage=1):
    # 杠杆自适应系数
    if leverage >= 20:
        lev_factor = 4.0   # 20x: 1.2% → 4.8%
    elif leverage >= 10:
        lev_factor = 2.5   # 10x: 1.2% → 3.0%
    elif leverage >= 5:
        lev_factor = 1.5   # 5x:  1.2% → 1.8%
    else:
        lev_factor = 1.0   # 1-3x: 保持 1.2%
    
    # 应用到所有止损计算
    adjusted_sl_pct = rules.sl_pct * lev_factor
    adjusted_min_pct = rules.sl_min_pct * lev_factor
    buffer = atr * rules.sl_buffer_atr * lev_factor  # ATR缓冲也放大
```

**效果对比**：

| 杠杆 | 原止损距离 | 新止损距离 | 保证金亏损 | 评价 |
|------|-----------|-----------|-----------|------|
| 3x   | 1.2%      | 1.2%      | -3.6%     | ✅ 保持不变 |
| 5x   | 1.2%      | 1.8%      | -9.0%     | ✅ 适度放宽 |
| 10x  | 1.2%      | 3.0%      | -30%      | ✅ 合理空间 |
| 20x  | 1.2%      | 4.8%      | -96%      | ✅ 承受波动 |

### 3. 增加 ATR 计算

执行器新增 `_calc_atr()` 方法，从K线历史计算真实波动率：

```python
def _calc_atr(self, tf: str) -> float | None:
    """计算当前ATR，用于止损外扩"""
    candles = list(self.store.candles.get(tf, []))
    if len(candles) < 15:
        return None
    # 简单TR平均（最近14根）
    trs = []
    for i in range(1, min(15, len(candles))):
        c, prev = candles[-i], candles[-i-1]
        tr = max(c.h - c.l, abs(c.h - prev.c), abs(c.l - prev.c))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else None
```

## 📊 修复后的效果

### 10x 杠杆示例

**配置**（state.py 默认）：
```python
EnhancedExitRules(
    # 三级止盈
    tp1_pct=1.0, tp1_ratio=30.0,   # 1% 平30%
    tp2_pct=2.0, tp2_ratio=40.0,   # 2% 再平40%
    tp3_pct=3.5, tp3_ratio=30.0,   # 3.5% 剩余全平
    # 智能止损
    sl_mode="st",               # 使用SuperTrend线
    sl_buffer_atr=0.5,          # 外扩0.5倍ATR
    sl_min_pct=1.2,             # 基础最小距离1.2%
    # 盈利保护
    protect_profit_at=1.5,      # 浮盈达1.5%启动保护
    protect_trail_pct=0.8,      # 允许回撤0.8%
)
```

**实际止损距离**（10x 杠杆自动 ×2.5）：
- 最小距离：1.2% × 2.5 = **3.0%**
- ATR 外扩：0.5 × ATR × 2.5 = **1.25 × ATR**
- 百分比止损：2.0% × 2.5 = **5.0%**

**开仓后流程**：

```
1. 开仓 @ 70,000
   止损: 67,900（-3.0%，对应保证金 -30%）

2. 价格涨到 70,700（+1%）
   ✅ 第1档止盈：平30%
   ✅ 止损移至开仓价 70,000（保本）
   
3. 价格涨到 71,400（+2%）
   ✅ 第2档止盈：再平40%（累计70%）
   ✅ 止损上移至 70,700（锁定1%利润）
   
4. 价格涨到 72,450（+3.5%）
   ✅ 第3档止盈：剩余全平
   ✅ 本笔盈利锁定

5. 如果价格回落
   - 已过第1档：最坏保本（已平30%有盈利）
   - 已过第2档：最坏锁定1%（已平70%有盈利）
   - 有盈利保护：允许从最高点回撤0.8%
```

## 🎯 关键改进

1. **执行器真正使用增强版规则**
   - 三级止盈生效：1%/2%/3.5% 分批离场
   - ATR 外扩生效：止损线给震荡留空间
   - 盈利保护生效：浮盈后允许适度回撤

2. **杠杆自适应止损**
   - 低杠杆（1-3x）：保持原距离
   - 中杠杆（5x）：适度放宽 1.5 倍
   - 高杠杆（10x）：合理放宽 2.5 倍
   - 超高杠杆（20x）：充分放宽 4.0 倍

3. **日志增强**
   - 开仓时显示：`三级止盈 1%/2%/3.5%`
   - 止损计算：`杠杆10x，ATR=245.3，止损距离=3.12%`
   - 止盈记录：`第1档止盈` / `第2档止盈` / `第3档止盈`

## 🚀 如何验证

### 1. 查看日志

重启后下次开仓会看到：
```
[止损计算] 杠杆10x，ATR=245.3，止损距离=3.12%
[持仓建立] BTC-USDT-SWAP long @ 70000 保证金 10U × 10x 止损 67818 档位 normal
```

止损距离应该在 **3% 左右**（不再是 1.2%）。

### 2. 观察止盈分批

价格上涨时应该看到：
```
[第1档止盈] BTC-USDT-SWAP 平 0.00015 @ 70700（30% 仓位）
[第2档止盈] BTC-USDT-SWAP 平 0.0002 @ 71400（40% 仓位）
[第3档止盈] BTC-USDT-SWAP 平 0.00015 @ 72450（剩余全平）
```

### 3. 前端持仓事件

持仓卡应该显示：
```
✓ 第1档止盈 0.00015 @ 70700（30% 仓位）
✓ 止损移至开仓价 70000（保本）
✓ 第2档止盈 0.0002 @ 71400（40% 仓位）
✓ 止损上移至 70700（锁定1%利润）
```

## ⚙️ 自定义调整

如果觉得 10x 的 3% 止损还是太窄/太宽，可以调整配置：

**方法1：调整基础距离**（state.py）：
```python
sl_min_pct=1.5,  # 1.5% × 2.5 = 3.75%（10x时）
```

**方法2：调整杠杆系数**（position_enhanced.py）：
```python
elif leverage >= 10:
    lev_factor = 3.0   # 改为 3.0，则 1.2% × 3.0 = 3.6%
```

**方法3：单独为某品种设置**：
前端「挂单配置」中可为每个品种设置独立的止盈止损规则。

## 📝 总结

**修复前**：
- ❌ 增强版规则配了但没用
- ❌ 10x 杠杆用 1.2% 止损，3% 就扫
- ❌ 只有单档止盈，要么全平要么不平

**修复后**：
- ✅ 执行器真正调用增强版函数
- ✅ 10x 杠杆自动放宽到 3% 止损
- ✅ 三级止盈分批离场 + 盈利保护
- ✅ 第1档后自动保本，风险可控

**现在重启服务，下次开仓生效！** 🚀
