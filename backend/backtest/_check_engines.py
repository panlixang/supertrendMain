# -*- coding: utf-8 -*-
"""打印 43/47 两台服务器各品种当前的评分引擎与阈值配置。"""
import json
import urllib.request

HOSTS = ["http://43.108.10.84:5174", "http://47.84.106.154:5174"]
HDR = {"Content-Type": "application/json", "User-Agent": "supertrend-bt/1.0"}


def get(url):
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main():
    for host in HOSTS:
        print(f"\n######## {host} ########")
        syms = get(host + "/api/trade/symbols")["symbols"]
        for s in syms:
            print(f"\n== {s['symbol']} | enabled={s.get('enabled')} "
                  f"quick={s.get('quick_enabled')} | tfs={s.get('allow_tfs')}")
            print(f"   engine={s.get('score_engine')!r}  shadow="
                  f"{s.get('shadow_engine')!r}  dyn_thr={s.get('use_dynamic_threshold')} "
                  f"use_scoring={s.get('use_scoring')}")
            for k in ("scoring_full_threshold", "scoring_half_threshold",
                      "scoring_alert_threshold", "er_min", "er_weak_min",
                      "er_trend", "min_score"):
                print(f"   {k}={s.get(k)}", end="")
            print("")


if __name__ == "__main__":
    main()
