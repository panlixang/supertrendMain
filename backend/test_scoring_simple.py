"""
简化测试 - 检查打分制是否正常工作
"""

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent))

from regime import TradeConfig, evaluate
from history import fetch_candles

print("="*70)
print("测试：打分制集成")
print("="*70)

# 加载测试数据
raw_candles = fetch_candles("1h", 200, "BTC-USDT")
# 转换为字典格式
candles = [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol}
           for c in raw_candles]
print(f"已加载 {len(candles)} 根K线")

# 创建测试信号
sig = {
    "type": "buy",
    "price": candles[-1]["c"],
    "tf": "1h",
    "score": 2,
    "grade": "B"
}

# 配置（打分制开启）
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

print("\n配置：")
print(f"  use_scoring: {cfg.use_scoring}")
print(f"  全仓阈值: {cfg.scoring_full_threshold}")
print(f"  半仓阈值: {cfg.scoring_half_threshold}")

# 执行评估
result = evaluate(sig, candles, cfg)

print("\n结果：")
print(f"  是否下单: {result.get('trade')}")
print(f"  是否半仓: {result.get('trade_half', False)}")

if 'score_detail' in result:
    print("\n[SUCCESS] 打分制集成成功！")
    sd = result['score_detail']
    print(f"  总分: {sd['total']:.1f}")
    print(f"  置信度: {sd['confidence']}")
    print(f"  建议: {sd['suggestion']}")
    print(f"\n  分项得分:")
    for k, v in sd['breakdown'].items():
        print(f"    {k}: {v}")
else:
    print("\n[FAILED] 打分制未生效")
    print(f"  返回字段: {list(result.keys())}")

print("\n" + "="*70)
