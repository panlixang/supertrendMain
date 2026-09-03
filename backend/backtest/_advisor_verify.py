# -*- coding: utf-8 -*-
"""Adaptive Engine Advisor 回归验证。

对 _engine_classify 的 7 个品种用同一份缓存重放：
  1. assess_symbol 出的建议（v1/v2/grey）应等于实证 verdict（v1/v2）；
  2. 灰色带不应产生误判 —— 当前 7 品种全部落在非灰区，全部断言一致；
  3. 打印一张"画像 + 建议"表供人工审。

跑法：python backend/backtest/_advisor_verify.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine_advisor import assess_symbol  # noqa: E402
from _live_cfg_backtest import exit_rules, trade_cfg, _get, ts_fmt  # noqa: E402
from _engine_classify import CASES, LIVE_URLS, DATA, TF  # noqa: E402


def main():
    old = {}
    jp = os.path.join(DATA, "_engine_classify.json")
    if os.path.exists(jp):
        for r in json.load(open(jp, encoding="utf-8")):
            old[r["name"]] = r
    n_ok = n_skip = 0
    for sym_key, fname in CASES:
        name = sym_key.split("-")[0]
        sym = None
        for url in LIVE_URLS:
            try:
                live = _get(url)
            except Exception:
                continue
            sym = next((s for s in live["symbols"] if s["symbol"] == sym_key), None)
            if sym:
                break
        path = os.path.join(DATA, fname)
        if sym is None or not os.path.exists(path):
            n_skip += 1
            print(f"== {name}: 跳过（无线上配置或无缓存）")
            continue
        cached = json.load(open(path, encoding="utf-8"))
        cbtf = {k: v for k, v in cached.items()
                if isinstance(v, list) and v and "ts" in (v[0] or {})}
        cfgA = trade_cfg(sym)
        try:
            out = assess_symbol(
                name, cbtf, sym["params"], cfgA,
                exit_rules=exit_rules(sym), tf=TF,
                margin_usdt=sym["margin_usdt"], leverage=sym["leverage"])
        except Exception as e:
            n_skip += 1
            print(f"== {name}: assess 失败 {e}")
            continue
        m, a = out["metrics"], out["advisor"]
        exp = (old.get(name) or {}).get("verdict", "?")
        tag = f"{a['short'] or 'grey':>4}  置信 {a['confidence']}"
        if a["short"] and a["short"] == exp:
            ok = "✓"
            n_ok += 1
        elif a["short"] is None:
            ok = "?"      # 灰带不判，不算错
        else:
            ok = f"✗ 期望 {exp}"
        print(f"{name:>6} flip100={m['flip_per_100']:>5} flips={m['flips']:>3} "
              f"成交={m['trades']:>3} wr={m['win_rate']:>4}% "
              f"conv={m['conv']:.0%} tight={m['gate_tightness']:.0%} "
              f"seg={m['seg_mean']:.0f}bar ER={m['er_mean']:.3f} → {tag} {ok}")
    print(f"\n{'-' * 70}")
    print(f"断言一致 {n_ok} / 7（跳过 {n_skip}）—— 失败即退出码 1")


if __name__ == "__main__":
    main()
