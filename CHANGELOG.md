# 更新日志

## 2026-09-26 - TP3=0 反向信号平仓模式

### 新增功能
- **两种止盈模式切换**：通过 `tp3_pct` 字段控制
  - `tp3_pct > 0`（如 3.5）→ 三档分批止盈（+1%/+2%/+3.5%）
  - `tp3_pct = 0` → 反向信号平仓（禁用所有价格止盈止损）

### 修改的文件
- `backend/position_enhanced.py` - 核心逻辑：tp3_pct=0 时跳过所有价格检查
- `backend/router.py` - API：tp3_pct 允许为 0
- `frontend/src/components/PatternTradePanel.jsx` - UI：TP3 输入框提示文案

### 使用方式
**前端操作**：
1. 打开形态识别页右侧配置面板
2. 找到"TP3 幅度"输入框
3. 设为 `0` → 反向信号平仓模式
4. 设为 `3.5` → 三档止盈模式

**适用场景**：
- 震荡行情 → `tp3_pct=3.5`（快速止盈）
- 强趋势行情 → `tp3_pct=0`（让利润奔跑）

### 技术细节
```python
# position_enhanced.py:105
def check_enhanced(pos, price, rules, max_unrealized=None):
    if pos.qty <= 0:
        return None
    
    # tp3_pct=0 → 反向信号平仓模式
    if rules.tp3_pct <= 0:
        return None  # 跳过所有价格检查
    
    # 正常模式：止损 + 三档止盈
    # ...
```

### 测试结果
```
[OK] Default config: TP1=1.0%, TP2=2.0%, TP3=3.5%
[OK] Reverse mode: TP3=0 (disable all price checks)
[OK] Normal mode @ +1.5%: tp1 (expected: tp1)
[OK] Reverse mode @ +10%: None (expected: None)
```

### 相关文档
- [TP3_ZERO_DESIGN.md](TP3_ZERO_DESIGN.md) - 设计文档
- [TP3_ZERO_SUMMARY.md](TP3_ZERO_SUMMARY.md) - 实现总结

---

## 历史版本

### 2026-09-XX - 修复启动问题
- 重命名 `backtest.py` → `backtest_engine.py`
- 解决 Python 包/模块命名冲突

### 2026-XX-XX - V3 信号引擎
- 新增 V3 趋势打分闸门
- 三档分批止盈系统
- 增强版持仓管理
