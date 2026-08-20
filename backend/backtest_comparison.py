"""
增强版回测对比脚本 - 对比原版与增强版的收益表现

运行方法：
python backend/backtest_comparison.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from backtest import run_backtest
from position import ExitRules
from regime import TradeConfig
from history import fetch_candles
from datetime import datetime, timedelta


def get_real_params():
    """获取实盘参数配置"""
    return {
        "btc": {
            "params": {
                "periods": 15,
                "multiplier": 9.1,
                "src": "hl2",
                "change_atr": True,
                "fast_len": 20,
                "slow_len": 50,
                "ma_type": "EMA",
            },
            "leverage": 3,
            "margin_usdt": 10.0,
            "er_min": 0.15,
            "er_weak_min": 0.12,
            "er_trend": 0.30,
            "exit_rules": ExitRules(
                enabled=True,
                tp1_pct=1.5,
                tp1_ratio=70.0,
                move_sl_to_entry=True,
                sl_mode="st",
                sl_pct=2.0,
                trail_with_st=True,
            ),
        },
        "mu": {
            "params": {
                "periods": 15,
                "multiplier": 9.1,
                "src": "hl2",
                "change_atr": True,
                "fast_len": 20,
                "slow_len": 50,
                "ma_type": "EMA",
            },
            "leverage": 3,
            "margin_usdt": 10.0,
            "er_min": 0.15,
            "er_weak_min": 0.12,
            "er_trend": 0.30,
            "exit_rules": ExitRules(
                enabled=True,
                tp1_pct=1.5,
                tp1_ratio=70.0,
                move_sl_to_entry=True,
                sl_mode="st",
                sl_pct=2.0,
                trail_with_st=True,
            ),
        }
    }


def get_enhanced_params():
    """获取增强版参数配置"""
    try:
        from position_enhanced import EnhancedExitRules

        return {
            "btc": {
                "exit_rules": EnhancedExitRules(
                    enabled=True,
                    # 多级止盈
                    tp1_pct=0.8, tp1_ratio=30.0,
                    tp2_pct=1.5, tp2_ratio=40.0,
                    tp3_pct=3.0, tp3_ratio=30.0,
                    # 智能止损
                    sl_mode="st",
                    sl_pct=2.0,
                    sl_buffer_atr=0.5,
                    sl_min_pct=1.0,
                    move_sl_to_entry=True,
                    trail_with_st=True,
                    # 盈利保护
                    protect_profit_at=1.5,
                    protect_trail_pct=0.8,
                ),
                "er_min": 0.15,
                "atr_filter_enabled": False,
                "range_filter_enabled": False,
                "adx_filter_enabled": False,
            },
            "mu": {
                "exit_rules": EnhancedExitRules(
                    enabled=True,
                    # 多级止盈（MU调整）
                    tp1_pct=1.2, tp1_ratio=40.0,  # 第一档降低，多平
                    tp2_pct=2.5, tp2_ratio=40.0,
                    tp3_pct=4.0, tp3_ratio=20.0,
                    # 智能止损（放宽）
                    sl_mode="st",
                    sl_pct=2.0,
                    sl_buffer_atr=0.8,  # ATR缓冲加大
                    sl_min_pct=1.5,     # 最小止损放宽
                    move_sl_to_entry=True,
                    trail_with_st=True,
                    # 盈利保护
                    protect_profit_at=1.5,
                    protect_trail_pct=0.8,
                ),
                "er_min": 0.12,  # ER阈值降低
                "atr_filter_enabled": True,
                "atr_vol_min": 0.6,  # ATR阈值降低
                "range_filter_enabled": False,
                "adx_filter_enabled": False,
            }
        }
    except ImportError:
        print("警告：增强版模块未找到，使用原版规则")
        real = get_real_params()
        return {
            "btc": {"exit_rules": real["btc"]["exit_rules"]},
            "mu": {"exit_rules": real["mu"]["exit_rules"]},
        }


def run_comparison(symbol: str, days: int = 60):
    """运行对比测试"""
    print(f"\n{'='*70}")
    print(f"回测品种: {symbol}")
    print(f"回测天数: {days}")
    print(f"{'='*70}\n")

    # 获取历史数据
    print(f"正在获取 {symbol} 15m K线数据...")
    try:
        candles = fetch_candles("15m", limit=days*24*4, symbol=symbol)
        if not candles or len(candles) < 100:
            print(f"❌ 数据不足，无法回测")
            return None
        print(f"✅ 获取 {len(candles)} 根K线\n")
    except Exception as e:
        print(f"❌ 获取数据失败: {e}")
        return None

    symbol_key = "btc" if "BTC" in symbol.upper() else "mu"
    real_cfg = get_real_params()[symbol_key]
    enhanced_cfg = get_enhanced_params()[symbol_key]

    # 基础参数
    base_params = {
        "init_cash": 10000.0,
        "fee_rate": 0.0005,
        "allow_short": True,
        "bias_filter": False,
        "sizing": "fixed",
        "leverage": real_cfg["leverage"],
        "margin_usdt": real_cfg["margin_usdt"],
    }

    # 1. 原版回测
    print("=" * 70)
    print("1️⃣  原版策略回测")
    print("=" * 70)

    result_original = run_backtest(
        candles,
        real_cfg["params"],
        **base_params,
        er_min=real_cfg["er_min"],
        er_weak_min=real_cfg.get("er_weak_min"),
        exit_rules=real_cfg["exit_rules"],
        exit_rules_quick=None,
    )

    if "error" in result_original:
        print(f"❌ {result_original['error']}")
        return None

    print_results(result_original, "原版")

    # 2. 增强版回测
    print("\n" + "=" * 70)
    print("2️⃣  增强版策略回测")
    print("=" * 70)

    result_enhanced = run_backtest(
        candles,
        real_cfg["params"],
        **base_params,
        er_min=enhanced_cfg.get("er_min", real_cfg["er_min"]),
        er_weak_min=real_cfg.get("er_weak_min"),
        exit_rules=enhanced_cfg["exit_rules"],
        exit_rules_quick=None,
        atr_filter_enabled=enhanced_cfg.get("atr_filter_enabled", False),
        atr_vol_min=enhanced_cfg.get("atr_vol_min", 0.7),
        range_filter_enabled=enhanced_cfg.get("range_filter_enabled", False),
        adx_filter_enabled=enhanced_cfg.get("adx_filter_enabled", False),
    )

    if "error" in result_enhanced:
        print(f"❌ {result_enhanced['error']}")
        return None

    print_results(result_enhanced, "增强版")

    # 3. 对比分析
    print("\n" + "=" * 70)
    print("3️⃣  对比分析")
    print("=" * 70)

    compare_results(result_original, result_enhanced)

    return {
        "symbol": symbol,
        "original": result_original,
        "enhanced": result_enhanced,
    }


def print_results(result: dict, label: str):
    """打印回测结果"""
    print(f"\n【{label}】")
    print(f"  初始资金: ${result['init_cash']:,.2f}")
    print(f"  最终权益: ${result['final']:,.2f}")
    print(f"  收益率:   {result['return_pct']:+.2f}%")
    print(f"  持有基准: {result['hold_pct']:+.2f}%")
    print(f"  超额收益: {result['alpha_pct']:+.2f}%")
    print(f"  最大回撤: {result['max_dd_pct']:.2f}%")
    print(f"\n  交易统计:")
    print(f"    总交易数: {result['trades']}")
    print(f"    盈利次数: {result['wins']}")
    print(f"    胜率:     {result['win_rate']}%")
    print(f"    平均盈利: {result['avg_win']:+.2f}%")
    print(f"    平均亏损: {result['avg_loss']:.2f}%")
    print(f"    盈亏比:   {result['profit_factor'] or 'N/A'}")
    print(f"    平均持仓: {result['avg_bars']:.1f} 根K线")
    print(f"\n  风控统计:")
    print(f"    ER拦截:   {result['er_blocked']} 次")
    print(f"    止盈次数: {result['tp1_count']}")
    print(f"    止损次数: {result['stop_count']}")
    print(f"    反向平仓: {result['reverse_count']}")
    print(f"    爆仓次数: {result['liq_count']}")


def compare_results(original: dict, enhanced: dict):
    """对比两个结果"""
    metrics = [
        ("收益率", "return_pct", "%", True),
        ("最大回撤", "max_dd_pct", "%", False),
        ("胜率", "win_rate", "%", True),
        ("盈亏比", "profit_factor", "", True),
        ("交易次数", "trades", "", None),
        ("止损次数", "stop_count", "", False),
    ]

    print("\n指标对比:")
    print(f"{'指标':<12} {'原版':>12} {'增强版':>12} {'变化':>12} {'评价':>8}")
    print("-" * 70)

    improvements = []

    for name, key, unit, better_higher in metrics:
        orig_val = original.get(key)
        enh_val = enhanced.get(key)

        if orig_val is None or enh_val is None:
            continue

        if isinstance(orig_val, (int, float)) and isinstance(enh_val, (int, float)):
            diff = enh_val - orig_val

            if orig_val != 0:
                pct_change = (diff / abs(orig_val)) * 100
            else:
                pct_change = 0

            # 判断是改善还是恶化
            if better_higher is not None:
                if better_higher:
                    emoji = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
                    is_better = diff > 0
                else:
                    emoji = "📉" if diff < 0 else "📈" if diff > 0 else "➡️"
                    is_better = diff < 0
            else:
                emoji = "➡️"
                is_better = None

            if is_better:
                improvements.append(name)

            diff_str = f"{diff:+.2f}{unit}"
            print(f"{name:<12} {orig_val:>11.2f}{unit} {enh_val:>11.2f}{unit} "
                  f"{diff_str:>12} {emoji:>8}")

    print("\n改善指标:")
    if improvements:
        for imp in improvements:
            print(f"  ✅ {imp}")
    else:
        print("  ⚠️  无明显改善")

    # 关键结论
    print("\n关键结论:")
    ret_diff = enhanced["return_pct"] - original["return_pct"]
    wr_diff = enhanced["win_rate"] - original["win_rate"]
    stop_diff = enhanced["stop_count"] - original["stop_count"]

    if ret_diff > 5:
        print(f"  🎉 收益率提升 {ret_diff:+.2f}%，增强版明显更优")
    elif ret_diff > 0:
        print(f"  ✅ 收益率提升 {ret_diff:+.2f}%，增强版略优")
    elif ret_diff > -5:
        print(f"  ⚠️  收益率下降 {ret_diff:.2f}%，效果相当")
    else:
        print(f"  ❌ 收益率下降 {ret_diff:.2f}%，需要调整参数")

    if wr_diff > 5:
        print(f"  🎯 胜率提升 {wr_diff:+.1f}%，信号质量明显改善")
    elif wr_diff > 0:
        print(f"  ✅ 胜率提升 {wr_diff:+.1f}%，信号质量有所改善")

    if stop_diff < -5:
        print(f"  🛡️  止损次数减少 {-stop_diff} 次，止损优化有效")
    elif stop_diff < 0:
        print(f"  ✅ 止损次数减少 {-stop_diff} 次")
    elif stop_diff > 5:
        print(f"  ⚠️  止损次数增加 {stop_diff} 次，需要调整止损参数")


def main():
    """主函数"""
    print("\n" + "=" * 70)
    print("SuperTrend 增强版 vs 原版 回测对比")
    print("=" * 70)

    symbols = [
        ("BTC-USDT", 60),  # BTC 60天
        ("MU-USDT", 60),   # MU 60天
    ]

    results = []
    for symbol, days in symbols:
        try:
            result = run_comparison(symbol, days)
            if result:
                results.append(result)
        except Exception as e:
            print(f"\n❌ {symbol} 回测失败: {e}")
            import traceback
            traceback.print_exc()

    # 总结
    if results:
        print("\n\n" + "=" * 70)
        print("📊 总体总结")
        print("=" * 70)

        for res in results:
            symbol = res["symbol"]
            orig = res["original"]
            enh = res["enhanced"]

            ret_diff = enh["return_pct"] - orig["return_pct"]
            wr_diff = enh["win_rate"] - orig["win_rate"]

            print(f"\n{symbol}:")
            print(f"  原版收益: {orig['return_pct']:+.2f}% | 胜率: {orig['win_rate']:.1f}%")
            print(f"  增强收益: {enh['return_pct']:+.2f}% | 胜率: {enh['win_rate']:.1f}%")
            print(f"  差异:     {ret_diff:+.2f}% | {wr_diff:+.1f}%")


if __name__ == "__main__":
    main()
