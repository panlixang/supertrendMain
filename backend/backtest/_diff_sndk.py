# -*- coding: utf-8 -*-
import json
import urllib.request


def _get(base, sym):
    url = base.rstrip("/") + "/api/trade/symbols"
    d = json.loads(urllib.request.urlopen(url, timeout=25).read())
    return next((s for s in d["symbols"] if s["symbol"] == sym), None)


def main():
    sym = "SNDK-USDT-SWAP"
    a43 = _get("http://43.108.10.84:5174", sym)
    a47 = _get("http://47.84.106.154:5174", sym)
    keys = sorted(set(a43) | set(a47))
    diff = {}
    for k in keys:
        v43, v47 = a43.get(k, None), a47.get(k, None)
        if v43 != v47:
            diff[k] = {"v43": v43, "v47": v47}
    print("=== SNDK 差异字段 ===")
    print(json.dumps(diff, ensure_ascii=False, indent=2, default=str))
    print("\n=== 43 完整配置 ===")
    print(json.dumps(a43, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
