# 修复完成报告

## 已完成的修复

### ✅ 第一步：删除孤儿文件
- 删除 `backend/signal_filter_stats.py`（已无任何引用）

### ✅ 第二步：修复 `bt_pattern_page.py`
- 移除对 `pattern_trade.signal_features` / `filter_decide` / `SCORE_CUT_DEFAULT` 的依赖
- 改用 `signal_v3.py` 中的 `v3_decide` + `features_from_candles`
- 重构 `build_signals` 函数，用 V3 趋势打分闸门替换旧的 ①~⑦ 漏斗
- 重构 `print_funnel` 函数，显示 V3 路径统计

### ✅ 第三步：修复依赖的回测脚本
批量修复了以下文件：
1. `backtest/_bt_score_year.py` - 年度回测对比
2. `backtest/_bt_score_verify.py` - 2026 验证脚本
3. `backtest/_bt_score_combo.py` - 组合过滤对比
4. `backtest/_dbg_score_breakdown.py` - 直接退出（V3 不适用）

**修改内容**：
- 移除 `from pattern_trade import SCORE_CUT_DEFAULT`
- 将 `s["pass_score"]` 改为 `s.get("v3_execute")`
- 更新输出文案，改为 "V3 口径（趋势打分闸门）"

---

## V3 口径说明

### 旧版（①~⑦ 漏斗）
```python
# 7 条独立规则
pass_flip       # ① 连续翻转过滤
pass_vol        # ② 波动异常过滤
pass_position   # ③ 箱体错误位置过滤
pass_candle     # ④ 极端K过滤
pass_near_high  # ⑤ 近高价过滤
pass_score      # ⑥ 加权打分过滤（score > 0.48 拦）
pass_tqi        # ⑦ TQI趋势质量过滤
```

### 新版（V3 趋势打分闸门）
```python
# 打分制，两条放行路径
v3_score    # 打分：100=成熟趋势，80=早期启动，0=未通过
v3_path     # 路径："成熟趋势" / "早期启动" / "未通过" / "极端震荡熔断"
v3_execute  # 是否放行：score > 0 即可
v3_fused    # 是否触发震荡熔断

# 多头路径1：成熟趋势（score=100）
- 4h MA30 斜率 > 0.16，或
- 斜率 > 0 且 1h MA30 距离 > 0.13

# 多头路径2：早期启动（score=80）
- 动量突破 > 0.5
- 波动收缩后扩张
- ST 方向一致
- 距离 MA30 < 3 ATR（避免末端追涨）

# 空头路径：沿用原 Short Gate
# 震荡熔断：flip50 > 8 且 ER20 < 0.15
```

---

## 使用方式

### 运行回测脚本
```bash
# 测试 bt_pattern_page.py
cd backend
python bt_pattern_page.py BTC-USDT --window 2025

# 年度对比
cd backtest
python _bt_score_year.py 2025

# 2026 验证
python _bt_score_verify.py

# 组合对比
python _bt_score_combo.py
```

### 信号字段映射

| 旧字段 | 新字段 | 说明 |
|--------|--------|------|
| `pass_filter` | `v3_execute` | 是否放行开仓 |
| `pass_score` | `v3_execute` | V3 打分 > 0 |
| `pass_*` | - | V3 不再单独判断 |
| - | `v3_score` | 100/80/0/-1000 |
| - | `v3_path` | 通过路径 |
| - | `v3_fused` | 震荡熔断 |

---

## 验证状态

✅ **导入测试通过**
- `bt_pattern_page.py` 可以正常导入
- `signal_v3.py` 集成正常
- 所有回测脚本可以运行

⚠️ **待验证**
- 需要用实际数据运行完整回测
- 确认 V3 特征计算正确
- 验证输出统计无误

---

生成时间：2026-09-25  
修复版本：V3 口径
