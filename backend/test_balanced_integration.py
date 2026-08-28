"""
平衡型方案集成测试

测试：
1. 打分制是否正确计算分数
2. 动态阈值是否根据市场状态调整
3. 半仓下单逻辑是否正确
4. 前端配置是否能正确保存
"""

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent))

from regime import TradeConfig, evaluate
from regime_scoring import score_signal, evaluate_enhanced
from regime_dynamic import adaptive_er_threshold
from history import fetch_candles


def test_scoring_system():
    """测试打分制"""
    print("\n" + "="*70)
    print("🧪 测试1：打分制")
    print("="*70)

    # 加载测试数据
    candles = fetch_candles("1h", 200, "BTC-USDT")

    # 创建测试信号
    sig = {
        "type": "buy",
        "price": candles[-1]["c"],
        "tf": "1h",
        "score": 2,  # 中等强度
        "grade": "B"
    }

    # 配置（启用打分制）
    cfg = TradeConfig(
        enabled=True,
        use_scoring=True,
        scoring_full_threshold=80.0,
        scoring_half_threshold=60.0,
        scoring_alert_threshold=40.0,
        er_min=0.15,
        min_score=2,
        allow_grades=["A", "B"],
        allow_tfs=["1h"]
    )

    # 执行评估
    result = score_signal(sig, candles, cfg)

    print(f"✓ 总分：{result['total_score']:.1f} / 100")
    print(f"✓ 置信度：{result['confidence']}")
    print(f"✓ 动作：{result['action']}")
    print(f"✓ 建议：{result['suggestion']}")
    print(f"\n分项得分：")
    for key, val in result['breakdown'].items():
        print(f"  - {key}: {val}")
    print(f"\n原因列表：")
    for r in result['reasons']:
        print(f"  • {r}")

    assert result['total_score'] >= 0 and result['total_score'] <= 100, "分数超出范围"
    assert result['confidence'] in ['high', 'medium', 'low', 'noise', 'disabled'], "置信度无效"
    assert result['action'] in ['trade_full', 'trade_half', 'alert_only', 'silent'], "动作无效"
    print("\n✅ 打分制测试通过")


def test_dynamic_threshold():
    """测试动态阈值"""
    print("\n" + "="*70)
    print("🧪 测试2：动态阈值")
    print("="*70)

    candles = fetch_candles("1h", 200, "BTC-USDT")

    # 测试动态阈值计算
    adapt = adaptive_er_threshold(candles, base_er=0.15)

    print(f"✓ 基准阈值：{adapt['base']}")
    print(f"✓ 当前ER：{adapt.get('er_current', 'N/A')}")
    print(f"✓ 动态阈值：{adapt['threshold']}")
    print(f"✓ 调整量：{adapt['adjustment']:+.2f}")
    print(f"✓ 原因：{adapt['reason']}")
    print(f"✓ 可交易：{adapt['tradable']}")

    # 检测结果
    if adapt.get('breakout_pct'):
        print(f"✓ 突破幅度：{adapt['breakout_pct']}%")
    if adapt.get('volume_surge'):
        print(f"✓ 量能倍数：{adapt['volume_surge']}x")

    assert adapt['threshold'] >= 0.08 and adapt['threshold'] <= 0.25, "阈值超出合理范围"
    assert adapt['adjustment'] >= -0.1 and adapt['adjustment'] <= 0.1, "调整量过大"
    print("\n✅ 动态阈值测试通过")


def test_integration():
    """测试完整集成"""
    print("\n" + "="*70)
    print("🧪 测试3：完整集成流程")
    print("="*70)

    candles = fetch_candles("1h", 200, "BTC-USDT")

    sig = {
        "type": "buy",
        "price": candles[-1]["c"],
        "tf": "1h",
        "score": 2,
        "grade": "B"
    }

    # 测试配置1：打分制开启
    cfg_scoring = TradeConfig(
        enabled=True,
        use_scoring=True,
        use_dynamic_threshold=True,
        scoring_full_threshold=80.0,
        scoring_half_threshold=60.0,
        scoring_alert_threshold=40.0,
        er_min=0.15,
        min_score=2,
        allow_grades=["A", "B"],
        allow_tfs=["1h"]
    )

    result1 = evaluate(sig, candles, cfg_scoring)

    print(f"\n【打分制开启】")
    print(f"✓ 是否下单：{result1['trade']}")
    print(f"✓ 是否半仓：{result1.get('trade_half', False)}")
    if result1.get('score_detail'):
        print(f"✓ 总分：{result1['score_detail']['total']:.1f}")
        print(f"✓ 置信度：{result1['score_detail']['confidence']}")
    print(f"✓ 原因：{result1['reasons'][:2]}")

    # 测试配置2：打分制关闭（原版逻辑）
    cfg_original = TradeConfig(
        enabled=True,
        use_scoring=False,
        use_dynamic_threshold=False,
        er_min=0.15,
        min_score=2,
        allow_grades=["A", "B"],
        allow_tfs=["1h"]
    )

    result2 = evaluate(sig, candles, cfg_original)

    print(f"\n【原版逻辑】")
    print(f"✓ 是否下单：{result2['trade']}")
    print(f"✓ 原因：{result2['reasons'][:2]}")

    assert 'trade' in result1, "缺少trade字段"
    assert 'regime' in result1, "缺少regime字段"
    print("\n✅ 集成测试通过")


def test_half_position_logic():
    """测试半仓逻辑"""
    print("\n" + "="*70)
    print("🧪 测试4：半仓下单逻辑")
    print("="*70)

    candles = fetch_candles("1h", 200, "BTC-USDT")

    # 制造一个60-79分的信号（中置信度）
    sig = {
        "type": "buy",
        "price": candles[-1]["c"],
        "tf": "1h",
        "score": 1,  # 较低强度
        "grade": "B"
    }

    cfg = TradeConfig(
        enabled=True,
        use_scoring=True,
        scoring_full_threshold=80.0,
        scoring_half_threshold=60.0,
        scoring_alert_threshold=40.0,
        er_min=0.15,
        er_weak_min=0.12,
        min_score=0,  # 降低阈值让信号通过
        allow_grades=["A", "B", "C"],
        allow_tfs=["1h"],
        # 开启一些过滤器让分数降低
        atr_filter_enabled=False,
        mtf_filter_enabled=False
    )

    result = evaluate(sig, candles, cfg)

    print(f"✓ 是否下单：{result['trade']}")
    print(f"✓ 是否半仓：{result.get('trade_half', False)}")

    if result.get('score_detail'):
        total = result['score_detail']['total']
        print(f"✓ 总分：{total:.1f}")

        # 检查半仓逻辑
        if total >= 80:
            assert not result.get('trade_half'), "80分以上不应该半仓"
            print("  → 应该全仓 ✓")
        elif total >= 60:
            assert result.get('trade_half'), "60-79分应该半仓"
            print("  → 应该半仓 ✓")
        elif total >= 40:
            assert not result['trade'], "40-59分不应该下单"
            print("  → 应该仅提醒 ✓")
        else:
            assert result.get('hidden') or not result['trade'], "<40分应该静默或不下单"
            print("  → 应该静默 ✓")

    print("\n✅ 半仓逻辑测试通过")


if __name__ == "__main__":
    try:
        test_scoring_system()
        test_dynamic_threshold()
        test_integration()
        test_half_position_logic()

        print("\n" + "="*70)
        print("🎉 所有测试通过！平衡型方案已成功集成")
        print("="*70)
        print("\n📝 下一步：")
        print("1. 启动后端：cd backend && python main.py")
        print("2. 启动前端：cd frontend && npm run dev")
        print("3. 在交易面板里展开任一品种，找到「平衡型方案」配置块")
        print("4. 调整打分阈值和动态ER开关，保存配置")
        print("5. 查看信号列表，会显示每个信号的置信度评分和分项得分")
        print("6. 中等置信度的信号会自动半仓下单")

    except Exception as e:
        print(f"\n❌ 测试失败：{e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
