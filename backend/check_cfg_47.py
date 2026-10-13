# -*- coding: utf-8 -*-
"""
全字段比对：47 服务线上品种配置  vs  本地回测配置。
排除运行态字段（last / position / history_loaded），其余一律比对。

用法: python check_cfg_47.py
"""

import os
import sys
import json
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

HOST = os.environ.get("HOST47", "http://47.84.106.154:5174")
BACKEND = os.path.dirname(os.path.abspath(__file__))

TARGET = {
    "NVDA-USDT-SWAP": os.path.join(BACKEND, "backtest", "nvda", "nvda_config.json"),
    "SNDK-USDT-SWAP": os.path.join(BACKEND, "backtest", "sndk", "sndk_config.json"),
}
SKIP_TOP = {"symbol", "last", "position", "history_loaded"}


def get(path):
    with urllib.request.urlopen(HOST + path, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def flat_diff(live, want, prefix=""):
    keys = sorted(set(live) | set(want))
    out = []
    for k in keys:
        if k in SKIP_TOP and not prefix:
            continue
        lv, wv = live.get(k, "<缺失>"), want.get(k, "<缺失>")
        if isinstance(lv, str) and k in ("score_engine", "shadow_engine", "ma30_tf",
                                         "src", "ma_type", "tp3_mode", "sl_mode",
                                         "sizing_mode", "margin_mode"):
            lv = lv.strip().lower()
            wv = (wv or "").strip().lower() if isinstance(wv, str) else wv
        if isinstance(lv, float) or isinstance(wv, float):
            try:
                if abs(float(lv) - float(wv)) < 1e-9:
                    continue
            except (TypeError, ValueError):
                pass
        if lv != wv:
            out.append((f"{prefix}{k}", lv, wv))
    return out


def main():
    live = {s["symbol"]: s for s in get("/api/trade/symbols")["symbols"]}
    total_diff = 0

    for sym, path in TARGET.items():
        if sym not in live:
            print(f"[缺失] {sym} 不在 47 服务交易列表")
            continue
        want = json.load(open(path, "r", encoding="utf-8"))
        L, W = live[sym], want

        d_top = flat_diff(L, W)
        d_par = flat_diff(L["params"], W.get("params", {}), "params.")
        d_ex = flat_diff(L["exit_rules"], W.get("exit_rules", {}), "exit_rules.")
        d_qk = flat_diff(L["exit_rules_quick"], W.get("exit_rules_quick", {}), "exit_rules_quick.")

        print("=" * 78)
        print(f"### {sym}")
        for title, d in (("品种主配置", d_top), ("指标 params", d_par),
                         ("出场 exit_rules(标准档)", d_ex), ("出场 exit_rules_quick(快档)", d_qk)):
            if not d:
                print(f"  [{title}] 全部一致")
                continue
            total_diff += len(d)
            print(f"  [{title}] {len(d)} 处不同：")
            for k, lv, wv in d:
                print(f"      {k:<34} 线上 {lv}   |  回测 {wv}")

    print("=" * 78)
    print("结论：" + ("完全对齐，线上 == 本地回测配置" if total_diff == 0
                     else f"仍有 {total_diff} 处不同（见上）"))


if __name__ == "__main__":
    main()
