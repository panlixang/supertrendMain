# -*- coding: utf-8 -*-
"""
从指定服务(47 / 43)拉取线上品种的真实参数，落成回测 BASE config。

用法:
  python fetch_live_cfg.py 47 SKHYNIX MU
  python fetch_live_cfg.py 43 ETH

输出: backend/backtest/<key>/<key>_live_config.json  （结构=线上品种对象，可直接给 engine / optimize_any）
"""

import os
import sys
import json
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

HOSTS = {
    "47": "http://47.84.106.154:5174",
    "43": "http://43.108.10.84:5174",
}
BACKEND = os.path.dirname(os.path.abspath(__file__))


def main():
    if len(sys.argv) < 3:
        print("用法: python fetch_live_cfg.py <47|43> <KEY> [KEY...]")
        return
    tag = sys.argv[1]
    keys = sys.argv[2:]
    host = HOSTS.get(tag)
    if not host:
        print(f"未知服务 {tag}，可选 {list(HOSTS)}")
        return

    with urllib.request.urlopen(host + "/api/trade/symbols", timeout=25) as r:
        live = {s["symbol"]: s for s in json.loads(r.read().decode())["symbols"]}
    print(f"服务 {tag} ({host})   线上 {len(live)} 品种: {sorted(live)}")

    for key in keys:
        hits = [s for s in live if key.upper() in s.upper()]
        if not hits:
            print(f"\n### {key}: 线上没找到匹配品种，跳过")
            continue
        sym = hits[0]
        cfg = live[sym]
        out = os.path.join(BACKEND, "backtest", key.lower(),
                           f"{key.lower()}_live_config.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

        p = cfg.get("params", {})
        ex = cfg.get("exit_rules", {})
        print(f"\n### {sym}  ->  {out}")
        print(f"  enabled={cfg.get('enabled')}  margin={cfg.get('margin_usdt')}U  "
              f"lev={cfg.get('leverage')}x  allow_tfs={cfg.get('allow_tfs')}")
        print(f"  ST: {p.get('periods')}x{p.get('multiplier')} src={p.get('src')} "
              f"chg_atr={p.get('change_atr')}  闸门: {cfg.get('scoring_full_threshold')}"
              f"/{cfg.get('scoring_alert_threshold')}")
        print(f"  ER: trend={cfg.get('er_trend')} min={cfg.get('er_min')} "
              f"weak={cfg.get('er_weak_min')}")
        print(f"  出场: tp1={ex.get('tp1_pct')}%/{ex.get('tp1_ratio')}%  "
              f"tp2={ex.get('tp2_pct')}%/{ex.get('tp2_ratio')}%  "
              f"sl={ex.get('sl_mode')} {ex.get('sl_pct')}% trail={ex.get('trail_with_st')}")
        print(f"  过滤: range={cfg.get('range_filter_enabled')} "
              f"mtf={cfg.get('mtf_filter_enabled')} atr={cfg.get('atr_filter_enabled')} "
              f"adx={cfg.get('adx_filter_enabled')} "
              f"score_engine={cfg.get('score_engine')!r}")


if __name__ == "__main__":
    main()
