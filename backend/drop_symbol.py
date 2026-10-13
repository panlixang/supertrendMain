# -*- coding: utf-8 -*-
"""从指定服务删除一个交易品种（内置持仓检查，有持仓/无该品种都会拒绝）。

用法: python drop_symbol.py <43|47> <SYMBOL-USDT-SWAP>
"""
import os
import sys
import time
import json
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

BACKEND = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(BACKEND, "_bt2026_data")

HOSTS = {"43": "http://43.108.10.84:5174", "47": "http://47.84.106.154:5174"}


def http(host, method, path, payload=None, timeout=40):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(host + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    tag, sym = sys.argv[1], sys.argv[2].strip().upper()
    host = HOSTS[tag]

    d = http(host, "GET", "/api/trade/symbols")
    m = {s["symbol"]: s for s in d["symbols"]}
    print(f"服务 {tag} ({host}) 上限={d['max']} 品种数={len(m)}")

    s = m.get(sym)
    if s is None:
        print(f"[中止] {sym} 不在该服务的交易列表里")
        return
    pos = s.get("position")
    print(f"待删除: {sym}  enabled={s.get('enabled')}  margin={s.get('margin_usdt')}  position={pos}")
    if pos and (pos.get("qty") or 0) != 0:
        print(f"[中止] {sym} 当前有持仓 qty={pos.get('qty')}，平仓后再删")
        return

    os.makedirs(BACKUP_DIR, exist_ok=True)
    bp = os.path.join(BACKUP_DIR, f"_rollback_{tag}_presymbol_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(bp, "w", encoding="utf-8") as f:
        json.dump({"deleted": s, "all": m}, f, ensure_ascii=False, indent=2)
    print(f"[备份] 删除前配置 -> {bp}")

    r = http(host, "DELETE", f"/api/trade/symbols/{sym}")
    if not r.get("ok"):
        print(f"[失败] {r.get('error')}")
        return
    after = [x["symbol"].replace("-USDT-SWAP", "") for x in r.get("symbols", [])]
    print(f"[已删除] {sym}  剩余 {len(after)} 个: {after}")


if __name__ == "__main__":
    main()
