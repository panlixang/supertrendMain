# -*- coding: utf-8 -*-
"""把 V2 闸门生产配置写入线上 /api/trade/symbols（幂等，可重跑，节点感知）。

目标（见 V2_GATE_PROD_CONFIG.md）：
  MU/ETH/SPCX 三个品种设置
    use_scoring=true, score_engine="v2",
    scoring_full_threshold = scoring_half_threshold = scoring_alert_threshold = 40/44/44
  其余字段（含 use_dynamic_threshold / ER / 过滤器 / 止盈止损）保持现状不动。

节点感知：先 GET 该节点已配置品种，只对“确实存在的目标品种”下发，
避免把未配置品种当成新增撞 MAX_SYMBOLS 上限，也避免未知品种报错。

用法：
  python apply_v2_prod_config.py
  BASE=http://47.84.106.154:5174 python apply_v2_prod_config.py
"""
from __future__ import annotations
import json
import os
import sys
import urllib.request

# 与 _v2_threshold_scan.LIVE 一致的生产节点
LIVE = [
    "http://43.108.10.84:5174",
    "http://47.84.106.154:5174",
]

# 目标品种（完整 instId）-> 评分闸门阈值（full=half=alert）
TARGET = {
    "MU-USDT-SWAP": 40.0,
    "ETH-USDT-SWAP": 44.0,
    "SPCX-USDT-SWAP": 44.0,
}

# 是否把 score_engine 钉成 "v2"。若品种已是 v2 只想改阈值，可设 False（仍会发送阈值）。
SET_ENGINE = True


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
            print(f"  GET symbols 失败 -> {e}")
            all_ok = False
            continue
        for sym, thr in TARGET.items():
            if sym not in present:
                print(f"  {sym}: 不在该节点，跳过")
                continue
            payload = {
                "symbol": sym,
                "use_scoring": True,
                "scoring_full_threshold": thr,
                "scoring_half_threshold": thr,
                "scoring_alert_threshold": thr,
            }
            if SET_ENGINE:
                payload["score_engine"] = "v2"
            try:
                resp = _post(b, payload)
            except Exception as e:  # noqa: BLE001
                print(f"  {sym}: 请求失败 -> {e}")
                all_ok = False
                continue
            ok = resp.get("ok")
            if not ok:
                all_ok = False
            print(f"  {sym} (thr={thr:g}) -> {'OK' if ok else 'FAIL'}  {resp}")
    print("\n全部成功" if all_ok else "\n存在失败，请检查上面输出", flush=True)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
