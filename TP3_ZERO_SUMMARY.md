# TP3=0 反向信号平仓 - 实现总结

## ✅ 已完成

### 核心设计
通过 `tp3_pct` 字段实现两种模式切换：
- **`tp3_pct > 0`**（如 3.5）→ 三档分批止盈模式
- **`tp3_pct = 0`** → 反向信号平仓模式

### 修改的文件

#### 1. [backend/position_enhanced.py](backend/position_enhanced.py#L105)
```python
def check_enhanced(pos, price, rules, max_unrealized=None):
    if pos.qty <= 0:
        return None
    
    # tp3_pct=0 → 反向信号平仓模式
    if rules.tp3_pct <= 0:
        return None  # 跳过所有价格检查
    
    # 正常模式：止损 + 三档止盈
    # ...
```

#### 2. [backend/router.py:814](backend/router.py#L814)
```python
class ExitRulesPatch(BaseModel):
    tp3_pct: Optional[float] = None  # 0=反向信号平仓

def _apply_exit_patch(r, patch):
    # tp3_pct 允许为 0
    if patch.tp3_pct is not None:
        r.tp3_pct = max(0.0, min(100.0, patch.tp3_pct))
```

#### 3. [frontend/src/components/PatternTradePanel.jsx:459](frontend/src/components/PatternTradePanel.jsx#L459)
```jsx
<div style={SZ.row}>
  <span>TP3 幅度</span>
  <input type="number" step={0.1}
         defaultValue={s.tp3_pct ?? cfg.tp3_pct ?? 3.5}
         onBlur={(e) => updateSymbol(s.symbol, { tp3_pct: Number(e.target.value) })} />
  <span>%（设为 0 = 反向信号平仓）</span>
</div>
<div style={{ fontSize: 10, color: C.dim }}>
  TP3=0 时，所有价格止盈止损失效，只在反向信号时平仓
</div>
```

---

## 使用方式

### 前端操作
1. 打开形态识别页右侧面板
2. 找到"止盈止损"配置区的 TP3 输入框
3. **将 TP3 幅度设为 `0`** → 自动切换到反向信号平仓模式
4. 设为 `> 0`（如 3.5）→ 恢复三档止盈模式

### 两种模式对比

| 配置 | TP1 | TP2 | TP3 | 行为 |
|------|-----|-----|-----|------|
| **三档止盈** | 1.0% | 2.0% | 3.5% | +1% 平30% → +2% 平40% → +3.5% 平剩余 |
| **反向平仓** | - | - | 0 | 忽略所有价格，只在反向信号时全仓平掉 |

---

## 测试验证

```bash
# 测试通过：tp3_pct=0 禁用所有价格检查
价格 101.0 (浮盈 1.0%): None  ✓
价格 102.0 (浮盈 2.0%): None  ✓
价格 105.0 (浮盈 5.0%): None  ✓
价格 110.0 (浮盈 10.0%): None ✓

# 测试通过：tp3_pct=3.5 正常触发 TP1
价格 101.5 (浮盈 1.5%): tp1  ✓
```

---

## 优势

1. **零字段新增**：复用现有 `tp3_pct` 字段
2. **语义清晰**：0 = 禁用，直观易懂
3. **向后兼容**：旧配置 `tp3_pct=3.5` 不受影响
4. **简洁实现**：一行判断 `if rules.tp3_pct <= 0: return None`

---

## 适用场景

### 场景 1：震荡行情 → 三档止盈
- 价格反复波动
- 假突破频繁
- **推荐**：`tp3_pct=3.5`

### 场景 2：强趋势行情 → 反向平仓
- 单边上涨/下跌
- 回调少
- **推荐**：`tp3_pct=0`

---

## 相关文档

- [TP3_ZERO_DESIGN.md](TP3_ZERO_DESIGN.md) - 设计文档
- [position_enhanced.py:105](backend/position_enhanced.py#L105) - 核心逻辑
- [PatternTradePanel.jsx:459](frontend/src/components/PatternTradePanel.jsx#L459) - 前端UI

---

**实现日期**：2026-09-26  
**状态**：✅ 已完成并测试通过  
**改动文件**：4 个文件，+21 -19 行
