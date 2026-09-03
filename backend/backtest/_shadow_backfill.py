# -*- coding: utf-8 -*-
"""Shadow 样本未来收益回填（阶段4）。

把 logs/shadow_<SYMBOL>.jsonl 里每条信号，用该品种 1h 收盘 K 线回填：
    future_return_pct = (信号 ts 后第 horizon 根已收盘 1h 的 close - 信号价) / 信号价 × 100

注意这是"未来 N 根价格收益"的近似代理（不做止盈止损/平仓模拟），用于快速
判断被过滤集合的赢亏方向，已足够指导引擎取舍。K 线来源优先级：
    1. --candles 指定的 JSON 缓存（_live_data 格式，含 1h key）
    2. 直连 OKX 拉取（需可联网，限最近 4500 根）

原地回填会保留原行并追加 future_return_pct / horizon。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import bisect

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SHADOW_DIR = os.environ.get(
    "SHADOW_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs"))
HORIZON_H = 24  # 默认看未来 24 根 1h


def load_candles_local(path: str, tf: str = "1h") -> list[dict]:
    if not path or not os.path.exists(path):
        return []
    cached = json.load(open(path, encoding="utf-8"))
    for k, v in cached.items():
        if isinstance(v, list) and v and "ts" in v[0] and (k == tf or k.lower() == tf.lower()):
            return v
    return []


def load_candles_okx(symbol: str, limit: int = 4500) -> list[dict]:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from _live_cfg_backtest import fetch_candles
        return fetch_candles(symbol, "1h", limit)
    except Exception as e:
        print(f"  OKX 拉取失败: {e}")
        return []


def backfill(path: str, candles: list[dict], horizon: int):
    ts_list = [c["ts"] for c in candles]
    out = []
    changed = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("future_return_pct") is not None:
                out.append(r)
                continue
            ts = r.get("ts")
            price = r.get("price")
            i = bisect.bisect_right(ts_list, ts)  # 下一条未收盘
            if i < len(candles):
                future = candles[min(i + horizon, len(candles) - 1)]["c"]
                if price:
                    r["future_return_pct"] = round((future - price) / price * 100, 3)
                    r["horizon"] = horizon
                    changed += 1
            out.append(r)
    with open(path, "w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candles", default="", help="_live_data 格式 JSON 缓存（含 1h）")
    ap.add_argument("--horizon", type=int, default=HORIZON_H, help="未来 N 根 1h")
    args = ap.parse_args()
    if not os.path.isdir(SHADOW_DIR):
        print(f"无 shadow 目录: {SHADOW_DIR}")
        sys.exit(1)
    total = 0
    for fn in sorted(os.listdir(SHADOW_DIR)):
        if not fn.startswith("shadow_") or not fn.endswith(".jsonl"):
            continue
        path = os.path.join(SHADOW_DIR, fn)
        sym_key = fn[len("shadow_"):-len(".jsonl")] + "-USDT-SWAP"
        candles = load_candles_local(args.candles) if args.candles else load_candles_okx(sym_key)
        if not candles:
            print(f"{fn}: 无 K 线，跳过（可用 --candles 指缓存）")
            continue
        n = backfill(path, candles, args.horizon)
        total += n
        print(f"{fn}: 回填 {n} 条 (horizon={args.horizon}h)")
    print(f"完成，共回填 {total} 条。重新运行 _shadow_analyze.py 查看分桶收益。")


if __name__ == "__main__":
    main()
