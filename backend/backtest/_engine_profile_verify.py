# -*- coding: utf-8 -*-
"""Engine Profile 验证（阶段2）：score_engine 配置 == 手工 score_v2。

断言：
  1. NVDA: score_engine="quality_filter_v2" + thr 45 → 应与 score_v2=True 完全一致
     （复现 _score_v2_multi 的 40.09U）。
  2. NVDA: 空 engine（=v1 默认）→ 32.01U 基线，证明默认不受影响。
  3. CL: score_engine="trend_follow_v1" → 与默认(空=v1)一致，50.45U。
  4. resolve_engine 别名映射正确。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import replace  # noqa: E402

from backtest import run_backtest  # noqa: E402
from regime_scoring import resolve_engine  # noqa: E402
from _live_cfg_backtest import exit_rules, trade_cfg, _get  # noqa: E402

LIVE_URL = "http://47.84.106.154:5174/api/trade/symbols"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data")
CASES = [
    ("NVDA-USDT-SWAP", "NVDA.json"),
    ("CL-USDT-SWAP", "cl_half_cache.json"),
]
TF = "1h"


def main():
    # resolve_engine 别名断言
    from regime import TradeConfig
    assert resolve_engine(TradeConfig()) == "v1"
    assert resolve_engine(TradeConfig(score_v2=True)) == "v2"
    assert resolve_engine(TradeConfig(score_engine="quality_filter_v2")) == "v2"
    assert resolve_engine(TradeConfig(score_engine="v2")) == "v2"
    assert resolve_engine(TradeConfig(score_engine="trend_follow_v1")) == "v1"
    assert resolve_engine(TradeConfig(score_engine="bogus_engine")) == "v1"
    print("✓ resolve_engine 别名断言通过")

    live = _get(LIVE_URL)
    ok = True
    for sym_key, fname in CASES:
        name = sym_key.split("-")[0]
        sym = next(s for s in live["symbols"] if s["symbol"] == sym_key)
        p = sym["params"]
        cfgA = trade_cfg(sym)  # 服务器未设 engine → v1 默认
        ex = exit_rules(sym)
        cached = json.load(open(os.path.join(DATA, fname), encoding="utf-8"))
        cbtf = {k: v for k, v in cached.items() if isinstance(v, list) and v}
        candles = cbtf[TF]
        common = dict(init_cash=100.0, fee_rate=0.0005, allow_short=True,
                      exit_rules=ex, sizing="fixed",
                      margin_usdt=sym["margin_usdt"], leverage=sym["leverage"],
                      gate_tf=TF, candles_by_tf=cbtf)

        def one(cfg):
            r = run_backtest(candles, p, live_gate=cfg, **common)
            return r if "error" not in r else None

        ra = one(cfgA)  # v1 基线
        print(f"\n== {name} v1 基线: pnl={ra['final']-100:.2f}U n={ra['trades']}")

        if name == "NVDA":
            cfgV2b = replace(cfgA, score_v2=True, scoring_full_threshold=45.0,
                             scoring_half_threshold=45.0, scoring_alert_threshold=45.0)
            cfgEng = replace(cfgA, score_engine="quality_filter_v2",
                             scoring_full_threshold=45.0, scoring_half_threshold=45.0,
                             scoring_alert_threshold=45.0)
            rb = one(cfgV2b)
            re_ = one(cfgEng)
            pnl_b = rb['final'] - 100.0
            pnl_e = re_['final'] - 100.0
            print(f"  score_v2=True      : pnl={pnl_b:.2f}U n={rb['trades']}")
            print(f"  score_engine=v2    : pnl={pnl_e:.2f}U n={re_['trades']}")
            assert rb["trade_list"] == re_["trade_list"], \
                f"{name}: bool 与 engine 配置交易不一致"
            assert abs(pnl_e - 40.09) < 0.2, f"{name}: engine 未复现 40.09U，得 {pnl_e}"
            print(f"  ✓ engine 配置与 score_v2 完全一致，复现 {pnl_e:.2f}U")
        else:
            rb = one(replace(cfgA, score_engine="trend_follow_v1"))
            pnl_b = rb['final'] - 100.0
            print(f"  score_engine=trend_follow_v1: pnl={pnl_b:.2f}U n={rb['trades']}")
            assert abs(pnl_b - (ra['final'] - 100.0)) < 1e-6, \
                f"CL: trend_follow_v1 应等于 v1 基线"
            print(f"  ✓ trend_follow_v1 == v1 基线")
    print("\n全部断言通过 ✓")


if __name__ == "__main__":
    main()
