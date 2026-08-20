"""
增强功能快速启用指南

这个文件展示如何在现有代码中启用增强功能
"""

# ============================================================================
# 方案1：最小改动 - 只在信号评估环节启用增强
# ============================================================================

# 在 backend/feed.py 或处理信号的地方，找到类似这样的代码：
"""
from regime import evaluate
...
gate = evaluate(sig, candles, cfg, candles_by_tf, p)
"""

# 改为：
"""
from integration import enhanced_signal_handler
...
gate = enhanced_signal_handler(
    sig, candles, cfg, candles_by_tf, p,
    use_momentum=True,        # 启用动量突破检测
    use_false_filter=True,    # 启用假突破过滤
    use_adaptive=True,        # 启用自适应阈值
)
"""

# 这样就能立即解决"大行情没下单，震荡反而下单"的问题


# ============================================================================
# 方案2：完整改动 - 同时启用增强持仓管理
# ============================================================================

# 在 backend/main.py 创建执行器的地方：
"""
# 原代码
from executor import Executor
state.executors[symbol] = Executor(state, stores[symbol])

# 改为
from integration import create_enhanced_executor
state.executors[symbol] = create_enhanced_executor(state, stores[symbol])
"""

# 在 backend/state.py 的 AppState.__init__ 中：
"""
# 原代码
from position import ExitRules
self.exit_rules = ExitRules()

# 改为
from position_enhanced import EnhancedExitRules
self.exit_rules = EnhancedExitRules(
    # 多级止盈
    tp1_pct=1.0, tp1_ratio=30.0,  # 1% 平30%
    tp2_pct=2.0, tp2_ratio=40.0,  # 2% 再平40%
    tp3_pct=3.5, tp3_ratio=30.0,  # 3.5% 全平

    # 智能止损
    sl_buffer_atr=0.5,   # ST止损线外扩0.5倍ATR
    sl_min_pct=1.2,      # 最小止损距离1.2%

    # 盈利保护
    protect_profit_at=1.5,    # 1.5%启动保护
    protect_trail_pct=0.8,    # 允许回撤0.8%
)
"""

# 这样就能解决"MU一直止损/保本没盈利"的问题


# ============================================================================
# 方案3：使用预设配置 - 针对不同品种快速配置
# ============================================================================

# 在 backend/main.py 或启动后的初始化脚本中：
"""
from integration import apply_preset

# 为BTC应用主流币配置
apply_preset(state, "BTC-USDT", "btc")

# 为SOL应用山寨币配置（高波动）
apply_preset(state, "SOL-USDT", "altcoin")

# 为MU应用山寨币配置
apply_preset(state, "MU-USDT", "altcoin")

# 或使用保守配置
apply_preset(state, "ETH-USDT", "conservative")
"""


# ============================================================================
# 监控和调试
# ============================================================================

# 1. 查看动量突破检测日志
"""
[动量突破] 15m buy - 动量突破（区间3.2%，量能放大）
"""

# 2. 查看假突破过滤日志
"""
[假突破过滤] 15m sell - 已拦截
"""

# 3. 查看自适应调整日志
"""
[自适应调整] 波动率=3.5%, 倍数=1.4
"""

# 4. 查看多级止盈日志
"""
[第1档止盈] BTC-USDT-SWAP 平 0.3 @ 45230，止损→45000
[第2档止盈] BTC-USDT-SWAP 平 0.4 @ 45500，止损→45045
[第3档止盈] BTC-USDT-SWAP 平 0.3 @ 46000，止损→45000
"""


# ============================================================================
# 渐进式启用建议
# ============================================================================

# 第1周：只启用信号增强
"""
gate = enhanced_signal_handler(
    sig, candles, cfg, candles_by_tf, p,
    use_momentum=True,
    use_false_filter=False,   # 先不开
    use_adaptive=False,       # 先不开
)
"""

# 第2周：加入假突破过滤
"""
gate = enhanced_signal_handler(
    sig, candles, cfg, candles_by_tf, p,
    use_momentum=True,
    use_false_filter=True,    # 开启
    use_adaptive=False,
)
"""

# 第3周：加入自适应阈值
"""
gate = enhanced_signal_handler(
    sig, candles, cfg, candles_by_tf, p,
    use_momentum=True,
    use_false_filter=True,
    use_adaptive=True,        # 全部开启
)
"""

# 第4周：启用增强持仓管理
"""
state.executors[symbol] = create_enhanced_executor(state, stores[symbol])
"""


# ============================================================================
# 参数微调示例
# ============================================================================

# 针对MU频繁止损的问题，可以单独调整：
"""
# 在 settings.json 或前端配置中
{
  "symbols": [
    {
      "symbol": "MU-USDT",
      "exit_rules": {
        "tp1_pct": 1.2,       # 降低第一档（更容易触发）
        "tp1_ratio": 40,      # 第一档多平一些
        "tp2_pct": 2.5,
        "tp2_ratio": 40,
        "tp3_pct": 4.0,
        "tp3_ratio": 20,
        "sl_min_pct": 1.5,    # 止损放宽（避免扫损）
        "sl_buffer_atr": 0.8  # ATR缓冲加大
      },
      "er_min": 0.12,         # ER阈值降低（更容易通过）
      "min_score": 1,         # 信号强度要求降低
      "atr_filter_enabled": true,  # 启用ATR过滤
      "atr_vol_min": 0.6      # ATR阈值降低
    }
  ]
}
"""


# ============================================================================
# 回测验证示例
# ============================================================================

"""
# 假设你有历史数据，可以这样回测：

import asyncio
from regime_enhanced import evaluate_enhanced
from position_enhanced import EnhancedPosition, check_enhanced

# 模拟交易
candles = load_history("MU-USDT", "15m", days=30)
signals = detect_signals(candles)

wins = 0
losses = 0
total_pnl = 0

for sig in signals:
    # 评估信号
    gate = evaluate_enhanced(sig, candles, cfg)

    if not gate["trade"]:
        continue

    # 模拟开仓
    pos = EnhancedPosition(
        symbol="MU-USDT",
        side="long" if sig["type"] == "buy" else "short",
        tf="15m",
        entry=sig["price"],
        qty=1.0,
        init_qty=1.0,
        stop=enhanced_initial_stop(sig, rules),
        leverage=3,
        entry_ts=sig["ts"]
    )

    # 模拟价格变动
    for i in range(len(candles)):
        price = candles[i]["c"]
        pos.update_max_unrealized(price)

        act = check_enhanced(pos, price, rules, pos.max_unrealized_pct)
        if act:
            # 平仓
            pnl = pos.float_pnl(price)
            total_pnl += pnl

            if pnl > 0:
                wins += 1
            else:
                losses += 1

            print(f"平仓: {act['reason']}, PNL={pnl:.2f}")
            break

print(f"胜率: {wins/(wins+losses)*100:.1f}%")
print(f"总盈亏: {total_pnl:.2f}")
print(f"平均盈亏: {total_pnl/(wins+losses):.2f}")
"""


# ============================================================================
# 常见问题排查
# ============================================================================

"""
Q1: 启用后没有日志输出？
A: 检查 logging 级别，确保至少是 INFO：
   logging.basicConfig(level=logging.INFO)

Q2: 还是频繁止损？
A: 1) 检查 sl_min_pct 是否设置（至少1.2%）
   2) 检查 sl_buffer_atr 是否生效（需要信号带atr字段）
   3) 考虑启用盈利保护

Q3: 大行情还是没捕获？
A: 1) 确认 use_momentum=True
   2) 检查日志是否有 [动量突破] 记录
   3) 检查成交量数据是否正常

Q4: 启动报错 ImportError？
A: 确保新文件都在 backend/ 目录：
   - regime_enhanced.py
   - position_enhanced.py
   - integration.py

Q5: 想回退到原版？
A: 只需要注释掉 integration 导入，恢复原来的 import 即可
   所有增强功能都是可选的，不影响原有代码
"""
