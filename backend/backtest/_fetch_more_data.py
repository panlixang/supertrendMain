"""从线上服务器缓存拉取 MU/SPCX/SNDK 的 K 线数据。
流程：记录当前品种 → 依次切到目标品种拉取 → 恢复原品种。
"""
import json, os, time, urllib.request

BASE = "http://43.108.10.84:5174"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data")
TFS = ("1h", "15m", "4h", "1d")
TARGETS = (("MU", "MU-USDT-SWAP"), ("SPCX", "SPCX-USDT-SWAP"),
           ("SNDK", "SNDK-USDT-SWAP"))


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


def dump_symbol(tag: str):
    out = {}
    for tf in TFS:
        d = get(f"{BASE}/api/candles?tf={tf}")
        print(f"  [{tag}] {tf}: {len(d)} 根", flush=True)
        out[tf] = d
    with open(os.path.join(OUT_DIR, f"{tag}.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"  [{tag}] saved", flush=True)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    cur = get(f"{BASE}/api/symbol")["symbol"]
    print("当前品种:", cur, flush=True)

    for tag, sym in TARGETS:
        if sym == cur:
            print(f"{tag} 就是当前品种，直接拉", flush=True)
        else:
            r = post(f"{BASE}/api/symbol", {"instId": sym})
            print(f"切换到 {sym}: ok={r.get('ok')}", flush=True)
            time.sleep(3)
        dump_symbol(tag)

    if cur != "BTC-USDT-SWAP" and cur != "ETH-USDT-SWAP" and cur not in [s for _, s in TARGETS]:
        print(f"恢复品种 {cur}:", post(f"{BASE}/api/symbol", {"instId": cur}), flush=True)
    else:
        print(f"恢复品种 BTC:", post(f"{BASE}/api/symbol", {"instId": "BTC-USDT-SWAP"}), flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
