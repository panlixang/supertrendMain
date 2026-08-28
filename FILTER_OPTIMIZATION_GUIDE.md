"""
震荡过滤优化 - 快速集成指南

三个方案可以单独使用，也可以组合使用。按推荐顺序：

方案1：打分制（最推荐）
  ✓ 优点：不会漏掉真趋势，假信号也能降级处理
  ✓ 集成难度：中等，需要修改执行器支持半仓
  ✓ 适合：想要"更聪明的过滤"的场景

方案2：动态阈值
  ✓ 优点：解决ER滞后问题，突破启动时放宽
  ✓ 集成难度：低，直接替换ER判断逻辑
  ✓ 适合：想要"自适应阈值"的场景

方案3：确认机制
  ✓ 优点：假信号率最低（砍掉50%+）
  ✓ 集成难度：高，需要状态管理
  ✓ 适合：稳健型，宁可错过也不做错

组合推荐：
  - 保守型：方案2（动态阈值）+ 方案3（确认机制）
  - 平衡型：方案1（打分制）+ 方案2（动态阈值）
  - 激进型：只用方案1（打分制），60分以上就下
"""

# ============================================================
# 集成示例1：在 router.py 里启用打分制
# ============================================================

def integrate_scoring_in_router():
    """
    修改 backend/router.py，在信号评估部分：

    原代码：
        from regime import evaluate
        result = evaluate(sig, candles, cfg)

    改为：
        from regime_scoring import evaluate_enhanced
        result = evaluate_enhanced(sig, candles, cfg, candles_by_tf, p)

    然后修改执行器部分：
        if result["trade"]:
            # 新增：检查是否半仓
            if result.get("trade_half"):
                amount = cfg.amount_usdt * 0.5  # 半仓
            else:
                amount = cfg.amount_usdt  # 全仓

            # 下单逻辑...

    前端显示（在 SignalModal.jsx 里）：
        {scoreDetail && (
          <div className="score-breakdown">
            <div className="score-total">
              置信度：{scoreDetail.total}分 ({scoreDetail.confidence})
            </div>
            <div className="score-bars">
              {Object.entries(scoreDetail.breakdown).map(([key, val]) => (
                <div key={key} className="score-item">
                  <span>{key}</span>
                  <div className="bar" style={{width: `${Math.abs(val)}%`}} />
                  <span>{val}</span>
                </div>
              ))}
            </div>
            <div className="suggestion">{scoreDetail.suggestion}</div>
          </div>
        )}
    """
    pass


# ============================================================
# 集成示例2：在 regime.py 里启用动态阈值
# ============================================================

def integrate_dynamic_threshold():
    """
    修改 backend/regime.py 的 evaluate() 函数：

    原代码（第143行附近）：
        if not regime["tradable"] and not breakout_start:
            reasons.append(f"ER {er} < {cfg.er_min}")

    改为：
        from regime_dynamic import adaptive_er_threshold

        # 使用动态阈值
        adapt = adaptive_er_threshold(candles, cfg.er_min)
        if not adapt["tradable"] and not breakout_start:
            reasons.append(
                f"ER {adapt['er_current']:.2f} < 动态阈值 {adapt['threshold']:.2f} "
                f"({adapt['reason']})"
            )

        # 可选：把动态阈值信息返回给前端展示
        filters["adaptive_threshold"] = adapt

    前端可以显示：
        当前ER: 0.13
        动态阈值: 0.11 ↓ (突破启动阶段，已放宽)
    """
    pass


# ============================================================
# 集成示例3：启用确认机制（需要修改较多）
# ============================================================

def integrate_confirmation():
    """
    1. 在 backend/state.py 里添加全局跟踪器：
        from regime_confirmation import SignalConfirmationTracker
        confirmation_tracker = SignalConfirmationTracker()

    2. 修改 backend/router.py 的信号处理逻辑：

        原代码：
            # 新翻转信号
            if result["trade"]:
                # 直接下单
                place_order(...)

        改为：
            from regime_confirmation import evaluate_with_confirmation

            # 检查确认状态
            confirm_result = evaluate_with_confirmation(
                sig, symbol, candles, st, cfg
            )

            if confirm_result.get("confirmation", {}).get("pending"):
                # 新信号，等待确认
                send_notification(f"待确认信号：{sig['type']} @ {sig['price']}")

            elif confirm_result.get("confirmation", {}).get("confirmed"):
                # 确认通过，下单
                entry_price = confirm_result["confirmation"]["entry_price"]
                place_order(entry_price)
                send_notification(
                    f"信号已确认（等待{confirm_result['confirmation']['bars_waited']}根）"
                )

            elif confirm_result.get("confirmation", {}).get("cancelled"):
                # 确认失败，静默
                pass

    3. 修改 WebSocket 推送（backend/feed.py）：
        每根新K线来时，检查所有待确认信号：

        from regime_confirmation import get_pending_signals

        def on_new_candle(symbol, tf, candle):
            # ... 原有逻辑 ...

            # 检查待确认信号
            pending = get_pending_signals()
            for key, item in pending.items():
                # 推送给前端显示"待确认"状态
                ws.send_json({
                    "type": "pending_signal",
                    "data": item
                })

    4. 前端显示（SignalList.jsx）：
        {signal.confirmation?.pending && (
          <span className="badge pending">
            待确认 {signal.confirmation.bars_waited}/{signal.confirmation.max_bars}
          </span>
        )}

        {signal.confirmation?.confirmed && (
          <span className="badge confirmed">
            ✓ 已确认（等待{signal.confirmation.bars_waited}根）
          </span>
        )}
    """
    pass


# ============================================================
# 测试建议
# ============================================================

def testing_guide():
    """
    集成前先测试效果：

    1. 运行对比测试：
        cd backend
        python test_filter_methods.py

    2. 查看报告：
        cat filter_comparison_report.json

    3. 调整参数：
        - 打分制：调整各项分值权重（在 regime_scoring.py 里）
        - 动态阈值：调整放宽/收紧幅度（在 regime_dynamic.py 里）
        - 确认机制：调整最大等待根数（max_confirm_bars）

    4. 模拟盘验证：
        先在模拟盘跑1-2周，观察：
        - 假信号是否减少
        - 真趋势是否漏掉
        - 整体盈亏是否改善

    5. 逐步上实盘：
        确认无误后，小仓位上实盘观察
    """
    pass


# ============================================================
# 配置建议
# ============================================================

def recommended_config():
    """
    根据你的风格选择配置：

    # 保守型（假信号率最低）
    TradeConfig(
        er_min=0.15,           # 标准阈值
        min_score=2,           # 只做高质量翻转
        quick_enabled=False,   # 不做弱档
        # 启用确认机制
        use_confirmation=True,
        # 启用动态阈值
        use_dynamic_threshold=True,
        # ATR/MTF等辅助过滤器按需开启
        atr_filter_enabled=True,
        mtf_filter_enabled=True
    )

    # 平衡型（推荐）
    TradeConfig(
        er_min=0.15,
        min_score=2,
        quick_enabled=False,
        # 启用打分制
        use_scoring=True,
        scoring_full_threshold=80,   # 80分以上全仓
        scoring_half_threshold=60,   # 60-79分半仓
        scoring_alert_threshold=40,  # 40-59分仅提醒
        # 启用动态阈值
        use_dynamic_threshold=True,
        # 辅助过滤器适度开启
        atr_filter_enabled=False,    # 打分制已包含ATR
        mtf_filter_enabled=True
    )

    # 激进型（不漏机会）
    TradeConfig(
        er_min=0.12,           # 降低阈值
        min_score=1,           # 降低质量要求
        quick_enabled=True,    # 开启弱档
        # 只用打分制，其他过滤器关闭
        use_scoring=True,
        scoring_full_threshold=70,   # 70分就全仓
        scoring_half_threshold=50,
        # 其他过滤器全关
        atr_filter_enabled=False,
        mtf_filter_enabled=False
    )
    """
    pass


# ============================================================
# 性能优化提示
# ============================================================

def performance_tips():
    """
    这些方案的计算开销：

    1. 打分制：+5-10ms（多算几个指标）
    2. 动态阈值：+10-15ms（多算ER斜率、突破强度）
    3. 确认机制：几乎无开销（只是状态管理）

    优化建议：
    - ER、ATR等指标可以缓存，不用每次重算
    - 打分制的各项检测可以并行计算
    - 动态阈值的突破检测可以复用区间震荡的计算结果

    如果性能有压力，优先级：
    确认机制 > 动态阈值 > 打分制
    （确认机制效果最好且开销最小）
    """
    pass


if __name__ == "__main__":
    print(__doc__)
    print("\n运行 test_filter_methods.py 可以对比各方案效果")
    print("然后根据上面的集成示例修改代码即可")
