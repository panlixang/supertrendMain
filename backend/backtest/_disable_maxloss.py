# -*- coding: utf-8 -*-
"""关闭 43/47 两台服务器所有线上品种的极端保护止损(max_loss)。

普通档 + quick 档 exit_rules.max_loss_enabled -> False。
改后 GET 验证打印。
"""
import json
import urllib.request

HOSTS = ["http://43.108.10.84:5174", "http://47.84.106.154:5174"]
HDR = {"Content-Type": "application/json", "User-Agent": "supertrend-bt/1.0"}


def post(url, data):
    req = urllib.request.Request(url, data=json.dumps(data).encode(),
                                 headers=HDR, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def get(url):
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def process(host: str):
    print(f"\n######## {host} ########", flush=True)
    syms = get(host + "/api/trade/symbols")["symbols"]
    print(f"品种 {len(syms)} 个\n", flush=True)
    for sym in syms:
        er = sym.get("exit_rules") or {}
        erq = sym.get("exit_rules_quick") or {}
        print(f"== {sym['symbol']} | normal ml={er.get('max_loss_enabled')}/"
              f"{er.get('max_loss_pct')} | quick ml="
              f"{erq.get('max_loss_enabled')}/{erq.get('max_loss_pct')}",
              flush=True)
        body = {
            "symbol": sym["symbol"],
            "exit_rules": {"max_loss_enabled": False},
            "exit_rules_quick": {"max_loss_enabled": False},
        }
        r = post(host + "/api/trade/symbols", body)
        if not r.get("ok"):
            print(f"  POST 失败: {r.get('error')}", flush=True)
            continue
        s = next(x for x in r["symbols"] if x["symbol"] == sym["symbol"])
        er2 = s.get("exit_rules") or {}
        erq2 = s.get("exit_rules_quick") or {}
        print(f"  改后: normal ml={er2.get('max_loss_enabled')}"
              f"({er2.get('max_loss_pct')}) | quick ml="
              f"{erq2.get('max_loss_enabled')}({erq2.get('max_loss_pct')})",
              flush=True)


def main():
    for host in HOSTS:
        try:
            process(host)
        except Exception as e:
            print(f"\n######## {host} ######## ERROR: {e}", flush=True)


if __name__ == "__main__":
    main()
