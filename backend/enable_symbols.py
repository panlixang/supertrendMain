# -*- coding: utf-8 -*-
"""启用/停用指定服务的交易品种（enabled 开关）。

用法: python enable_symbols.py <43|47> <true|false> <SYM1> [SYM2 ...]
"""
import sys
import json
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

HOSTS = {"43": "http://43.108.10.84:5174", "47": "http://47.84.106.154:5174"}


def http(host, method, path, payload=None, timeout=40):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(host + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    tag = sys.argv[1]
    wanted = sys.argv[2].strip().lower() == "true"
    syms = [s.strip().upper() for s in sys.argv[3:]]
    host = HOSTS[tag]
    print(f"服务 {tag} ({host})  enabled -> {wanted}")

    before = {s["symbol"]: s for s in http(host, "GET", "/api/trade/symbols")["symbols"]}
    for sym in syms:
        if sym not in before:
            print(f"  {sym}: 不在交易列表")
            continue
        cur = before[sym].get("enabled")
        if cur == wanted:
            print(f"  {sym}: 已是 enabled={cur}，跳过")
            continue
        r = http(host, "POST", "/api/trade/symbols", {"symbol": sym, "enabled": wanted})
        after = before.copy()
        if r.get("ok"):
            for s in r.get("symbols", []):
                after[s["symbol"]] = s
        got = after.get(sym, {}).get("enabled")
        msg = r.get("error") or ""
        print(f"  {sym}: enabled {cur} -> {got}  {'OK' if got == wanted else 'FAILED ' + str(msg)}")


if __name__ == "__main__":
    main()
