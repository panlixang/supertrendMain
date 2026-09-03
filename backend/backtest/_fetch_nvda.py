"""拉取 NVDA-USDT-SWAP 多周期 K 线存到 _live_data/NVDA.json。"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _live_cfg_backtest import BARS, BIAS_TFS, fetch_candles

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data", "NVDA.json")
SYMBOL = "NVDA-USDT-SWAP"
GATE_TF = "1h"


def main():
    cbtf = {}
    bars = BARS.get(GATE_TF, 4500)
    print(f"=== {SYMBOL} ===", flush=True)
    cbtf[GATE_TF] = fetch_candles(SYMBOL, GATE_TF, bars)
    print(f"  {GATE_TF}: {len(cbtf[GATE_TF])} 根", flush=True)
    for tf in BIAS_TFS:
        if tf == GATE_TF:
            continue
        extra = fetch_candles(SYMBOL, tf, min(bars, BARS.get(tf, 4500)))
        print(f"  {tf}: {len(extra)} 根", flush=True)
        if extra:
            cbtf[tf] = extra
        time.sleep(0.3)
    if not cbtf[GATE_TF]:
        print("无数据，退出", flush=True)
        return
    from _live_cfg_backtest import ts_fmt
    c = cbtf[GATE_TF]
    print(f"  时间范围 {ts_fmt(c[0]['ts'])} ~ {ts_fmt(c[-1]['ts'])}", flush=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(cbtf, f, ensure_ascii=False)
    print(f"Wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()
