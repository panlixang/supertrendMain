# -*- coding: utf-8 -*-
"""
从 43 服务（43.108.10.84:5174）拉取线上品种的真实参数，落成回测 config。

用法：
  python fetch_live_43.py              # 拉 BTC / ETH（默认）
  python fetch_live_43.py BTC ETH      # 指定关键字（模糊匹配 symbol）
  python fetch_live_43.py --list       # 只列出线上所有品种

输出：
  backend/backtest/{btc,eth}/xxx_config.json  （结构 = 线上品种对象，可直接给 engine 回测）
"""

import os
import sys
import json
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

HOST = os.environ.get("HOST43", "http://43.108.10.84:5174")
BACKEND = os.path.dirname(os.path.abspath(__file__))

# 关键字 -> 输出 config 路径
DEFAULT_OUT = {
    "BTC": os.path.join(BACKEND, "backtest", "btc", "btc_config.json"),
    "ETH": os.path.join(BACKEND, "backtest", "eth", "eth_config.json"),
}


def get(path, timeout=30):
    with urllib.request.urlopen(HOST + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    args = [a for a in sys.argv[1:]]
    if "--list" in args:
        syms = [s["symbol"] for s in get("/api/trade/symbols")["symbols"]]
        print(f"{HOST} 线上品种({len(syms)}):")
        for s in syms:
            print("  " + s)
        return

    keys = [a.upper() for a in args] or list(DEFAULT_OUT)
    live = {s["symbol"]: s for s in get("/api/trade/symbols")["symbols"]}
    print(f"目标服务: {HOST}")
    print(f"线上品种: {sorted(live)}")
    print("=" * 78)

    for key in keys:
        hits = [s for s in live if key in s.upper()]
        if not hits:
            print(f"\n### {key}: 线上没找到匹配的品种，跳过")
            continue
        sym = hits[0]
        cfg = live[sym]
        out = DEFAULT_OUT.get(key) or os.path.join(
            BACKEND, "backtest", key.lower(), f"{key.lower()}_config.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

        p = cfg.get("params", {})
        ex = cfg.get("exit_rules", {})
        print(f"\n### {sym}  ->  {out}")
        print(f"  enabled={cfg.get('enabled')}  margin={cfg.get('margin_usdt')}U  "
              f"lev={cfg.get('leverage')}x  allow_tfs={cfg.get('allow_tfs')}")
        print(f"  ST: {p.get('periods')}x{p.get('multiplier')}  src={p.get('src')}  "
              f"chg_atr={p.get('change_atr')}  ma={p.get('fast_len')}/{p.get('slow_len')}"
              f"{p.get('ma_type')}")
        print(f"  闸门: full={cfg.get('scoring_full_threshold')} "
              f"half={cfg.get('scoring_half_threshold')} "
              f"alert={cfg.get('scoring_alert_threshold')}  "
              f"use_scoring={cfg.get('use_scoring')}  score_engine={cfg.get('score_engine')!r}")
        print(f"  ER: trend={cfg.get('er_trend')} min={cfg.get('er_min')} "
              f"weak={cfg.get('er_weak_min')} hide_below={cfg.get('er_hide_below')}")
        print(f"  出场: tp1={ex.get('tp1_pct')}%/{ex.get('tp1_ratio')}%  "
              f"tp2={ex.get('tp2_pct')}%/{ex.get('tp2_ratio')}%  "
              f"tp3={ex.get('tp3_pct')}%/{ex.get('tp3_ratio')}% mode={ex.get('tp3_mode')}")
        print(f"        sl={ex.get('sl_mode')} {ex.get('sl_pct')}%  "
              f"trail={ex.get('trail_with_st')}  move_sl_to_entry={ex.get('move_sl_to_entry')}  "
              f"max_loss={ex.get('max_loss_enabled')}({ex.get('max_loss_pct')}%)")
        print(f"  过滤: range={cfg.get('range_filter_enabled')} "
              f"mtf={cfg.get('mtf_filter_enabled')} atr={cfg.get('atr_filter_enabled')} "
              f"adx={cfg.get('adx_filter_enabled')} filter_v3={cfg.get('filter_v3')}")


if __name__ == "__main__":
    main()
