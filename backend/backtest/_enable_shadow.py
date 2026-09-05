# -*- coding: utf-8 -*-
"""开启 Shadow Mode（阶段3：双引擎对照采集）。

仅对 47（新版代码）生效。43 有意保留旧版纯 v1 作对照组，不升级也不开
shadow；该机 POST 会被静默忽略（无 shadow_engine 字段），保持不动即可。

规则：shadow_engine = 与当前主引擎相反的另一引擎，只记录不干预交易：
  主 v1（含空/None/trend_follow_v1） → shadow v2
  主 v2（quality_filter_v2）         → shadow v1
落地 backend/logs/shadow_<SYM>.jsonl，每行一条判单信号。

复查：GET 打印每品种 shadow_engine。
关闭：把 shadow_engine 置 "" 重跑。
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


def opposite(main: str) -> str:
    m = (main or "").strip().lower()
    if not m or m in ("v1", "trend_follow_v1"):
        return "v2"
    if m in ("v2", "quality_filter_v2"):
        return "v1"
    return None  # 未知引擎名 → 跳过


def process(host: str):
    print(f"\n######## {host} ########", flush=True)
    syms = get(host + "/api/trade/symbols")["symbols"]
    print(f"品种 {len(syms)} 个\n", flush=True)
    for sym in syms:
        main = sym.get("score_engine") or ""
        sh = opposite(main)
        tag = "(主 v1 → shadow v2)" if sh == "v2" else "(主 v2 → shadow v1)"
        print(f"== {sym['symbol']}  主引擎={main!r}  {tag}", flush=True)
        if sh is None:
            print(f"  跳过：未知主引擎 {main!r}", flush=True)
            continue
        body = {"symbol": sym["symbol"], "shadow_engine": sh}
        r = post(host + "/api/trade/symbols", body)
        if not r.get("ok"):
            print(f"  POST 失败: {r.get('error')}", flush=True)
            continue
        s = next(x for x in r["symbols"] if x["symbol"] == sym["symbol"])
        print(f"  改后: main={s.get('score_engine')!r} "
              f"shadow={s.get('shadow_engine')!r}", flush=True)


def main():
    for host in HOSTS:
        try:
            process(host)
        except Exception as e:
            print(f"\n######## {host} ######## ERROR: {e}", flush=True)


if __name__ == "__main__":
    main()
