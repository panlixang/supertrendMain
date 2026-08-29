"""全品种数据刷新：直连 OKX 拉取线上品种 + QQQ/SKHYNIX 的多周期 K 线，覆盖写入 _live_data。

与 _fetch_live_data.py（走线上服务器、需切品种）不同，本脚本统一直连 OKX，
不影响线上实盘交易品种，供月度滚动寻优前刷新数据使用。

用法:
  python _refresh_data.py             # 刷新全部品种
  python _refresh_data.py --symbols BTC,QQQ   # 只刷新指定品种
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _live_cfg_backtest import BARS, BIAS_TFS, LIVE_URL, _get, fetch_candles

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data")
GATE_TF = "1h"

# 未上线但已拉数据的模板品种（直连 OKX）
EXTRA = ["QQQ-USDT-SWAP", "SKHYNIX-USDT-SWAP"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="", help="只刷新指定品种，逗号分隔")
    args = ap.parse_args()

    live = _get(LIVE_URL)
    symbols = [s["symbol"] for s in live["symbols"] if "USDT-SWAP" in s.get("symbol", "")]
    symbols = list(dict.fromkeys(symbols + EXTRA))
    if args.symbols:
        want = {s.strip().upper() for s in args.symbols.split(",")}
        symbols = [s for s in symbols
                   if any(w in s or w == s.split("-")[0] for w in want)]

    os.makedirs(DATA_DIR, exist_ok=True)
    for symbol in symbols:
        name = symbol.split("-")[0]
        out_path = os.path.join(DATA_DIR, f"{name}.json")
        cbtf = {}
        print(f"=== {symbol} ===", flush=True)
        bars = BARS.get(GATE_TF, 4500)
        cbtf[GATE_TF] = fetch_candles(symbol, GATE_TF, bars)
        print(f"  {GATE_TF}: {len(cbtf[GATE_TF])} 根", flush=True)
        for tf in BIAS_TFS:
            if tf == GATE_TF:
                continue
            try:
                extra = fetch_candles(symbol, tf, min(bars, BARS.get(tf, 4500)))
            except Exception as e:
                print(f"  {tf}: 拉取失败 {e}", flush=True)
                continue
            print(f"  {tf}: {len(extra)} 根", flush=True)
            if extra:
                cbtf[tf] = extra
            time.sleep(0.3)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(cbtf, f, ensure_ascii=False)
        print(f"Wrote {out_path}", flush=True)
        time.sleep(0.5)
    print("done", flush=True)


if __name__ == "__main__":
    main()
