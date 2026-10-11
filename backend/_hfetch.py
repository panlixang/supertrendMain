# -*- coding: utf-8 -*-
"""用 history-candles 端点为 INTC/AMD 拉历史 K 线并恢复本地缓存。
带网络重试（应对 DNS/瞬时断网）。history-candles 对下架品种也有效。
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_mu_home as M

OKX = ["https://www.okx.com", "https://okx.com"]


def fetch_hist(symbol, tf, limit, tries=6):
    bar = M.OKX_BAR[tf]
    for t in range(tries):
        collected = {}
        after = None
        try:
            while len(collected) < limit:
                page_n = min(300, limit - len(collected))
                qs = f"instId={symbol}&bar={bar}&limit={page_n}"
                if after:
                    qs += f"&after={after}"
                data = None
                for base in OKX:
                    try:
                        data = M._get(f"{base}/api/v5/market/history-candles?{qs}")
                        if data and data.get("code") == "0":
                            break
                    except Exception:
                        continue
                if not data or data.get("code") != "0" or not data.get("data"):
                    break
                rows = data["data"]
                if not rows:
                    break
                for row in rows:
                    try:
                        if len(row) > 8 and row[8] != "1":
                            continue
                        ts = int(row[0])
                        collected[ts] = {"ts": ts, "o": float(row[1]), "h": float(row[2]),
                                         "l": float(row[3]), "c": float(row[4]), "vol": float(row[5])}
                    except (IndexError, ValueError):
                        continue
                after = rows[-1][0]
                if len(rows) < page_n:
                    break
                time.sleep(0.06)
            if collected:
                return [collected[k] for k in sorted(collected)][-limit:]
        except Exception as e:
            print(f"    [{symbol} {tf}] 第{t+1}次重试出错: {e}", flush=True)
        print(f"    [{symbol} {tf}] 第{t+1}次未取到，{15}s 后重试", flush=True)
        time.sleep(15)
    return []


HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "_bt2026_data")
SYMS = {"INTC": "INTC-USDT-SWAP", "AMD": "AMD-USDT-SWAP"}
NEED = {"1h": 8200, "4h": 2500}

for base, sym in SYMS.items():
    d = {}
    for tf, n in NEED.items():
        arr = fetch_hist(sym, tf, n)
        d[tf] = arr
        print(f"  {base} {tf}: 拉到 {len(arr)} 根", flush=True)
    json.dump({"sym": sym, "1h": d["1h"], "4h": d["4h"]},
              open(os.path.join(DATA, f"{base}_USDT_SWAP.json"), "w"), ensure_ascii=False)
    print(f"  已保存 {base}_USDT_SWAP.json", flush=True)
print("DONE", flush=True)
