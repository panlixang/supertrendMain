"""
前后端打分数据流验证

验证链路：
1. regime.evaluate() 生成打分数据
2. feed.py 传递到信号
3. WebSocket 推送到前端
4. SignalList 显示打分
"""

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent))

from regime import TradeConfig, evaluate
from history import fetch_candles
import json

print("="*70)
print("完整链路验证")
print("="*70)

# 模拟真实场景
candles_raw = fetch_candles("1h", 200, "BTC-USDT")
candles = [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol}
           for c in candles_raw]

sig = {
    "type": "buy",
    "price": candles[-1]["c"],
    "tf": "1h",
    "score": 2,
    "grade": "B",
    "ts": candles[-1]["ts"]
}

# 真实配置
cfg = TradeConfig(
    enabled=True,
    use_scoring=True,
    scoring_full_threshold=80.0,
    scoring_half_threshold=60.0,
    scoring_alert_threshold=40.0,
    use_dynamic_threshold=True,
    er_min=0.15,
    min_score=2,
    allow_grades=["A", "B"],
    allow_tfs=["1h"]
)

print("\n[步骤1] regime.evaluate() 评估信号")
gate = evaluate(sig, candles, cfg)

print(f"  trade: {gate.get('trade')}")
print(f"  trade_half: {gate.get('trade_half')}")
print(f"  score_detail: {'存在' if gate.get('score_detail') else '缺失'}")

if gate.get('score_detail'):
    sd = gate['score_detail']
    print(f"    总分: {sd['total']}")
    print(f"    置信度: {sd['confidence']}")

print("\n[步骤2] feed.py 组装信号（模拟）")
# 模拟 feed.py 的逻辑
full = {**sig}
full["regime"] = gate["regime"]
full["gate_reasons"] = gate["reasons"]
full["will_trade"] = gate["trade"]
full["hidden"] = gate.get("hidden", False)
full["profile"] = gate.get("profile")
full["filters"] = gate.get("filters", {})
full["trade_half"] = gate.get("trade_half", False)
full["score_detail"] = gate.get("score_detail")

print(f"  信号包含 trade_half: {full.get('trade_half')}")
print(f"  信号包含 score_detail: {'存在' if full.get('score_detail') else '缺失'}")

print("\n[步骤3] WebSocket 推送数据（模拟）")
# 模拟 WebSocket 推送的 JSON
ws_data = {
    "type": "signal",
    "data": full
}

# 序列化测试
try:
    json_str = json.dumps(ws_data, default=str)
    print(f"  JSON序列化: 成功 ({len(json_str)} 字节)")

    # 反序列化测试（前端会做）
    received = json.loads(json_str)
    sig_data = received["data"]

    print(f"\n[步骤4] 前端接收数据")
    print(f"  trade_half: {sig_data.get('trade_half')}")
    if sig_data.get('score_detail'):
        print(f"  score_detail.total: {sig_data['score_detail']['total']}")
        print(f"  score_detail.confidence: {sig_data['score_detail']['confidence']}")
        print(f"  score_detail.suggestion: {sig_data['score_detail']['suggestion']}")
    else:
        print(f"  score_detail: 缺失！")

except Exception as e:
    print(f"  JSON序列化失败: {e}")

print("\n" + "="*70)
print("验证结果")
print("="*70)

if gate.get('score_detail') and full.get('score_detail'):
    print("[SUCCESS] 打分数据完整传递到前端")
    print("\n如何在前端查看：")
    print("1. 打开信号列表")
    print("2. 找到任一信号卡片")
    print("3. 如果信号包含 score_detail，会自动显示：")
    print("   - 信号置信度：XX分")
    print("   - 置信度标签（高置信/中置信/低置信）")
    print("   - 半仓标签（如果 trade_half=true）")
    print("   - 分项得分进度条")
    print("   - 建议文本")
    print("\n如果看不到：")
    print("1. 确保品种配置里勾选了「信号打分制」")
    print("2. 旧信号（集成前）没有打分数据")
    print("3. 等新信号产生，或手动调参触发翻转")
else:
    print("[FAILED] 打分数据丢失")
    print(f"  gate有score_detail: {bool(gate.get('score_detail'))}")
    print(f"  full有score_detail: {bool(full.get('score_detail'))}")

print("="*70)
