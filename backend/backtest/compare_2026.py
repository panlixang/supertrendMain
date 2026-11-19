# -*- coding: utf-8 -*-
"""对照：在 2026 窗口（2026-01-01 起）用 engine.run_core 跑指定 config（与 optimize_any 同口径）。
用法: python compare_2026.py <cfg1> [cfg2 ...]   （symbol 取自 cfg 的 "symbol" 字段，自动去 SWAP 后缀）
"""
import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from engine import run_core, _extract, _load_tf

TS_2026 = 1767225600000  # 2026-01-01 UTC

for cfg_path in sys.argv[1:]:
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    raw_sym = cfg.get("symbol", "")
    sym = raw_sym.replace("-SWAP", "").replace("SWAP", "")
    try:
        CANDLES = {tf: [c for c in _load_tf(sym, tf) if c["ts"] >= TS_2026]
                   for tf in ["15m", "1h", "4h"]}
    except Exception as ex:
        print(f"[警告] {raw_sym} 加载失败: {ex}")
        CANDLES = {"1h": []}
    if len(CANDLES["1h"]) == 0:
        print(f"\n[2026窗口] {raw_sym} ({sym}) config={os.path.basename(cfg_path)} 无数据，跳过")
        continue
    sc, pr, er = _extract(cfg)
    r = run_core(sym, sc, pr, er, tf="1h", candles=CANDLES)
    a, i, o = r["all"], r["is"], r["oos"]
    if a is None:
        print(f"\n[2026窗口] {raw_sym} ({sym}) config={os.path.basename(cfg_path)} 无成交")
        continue
    print(f"\n[2026窗口] {raw_sym} ({sym})  config={os.path.basename(cfg_path)}  1h根数={len(CANDLES['1h'])}")
    print(f"  全样本: 笔数={a['n']} 收益={a['ret_pct']:+.2f}% PF={a['pf']:.2f} "
          f"胜率={a['winrate']:.1f}% 回撤={r['max_dd']:.1f}%")
    print(f"  IS:    笔数={i['n']} 收益={i['ret_pct']:+.2f}% PF={i['pf']:.2f}")
    print(f"  OOS:   笔数={o['n']} 收益={o['ret_pct']:+.2f}% PF={o['pf']:.2f}")
