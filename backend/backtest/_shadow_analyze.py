# -*- coding: utf-8 -*-
"""Shadow 数据分析器（阶段4：被过滤信号诊断，实时样本版）。

读取 backend/logs/shadow_<SYMBOL>.jsonl（shadow.py 落盘），按主/副引擎的
下单分歧分桶统计。future_return_pct 字段由 _shadow_backfill.py 回填；
未回填时先给分歧样本数，供积累样本用。

口径（买 = trade_full / trade_half）：
    A 一致买      : 两个引擎都放行 → 执行的交易
    B 主买副拒    : 主引擎放行、shadow 拒 → 主引擎多做的
    C 主拒副买    : 主引擎拒、shadow 放行 → shadow 多做的（含被主引擎过滤的）
    D 一致不买    : 都拒

典型判读（现网主引擎 v1、shadow v2）：
    C 桶若 future 均值 < 0 → v2 过滤掉的是亏损信号（过滤有效，可考虑切 v2）
    C 桶若 future 均值 > 0 → v2 在杀赢家（过度过滤，维持 v1）
"""
from __future__ import annotations

import argparse
import json
import os
import sys

SHADOW_DIR = os.environ.get(
    "SHADOW_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs"))

TRADE = {"trade_full", "trade_half"}


def load_rows(symbol_filter: str = ""):
    rows = []
    if not os.path.isdir(SHADOW_DIR):
        return rows
    for fn in sorted(os.listdir(SHADOW_DIR)):
        if not fn.startswith("shadow_") or not fn.endswith(".jsonl"):
            continue
        if symbol_filter and symbol_filter.upper() not in fn.upper():
            continue
        with open(os.path.join(SHADOW_DIR, fn), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def bucket_of(r: dict) -> str:
    ma, sa = r.get("main_action"), r.get("shadow_action")
    m_trade, s_trade = ma in TRADE, sa in TRADE
    if m_trade and s_trade:
        return "A_一致买"
    if m_trade and not s_trade:
        return "B_主买副拒"
    if not m_trade and s_trade:
        return "C_主拒副买"
    return "D_一致不买"


def summarize(rows, with_future: bool):
    from collections import OrderedDict
    buckets = OrderedDict()
    for r in rows:
        b = bucket_of(r)
        d = buckets.setdefault(b, {"n": 0, "futs": [], "sides": set()})
        d["n"] += 1
        d["sides"].add(r.get("side"))
        f = r.get("future_return_pct")
        if isinstance(f, (int, float)):
            d["futs"].append(f)
    print(f"样本总数: {len(rows)}"
          + ("" if with_future else "（未回填未来收益，仅分歧计数）"))
    order = ["A_一致买", "B_主买副拒", "C_主拒副买", "D_一致不买"]
    for b in order:
        if b not in buckets:
            continue
        d = buckets[b]
        line = f"  {b:<10} n={d['n']:>4}  sides={'/'.join(sorted(d['sides']))}"
        if d["futs"]:
            avg = sum(d["futs"]) / len(d["futs"])
            pos = sum(1 for x in d["futs"] if x > 0)
            line += (f"  future avg={avg:+.3f}%  win={pos}/{len(d['futs'])} "
                     f"({pos/len(d['futs'])*100:.0f}%)")
        print(line)
    return buckets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="", help="只看某品种（子串匹配）")
    args = ap.parse_args()
    rows = load_rows(args.symbol)
    if not rows:
        print(f"{SHADOW_DIR} 下无 shadow_*.jsonl 样本。")
        print("用法：在品种上启用 shadow_engine（另一引擎名），等待信号判单产生样本，")
        print("再运行 backtest/_shadow_backfill.py 回填未来收益后重跑本脚本。")
        sys.exit(0)
    with_future = any(isinstance(r.get("future_return_pct"), (int, float)) for r in rows)
    print(f"文件目录: {SHADOW_DIR}")
    if rows:
        print(f"首个样本引擎: {rows[0].get('main_engine')} vs {rows[0].get('shadow_engine')}")
    summarize(rows, with_future)
    # 分歧明细：列最近 20 条主/副分歧
    disc = [r for r in rows if bucket_of(r) in ("B_主买副拒", "C_主拒副买")]
    if disc:
        print(f"\n分歧样本最近 {min(20, len(disc))} 条（新→旧）：")
        for r in disc[-20:]:
            print(f"  {r.get('symbol')} {r.get('ts')} {r.get('side')} "
                  f"主[{r.get('main_engine')} {r.get('main_score')} {r.get('main_action')}] "
                  f"副[{r.get('shadow_engine')} {r.get('shadow_score')} {r.get('shadow_action')}]"
                  + (f" future={r.get('future_return_pct')}%" if r.get("future_return_pct") is not None else ""))


if __name__ == "__main__":
    main()
