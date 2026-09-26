# TP3=0 反向信号平仓模式

## 设计方案

利用现有 `tp3_pct` 字段实现两种模式，无需新增字段：

| tp3_pct 值 | 模式 | 行为 |
|-----------|------|------|
| `> 0` (如 3.5) | 三档分批止盈 | TP1 +1% → TP2 +2% → TP3 +3.5% |
| `= 0` | 反向信号平仓 | 禁用所有价格止盈止损，只等反向信号 |

---

## 实现逻辑

### 后端 `position_enhanced.py`

```python
def check_enhanced(pos, price, rules, max_unrealized=None):
    """增强版检查：tp3_pct=0 为反向信号平仓模式"""
    if pos.qty <= 0:
        return None
    
    # tp3_pct=0 → 反向信号平仓模式：跳过所有价格检查
    if rules.tp3_pct <= 0:
        return None
    
    # 正常模式：止损 + 三档止盈
    # ...
```

**核心判断**：
- `tp3_pct <= 0` 时，直接返回 `None`
- 跳过所有止损、TP1、TP2、TP3 检查
- 只在 `executor.py` 的反向信号逻辑中平仓

### 前端 `PatternTradePanel.jsx`

```jsx
<div style={SZ.row}>
  <span>TP3 幅度</span>
  <input 
    type="number" 
    step={0.1}
    defaultValue={s.tp3_pct ?? cfg.tp3_pct ?? 3.5}
    onBlur={(e) => {
      const v = e.target.value.trim();
      if (v !== "") updateSymbol(s.symbol, { tp3_pct: Number(v) });
    }}
  />
  <span>%（设为 0 = 反向信号平仓）</span>
</div>
<div style={{ fontSize: 10, color: C.dim }}>
  TP3=0 时，所有价格止盈止损失效，只在反向信号时平仓
</div>
```

**UI 提示**：
- 输入框旁边标注："设为 0 = 反向信号平仓"
- 下方灰色提示文字说明行为

---

## 使用方式

### 场景 1：三档分批止盈（默认）

**配置**：
- `tp1_pct=1.0, tp2_pct=2.0, tp3_pct=3.5`

**行为**：
- +1% 平 30% → +2% 平 40% → +3.5% 平剩余 30%
- 保本 + ST 线跟踪

### 场景 2：反向信号平仓

**配置**：
- 将 `tp3_pct` 设为 `0`

**行为**：
- 忽略 TP1/TP2/TP3 价格检查
- 忽略所有止损（包括保本、跟踪）
- 只在下一个反向信号时全仓平掉

---

## 优势

1. **无需新增字段**：复用现有 `tp3_pct`
2. **简洁直观**：0 = 禁用，> 0 = 启用
3. **向后兼容**：旧配置 `tp3_pct=3.5` 保持不变
4. **易于理解**："设为 0" 明确表达禁用意图

---

## 测试验证

```python
# 测试1：tp3_pct=0 禁用所有检查
rules = EnhancedExitRules(tp3_pct=0)
pos = EnhancedPosition(..., entry=100.0, qty=1.0, ...)
result = check_enhanced(pos, 105.0, rules)  # 浮盈5%
assert result is None  # ✓ 跳过所有检查

# 测试2：tp3_pct=3.5 正常三档止盈
rules = EnhancedExitRules(tp3_pct=3.5)
pos = EnhancedPosition(..., entry=100.0, qty=1.0, ...)
result = check_enhanced(pos, 101.5, rules)  # 浮盈1.5%
assert result['action'] == 'tp1'  # ✓ 触发 TP1
```

---

## 相关文件

- [position_enhanced.py:105](backend/position_enhanced.py#L105) - `check_enhanced` 函数
- [PatternTradePanel.jsx:459](frontend/src/components/PatternTradePanel.jsx#L459) - TP3 输入框

---

生成时间：2026-09-26  
版本：v2.0 - 简化设计
