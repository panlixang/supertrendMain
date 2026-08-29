"""从线上服务器缓存拉取 ETH/BTC 的 K 线数据（本地 DNS 被劫持无法直连 OKX）。

流程：当前品种=BTC → 拉 BTC → 切 ETH → 拉 ETH → 切回 BTC。
数据存 _live_data/{SYM}.json：{"1h": [...], "15m": [...], "4h": [...], "1d": [...]}
"""
import json, os, sys, time, urllib.request

BASE = "http://43.108.10.84:5174"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data")
TFS = ("1h", "15m", "4h", "1d")


def get(url, timeout=180):
    req = urllib.request.Request(url, headers={"User-Agent": "supertrend-bt/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def post(url, body, timeout=60):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "supertrend-bt/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def dump_symbol(tag: str, symbol: str):
    out = {}
    for tf in TFS:
        d = get(f"{BASE}/api/candles?tf={tf}")
        print(f"  [{tag}] {tf}: {len(d)} 根", flush=True)
        if d and "sample" not in out:
            out["sample"] = d[0]
        if d and "sample_last" not in out:
            out["sample_last"] = d[-1]
        out[tf] = d
    with open(os.path.join(OUT_DIR, f"{tag}.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"  [{tag}] saved {len(out)} tfs, sample keys={list(out.get('sample', {}).keys())}")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    cur = get(f"{BASE}/api/symbol")["symbol"]
    print("当前品种:", cur, flush=True)

    for tag, sym in (("BTC", "BTC-USDT-SWAP"), ("ETH", "ETH-USDT-SWAP")):
        if sym == cur:
            print(f"{tag} 就是当前品种，直接拉", flush=True)
            dump_symbol(tag, sym)
            continue
        r = post(f"{BASE}/api/symbol", {"instId": sym})
        print(f"切换到 {sym}: {r}", flush=True)
        time.sleep(3)          # 等 feed 切品种 + 历史填充
        dump_symbol(tag, sym)

    # 恢复原品种
    if cur != "BTC-USDT-SWAP" and cur != "ETH-USDT-SWAP":
        print(f"恢复品种 {cur}:", post(f"{BASE}/api/symbol", {"instId": cur}), flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
