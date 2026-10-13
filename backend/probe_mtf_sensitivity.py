# -*- coding: utf-8 -*-
"""
MTF 周期敏感度实测：
同一份回测配置，只改 candles_by_tf 里给了哪些周期，比较回测结果是否变化。

  A = {15m, 1h, 4h}   <- 本地 db 现状（回测口径）
  B = {1h, 4h}
  C = {1h}            <- 极端：只给信号周期

若三者结果完全一致，说明多周期数据根本没参与决策，
那么线上多出的 5m/30m/1d/1w/1M 也不会改变任何一笔交易。

用法: python probe_mtf_sensitivity.py
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)                       # engine 内部 import 依赖 cwd=backend
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "backtest"))

from engine import run_core, _load_tf, _extract   # noqa: E402

CASES = [
    ("A {15m,1h,4h}", ["15m", "1h", "4h"]),
    ("B {1h,4h}",     ["1h", "4h"]),
    ("C {1h}",        ["1h"]),
]

TARGETS = [
    ("SNDK-USDT", os.path.join(HERE, "backtest", "sndk", "sndk_config.json")),
    ("NVDA-USDT", os.path.join(HERE, "backtest", "nvda", "nvda_config.json")),
]

KEYS = ("n", "ret_pct", "pf", "winrate", "pnl")


def brief(seg):
    if not seg:
        return "-"
    return f"n={seg.get('n')} ret={seg.get('ret_pct')}% pf={seg.get('pf')} " \
           f"win={seg.get('winrate')}%"


def main():
    for db_sym, path in TARGETS:
        cfg = json.load(open(path, "r", encoding="utf-8"))
        sc, pr, er = _extract(cfg)
        print("=" * 78)
        print(f"### {db_sym}   score_engine={sc.get('score_engine')!r}  "
              f"mtf_filter_enabled={sc.get('mtf_filter_enabled')}")
        results = {}
        for label, tfs in CASES:
            candles = {t: _load_tf(db_sym, t) for t in tfs}
            r = run_core(db_sym, sc, pr, er, tf="1h", limit=0, candles=candles)
            results[label] = r
            print(f"  [{label}]  全样本 {brief(r.get('all'))}")
            print(f"               IS {brief(r.get('is'))}")
            print(f"              OOS {brief(r.get('oos'))}")
        base = results[CASES[0][0]]
        same = all(
            all(results[l].get(k) == base.get(k) for k in ("all", "is", "oos"))
            for l, _ in CASES
        )
        print(f"  => 三种周期集合结果{'完全一致' if same else '不一致 <<< 存在差异'}")
    print("=" * 78)


if __name__ == "__main__":
    main()
