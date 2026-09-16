# -*- coding: utf-8 -*-
"""开启 V2 品种的 Shadow Mode：主引擎 v2 + shadow_engine=v1（V1 对照实时采集）。

节点感知：仅对线上实际存在的 V2 品种下发 shadow_engine=v1，其余跳过；幂等可重跑。
shadow 仅在判单点额外重算并落盘 backend/logs/shadow_<SYM>.jsonl，绝不影响主流程
（shadow.py 全程 try/except no-op）。用于验证 V2 在实时是否仍挡掉 V1 会接的低质量单。

用法：
  python enable_v2_shadow.py
  BASE=http://47.84.106.154:5174 python enable_v2_shadow.py
"""
from __future__ import annotations
import json
import os
import sys
import urllib.request

LIVE = [
    "http://43.108.10.84:5174",
    "http://47.84.106.154:5174",
]
# 完整 instId（与 apply_v2_prod_config.py 一致）
TARGET = {
    "MU-USDT-SWAP": 40.0,
    "ETH-USDT-SWAP": 44.0,
    "SPCX-USDT-SWAP": 44.0,
}


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=25) as r:
        return json.loads(r.read())


def _post(base: str, payload: dict) -> dict:
    url = base.rstrip("/") + "/api/trade/symbols"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def main():
    base = os.environ.get("BASE", "").rstrip("/")
    bases = [base] if base else LIVE
    all_ok = True
    for b in bases:
        print(f"\n=== node {b} ===")
        try:
            d = _get(b.rstrip("/") + "/api/trade/symbols")
            present = {s.get("symbol") for s in d.get("symbols", [])}
        except Exception as e:  # noqa: BLE001
            print(f"  GET 失败 -> {e}")
            all_ok = False
            continue
        for sym in TARGET:
            if sym not in present:
                print(f"  {sym}: 不在该节点，跳过")
                continue
            try:
                resp = _post(b, {"symbol": sym, "shadow_engine": "v1"})
            except Exception as e:  # noqa: BLE001
                print(f"  {sym}: 请求失败 -> {e}")
                all_ok = False
                continue
            ok = resp.get("ok")
            if not ok:
                all_ok = False
            new_sh = ""
            for s in resp.get("symbols", []):
                if s.get("symbol") == sym:
                    new_sh = s.get("shadow_engine")
            print(f"  {sym}: shadow_engine -> {new_sh!r}  "
                  f"({'OK' if ok else 'FAIL'} {resp.get('error', '')})")
    print("\n全部成功" if all_ok else "\n存在失败", flush=True)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
