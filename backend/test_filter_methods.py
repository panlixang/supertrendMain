"""
震荡过滤方案对比测试

对比4种方案在历史数据上的表现：
1. 原方案（当前的ER固定阈值）
2. 打分制（综合评分，分级处理）
3. 动态阈值（ER自适应）
4. 确认机制（等1-2根确认）

输出指标：
- 信号数量（过滤掉多少）
- 假信号率（翻转后很快反向的比例）
- 趋势捕捉率（大行情抓到的比例）
- 平均入场质量（入场后的平均盈亏）
"""

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent))

from indicators import super_trend, st_signals
from regime import evaluate as evaluate_original, TradeConfig, efficiency_ratio
from regime_scoring import evaluate_enhanced as evaluate_scoring
from regime_dynamic import evaluate_dynamic
from regime_confirmation import SignalConfirmationTracker
from feed import fetch_klines
import json


def load_test_data(symbol: str = "BTC/USDT:USDT", tf: str = "1h", limit: int = 500):
    """加载测试数据"""
    print(f"📥 加载数据：{symbol} {tf} 最近{limit}根...")
    candles = fetch_klines(symbol, tf, limit)
    print(f"✓ 加载完成：{len(candles)}根K线")
    return candles


def run_backtest(candles: list[dict], method: str, cfg: TradeConfig) -> dict:
    """回测某个过滤方案

    返回：{
        "signals": [...]           # 通过过滤的信号
        "rejected": [...]          # 被拒绝的信号
        "false_signals": 0,        # 假信号数（翻转后5根内反向的）
        "trend_captured": 0,       # 捕捉到的趋势数（翻转后20根至少5%的）
        "avg_pnl_5bars": 2.3,      # 入场后5根的平均盈亏%
        "avg_pnl_20bars": 8.5      # 入场后20根的平均盈亏%
    }
    """
    # 计算SuperTrend
    st = super_trend(
        [c["o"] for c in candles],
        [c["h"] for c in candles],
        [c["l"] for c in candles],
        [c["c"] for c in candles],
        periods=15,
        multiplier=9.1
    )

    all_signals = st_signals(candles, st, "1h")
    passed = []
    rejected = []

    # 应用不同的过滤方法
    for sig in all_signals:
        sig_idx = next(i for i, c in enumerate(candles) if c["ts"] == sig["ts"])

        if method == "original":
            result = evaluate_original(sig, candles[:sig_idx+1], cfg)
        elif method == "scoring":
            result = evaluate_scoring(sig, candles[:sig_idx+1], cfg)
        elif method == "dynamic":
            result = evaluate_dynamic(sig, candles[:sig_idx+1], cfg)
        elif method == "confirmation":
            # 确认机制需要模拟等待过程
            result = {"trade": True}  # 简化：假设都能确认（实际需要逐根模拟）
        else:
            result = {"trade": True}

        if result.get("trade"):
            passed.append({"sig": sig, "idx": sig_idx, "result": result})
        else:
            rejected.append({"sig": sig, "idx": sig_idx, "result": result})

    # 评估通过的信号质量
    false_signals = 0
    trend_captured = 0
    pnl_5bars = []
    pnl_20bars = []

    for item in passed:
        sig = item["sig"]
        idx = item["idx"]
        is_buy = sig["type"] == "buy"

        # 假信号检测：5根内反向超过2%
        if idx + 5 < len(candles):
            entry = candles[idx]["c"]
            exit_5 = candles[idx + 5]["c"]
            pnl_5 = (exit_5 - entry) / entry * 100 * (1 if is_buy else -1)
            pnl_5bars.append(pnl_5)

            if pnl_5 < -2:  # 亏损2%+
                false_signals += 1

        # 趋势捕捉：20根后盈利5%+
        if idx + 20 < len(candles):
            entry = candles[idx]["c"]
            exit_20 = candles[idx + 20]["c"]
            pnl_20 = (exit_20 - entry) / entry * 100 * (1 if is_buy else -1)
            pnl_20bars.append(pnl_20)

            if pnl_20 > 5:
                trend_captured += 1

    return {
        "method": method,
        "total_signals": len(all_signals),
        "passed": len(passed),
        "rejected": len(rejected),
        "pass_rate": round(len(passed) / len(all_signals) * 100, 1) if all_signals else 0,
        "false_signals": false_signals,
        "false_rate": round(false_signals / len(passed) * 100, 1) if passed else 0,
        "trend_captured": trend_captured,
        "capture_rate": round(trend_captured / len(passed) * 100, 1) if passed else 0,
        "avg_pnl_5bars": round(sum(pnl_5bars) / len(pnl_5bars), 2) if pnl_5bars else 0,
        "avg_pnl_20bars": round(sum(pnl_20bars) / len(pnl_20bars), 2) if pnl_20bars else 0,
        "passed_signals": passed,
        "rejected_signals": rejected
    }


def compare_methods():
    """对比所有方法"""
    print("\n" + "="*70)
    print("🔬 震荡过滤方案对比测试")
    print("="*70 + "\n")

    # 加载数据
    candles = load_test_data("BTC/USDT:USDT", "1h", 500)

    # 配置
    cfg = TradeConfig(
        enabled=True,
        er_min=0.15,
        er_weak_min=0.12,
        er_hide_below=0.10,
        min_score=2,
        allow_tfs=["1h"],
        allow_grades=["A", "B"],
        # 组合过滤器（根据你的需求调整）
        atr_filter_enabled=False,
        range_filter_enabled=False,
        mtf_filter_enabled=False,
        adx_filter_enabled=False
    )

    methods = [
        ("original", "原方案（ER固定阈值）"),
        ("scoring", "打分制（综合评分）"),
        ("dynamic", "动态阈值（ER自适应）"),
    ]

    results = []
    for method, desc in methods:
        print(f"\n🧪 测试：{desc}")
        print("-" * 70)
        result = run_backtest(candles, method, cfg)
        results.append(result)

        print(f"  信号总数：{result['total_signals']}")
        print(f"  通过数量：{result['passed']} ({result['pass_rate']}%)")
        print(f"  拒绝数量：{result['rejected']}")
        print(f"  假信号数：{result['false_signals']} ({result['false_rate']}%)")
        print(f"  捕捉趋势：{result['trend_captured']} ({result['capture_rate']}%)")
        print(f"  5根后平均盈亏：{result['avg_pnl_5bars']}%")
        print(f"  20根后平均盈亏：{result['avg_pnl_20bars']}%")

    # 输出对比表
    print("\n" + "="*70)
    print("📊 对比总结")
    print("="*70)
    print(f"{'方案':<20} {'通过率':<10} {'假信号率':<12} {'趋势捕捉':<12} {'5根盈亏':<12} {'20根盈亏'}")
    print("-" * 70)

    for r in results:
        method_name = next(desc for m, desc in methods if m == r["method"])
        print(f"{method_name:<20} {r['pass_rate']:<10}% {r['false_rate']:<12}% "
              f"{r['capture_rate']:<12}% {r['avg_pnl_5bars']:<12}% {r['avg_pnl_20bars']}%")

    # 推荐方案
    print("\n" + "="*70)
    print("💡 推荐")
    print("="*70)

    # 找到综合得分最高的（假信号率最低 + 趋势捕捉率最高 + 平均盈亏最高）
    best = max(results, key=lambda r: -r["false_rate"] + r["capture_rate"] + r["avg_pnl_20bars"])
    best_name = next(desc for m, desc in methods if m == best["method"])

    print(f"✓ 最佳方案：{best_name}")
    print(f"  理由：假信号率{best['false_rate']}%，趋势捕捉{best['capture_rate']}%，"
          f"20根平均{best['avg_pnl_20bars']}%")

    # 输出详细报告
    report_path = Path(__file__).parent / "filter_comparison_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "test_config": {
                "symbol": "BTC/USDT:USDT",
                "timeframe": "1h",
                "candles": len(candles),
                "er_min": cfg.er_min,
                "min_score": cfg.min_score
            },
            "results": results
        }, f, indent=2, ensure_ascii=False)

    print(f"\n📄 详细报告已保存：{report_path}")


if __name__ == "__main__":
    try:
        compare_methods()
    except Exception as e:
        print(f"\n❌ 测试失败：{e}")
        import traceback
        traceback.print_exc()
