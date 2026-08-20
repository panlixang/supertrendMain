"""
增强功能测试脚本 - 验证改进是否有效

运行方法：
python backend/test_enhancements.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from regime_enhanced import (
    detect_momentum_breakout,
    detect_false_breakout,
    adaptive_thresholds,
    evaluate_enhanced
)
from position_enhanced import (
    EnhancedExitRules,
    enhanced_initial_stop,
    check_enhanced,
    EnhancedPosition
)


def test_momentum_detection():
    """测试动量突破检测"""
    print("\n=== 测试1：动量突破检测 ===")

    # 模拟震荡后突破的K线数据
    candles = []
    base_price = 100.0

    # 前20根：震荡（95-105之间）
    for i in range(20):
        price = base_price + (i % 2) * 5 - 2.5
        candles.append({
            "ts": i * 60000,
            "o": price - 1,
            "h": price + 1,
            "l": price - 1,
            "c": price,
            "vol": 1000 + i * 10
        })

    # 第21根：大阳线突破
    candles.append({
        "ts": 20 * 60000,
        "o": 103,
        "h": 112,
        "l": 102,
        "c": 111,  # 突破区间10%
        "vol": 3000  # 成交量放大3倍
    })

    sig = {"type": "buy", "price": 111, "tf": "15m"}

    momentum = detect_momentum_breakout(candles, sig, lookback=20)

    if momentum:
        print(f"✅ 检测到动量突破:")
        print(f"   突破距离: {momentum['breakout_dist']}%")
        print(f"   成交量放大: {momentum['volume_surge']}")
        print(f"   动量确认: {momentum['momentum_confirmed']}")
        print(f"   评分: {momentum['score']}/3")
    else:
        print("❌ 未检测到动量突破（预期应该检测到）")

    return momentum is not None


def test_false_breakout_detection():
    """测试假突破检测"""
    print("\n=== 测试2：假突破检测 ===")

    # 模拟假突破：突破后立即回落
    candles = []
    base_price = 100.0

    # 前25根：震荡
    for i in range(25):
        price = base_price + (i % 3) * 2
        candles.append({
            "ts": i * 60000,
            "o": price,
            "h": price + 1,
            "l": price - 1,
            "c": price,
            "vol": 1000
        })

    # 接下来几根：ATR萎缩
    for i in range(25, 30):
        price = base_price + 1
        candles.append({
            "ts": i * 60000,
            "o": price,
            "h": price + 0.3,  # 波动变小
            "l": price - 0.3,
            "c": price,
            "vol": 900
        })

    # 最后一根：长上影线（假突破）
    candles.append({
        "ts": 30 * 60000,
        "o": 101,
        "h": 108,  # 上冲
        "l": 100,
        "c": 102,  # 但收回来了
        "vol": 1100
    })

    sig = {"type": "buy", "price": 102, "tf": "15m"}

    is_false = detect_false_breakout(candles, sig)

    if is_false:
        print("✅ 检测到假突破特征（长影线 + ATR萎缩）")
    else:
        print("❌ 未检测到假突破（预期应该检测到）")

    return is_false


def test_adaptive_thresholds():
    """测试自适应阈值"""
    print("\n=== 测试3：自适应阈值调整 ===")

    from regime import TradeConfig

    # 模拟高波动品种的K线（价格剧烈波动）
    candles_volatile = []
    price = 100.0
    for i in range(100):
        change = (i % 2) * 10 - 5  # ±5%波动
        price = price * (1 + change / 100)
        candles_volatile.append({
            "ts": i * 60000,
            "o": price * 0.99,
            "h": price * 1.01,
            "l": price * 0.98,
            "c": price,
            "vol": 1000
        })

    cfg = TradeConfig()
    adjustments = adaptive_thresholds(candles_volatile, cfg)

    print(f"高波动品种分析:")
    print(f"   波动率: {adjustments.get('volatility', 0)}%")
    print(f"   相对倍数: {adjustments.get('vol_ratio', 0)}")
    print(f"   ER阈值调整: {cfg.er_min} → {adjustments.get('er_min', cfg.er_min)}")
    print(f"   止盈倍数: {adjustments.get('tp_multiplier', 1.0)}")

    if adjustments.get('vol_ratio', 0) > 1.2:
        print("✅ 正确识别为高波动品种，阈值已降低")
        return True
    else:
        print("⚠️  波动率识别可能不准确")
        return False


def test_enhanced_stop_loss():
    """测试增强版止损"""
    print("\n=== 测试4：增强版止损计算 ===")

    sig = {
        "type": "buy",
        "price": 100.0,
        "line": 95.0,  # ST线在95
        "atr": 2.0
    }

    rules = EnhancedExitRules(
        sl_mode="st",
        sl_pct=2.0,
        sl_buffer_atr=0.5,  # 外扩0.5倍ATR
        sl_min_pct=1.2
    )

    stop = enhanced_initial_stop(sig, rules, atr=2.0)

    # 预期：95 - 0.5*2 = 94（外扩后）
    expected_range = (93.5, 94.5)

    print(f"开仓价: {sig['price']}")
    print(f"ST止损线: {sig['line']}")
    print(f"ATR: {sig['atr']}")
    print(f"外扩缓冲: {rules.sl_buffer_atr} * ATR = {rules.sl_buffer_atr * sig['atr']}")
    print(f"计算止损: {stop}")
    print(f"预期范围: {expected_range}")

    if expected_range[0] <= stop <= expected_range[1]:
        print(f"✅ 止损计算正确（比ST线低 {sig['line'] - stop:.1f}，预留震荡空间）")
        return True
    else:
        print(f"❌ 止损计算异常")
        return False


def test_multi_stage_tp():
    """测试多级止盈"""
    print("\n=== 测试5：多级止盈机制 ===")

    rules = EnhancedExitRules(
        tp1_pct=1.0, tp1_ratio=30.0,
        tp2_pct=2.0, tp2_ratio=40.0,
        tp3_pct=3.5, tp3_ratio=30.0,
    )

    pos = EnhancedPosition(
        symbol="BTC-USDT-SWAP",
        side="long",
        tf="15m",
        entry=100.0,
        qty=1.0,
        init_qty=1.0,
        stop=98.0,
        leverage=3,
        entry_ts=0
    )

    results = []

    # 价格上涨1%
    price1 = 101.0
    pos.update_max_unrealized(price1)
    act1 = check_enhanced(pos, price1, rules, pos.max_unrealized_pct)
    if act1 and act1["action"] == "tp1":
        print(f"✅ 价格 {price1} (+1.0%): 触发第一档止盈，平{act1['ratio']:.0f}%")
        results.append(True)
    else:
        print(f"❌ 价格 {price1}: 应该触发第一档止盈")
        results.append(False)

    # 模拟执行第一档
    pos.qty = 0.7  # 剩70%
    pos.tp1_done = True

    # 价格上涨2%
    price2 = 102.0
    pos.update_max_unrealized(price2)
    act2 = check_enhanced(pos, price2, rules, pos.max_unrealized_pct)
    if act2 and act2["action"] == "tp2":
        print(f"✅ 价格 {price2} (+2.0%): 触发第二档止盈，平{act2['ratio']:.0f}%")
        results.append(True)
    else:
        print(f"❌ 价格 {price2}: 应该触发第二档止盈")
        results.append(False)

    # 模拟执行第二档
    pos.qty = 0.3  # 剩30%
    pos.tp2_done = True

    # 价格上涨3.5%
    price3 = 103.5
    pos.update_max_unrealized(price3)
    act3 = check_enhanced(pos, price3, rules, pos.max_unrealized_pct)
    if act3 and act3["action"] == "tp3":
        print(f"✅ 价格 {price3} (+3.5%): 触发第三档止盈，全平")
        results.append(True)
    else:
        print(f"❌ 价格 {price3}: 应该触发第三档止盈")
        results.append(False)

    return all(results)


def test_profit_protection():
    """测试盈利保护"""
    print("\n=== 测试6：盈利保护机制 ===")

    rules = EnhancedExitRules(
        tp1_pct=1.0,
        protect_profit_at=1.5,  # 1.5%启动保护
        protect_trail_pct=0.8,  # 允许回撤0.8%
    )

    pos = EnhancedPosition(
        symbol="BTC-USDT-SWAP",
        side="long",
        tf="15m",
        entry=100.0,
        qty=1.0,
        init_qty=1.0,
        stop=98.0,
        leverage=3,
        entry_ts=0,
        breakeven=True  # 已保本
    )

    # 价格涨到102（+2%），记录最高点
    pos.update_max_unrealized(102.0)
    print(f"最高浮盈: {pos.max_unrealized_pct}%")

    # 价格回撤到101.3（回撤0.7%，在允许范围内）
    price_pullback = 101.3
    pnl = (price_pullback - pos.entry) / pos.entry * 100
    print(f"当前浮盈: {pnl:.2f}%（回撤 {pos.max_unrealized_pct - pnl:.2f}%）")

    act = check_enhanced(pos, price_pullback, rules, pos.max_unrealized_pct)

    if act is None:
        print(f"✅ 盈利保护生效：允许回撤0.8%，实际回撤0.7%，不止损")
        return True
    else:
        print(f"❌ 不应该止损（在保护范围内）")
        return False


def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("SuperTrend 增强功能测试套件")
    print("=" * 60)

    tests = [
        ("动量突破检测", test_momentum_detection),
        ("假突破过滤", test_false_breakout_detection),
        ("自适应阈值", test_adaptive_thresholds),
        ("增强止损", test_enhanced_stop_loss),
        ("多级止盈", test_multi_stage_tp),
        ("盈利保护", test_profit_protection),
    ]

    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"❌ 测试 {name} 失败: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))

    # 汇总
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)

    passed = sum(1 for _, r in results if r)
    total = len(results)

    for name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{status} - {name}")

    print(f"\n通过率: {passed}/{total} ({passed/total*100:.1f}%)")

    if passed == total:
        print("\n🎉 所有测试通过！可以开始使用增强功能。")
    else:
        print(f"\n⚠️  有 {total-passed} 个测试失败，请检查代码。")

    return passed == total


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
