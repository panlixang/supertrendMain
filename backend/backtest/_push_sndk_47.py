# -*- coding: utf-8 -*-
"""把 43 的 SNDK 配置覆盖到 47（只推送与 47 不同的业务字段）。"""
import json
import sys
import urllib.request

SRC = "http://43.108.10.84:5174"
DST = "http://47.84.106.154:5174"
SYM = "SNDK-USDT-SWAP"


def _get(base, sym):
    d = json.loads(urllib.request.urlopen(base + "/api/trade/symbols", timeout=25).read())
    return next(s for s in d["symbols"] if s["symbol"] == sym)


def _post(base, payload):
    req = urllib.request.Request(
        base + "/api/trade/symbols",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def main():
    src = _get(SRC, SYM)
    payload = {
        "symbol": SYM,
        "allow_tfs": src["allow_tfs"],
        "margin_usdt": src["margin_usdt"],
        "exit_rules": src["exit_rules"],
        "exit_rules_quick": src["exit_rules_quick"],
    }
    print("推送 payload:")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    resp = _post(DST, payload)
    print("\n推送响应:", resp)

    if not resp.get("ok"):
        print("FAIL，停止校验", file=sys.stderr)
        sys.exit(1)

    dst = _get(DST, SYM)
    diff = {k: {"v43": src.get(k), "v47": dst.get(k)}
            for k in set(src) | set(dst) if src.get(k) != dst.get(k)}
    diff.pop("last", None)  # 价格字段，运行时更新，忽略
    if diff:
        print("\n仍有差异（非 last）：")
        print(json.dumps(diff, ensure_ascii=False, indent=2, default=str))
        sys.exit(1)
    print("\n校验通过：47 SNDK 配置现已与 43 完全一致（除 last 价格字段）。")


if __name__ == "__main__":
    main()
