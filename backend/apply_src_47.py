# -*- coding: utf-8 -*-
"""
把 47 服务上品种的 Supertrend 数据源 src 从 hl2 改成 close（本地寻优值）。

为什么绕一圈：
  /api/trade/symbols 的 SymbolCfgIn 只暴露 periods / multiplier，改不了 src。
  但 /api/params 支持 src，而 state.params 是"视图委托"——指向 current_symbol
  那个 SymbolStore 的 params。所以：
      切图到目标品种 -> POST /api/params {"src":"close"} -> 落盘 + 重扫信号
  等于是改到了品种级参数，且 save_settings() 会写进 settings.json，重启不丢。

安全校验（防切错品种误改别人的参数）：
  切图后先 GET /api/params，比对 periods/multiplier 是否与该品种线上值一致，
  不一致就中止，绝不盲写。

用法：
  python apply_src_47.py             # dry-run，只预览
  python apply_src_47.py --apply     # 真正执行（改完自动切回原图表品种）
"""

import os
import sys
import json
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

HOST = os.environ.get("HOST47", "http://47.84.106.154:5174")
BACKEND = os.path.dirname(os.path.abspath(__file__))

# symbol -> 目标 src（来自本地寻优配置）
TARGET = {
    "MU-USDT-SWAP": os.path.join(BACKEND, "backtest", "MU", "mu_relax_opt_config.json"),
}
BACKUP_DIR = os.path.join(BACKEND, "_bt2026_data")


def http(method, path, payload=None, timeout=30):
    url = HOST + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    apply = "--apply" in sys.argv
    print(f"目标服务: {HOST}   模式: {'APPLY(真实写入)' if apply else 'DRY-RUN(只预览)'}")
    print("=" * 78)

    cur = http("GET", "/api/symbol")["symbol"]
    live = {s["symbol"]: s for s in http("GET", "/api/trade/symbols")["symbols"]}
    print(f"当前图表品种: {cur}\n")

    todo = []
    for sym, path in TARGET.items():
        if sym not in live:
            print(f"[跳过] {sym} 不在交易列表")
            continue
        with open(path, "r", encoding="utf-8") as f:
            want = json.load(f)
        cur_src = live[sym]["params"].get("src")
        want_src = want["params"].get("src")
        want_other = {k: want["params"].get(k) for k in
                      ("periods", "multiplier", "change_atr", "fast_len", "slow_len", "ma_type")}
        print(f"### {sym}")
        print(f"  params.src   线上 {cur_src}  ->  目标 {want_src}")
        for k, v in want_other.items():
            cv = live[sym]["params"].get(k)
            flag = "" if cv == v else "   <<< 线上不同但本次不改"
            print(f"  params.{k:<12} 线上 {cv} / 目标 {v}{flag}")
        if cur_src == want_src:
            print("  已一致，跳过")
            continue
        todo.append((sym, want_src, live[sym]["params"]))
        print()

    if not todo:
        print("无需改动。")
        return
    if not apply:
        print("=" * 78)
        print("DRY-RUN 结束，未写入。确认后加 --apply。")
        return

    os.makedirs(BACKUP_DIR, exist_ok=True)
    import time
    bpath = os.path.join(BACKUP_DIR, f"_rollback_47_src_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(bpath, "w", encoding="utf-8") as f:
        json.dump(live, f, ensure_ascii=False, indent=2)
    print(f"线上配置已备份 -> {bpath}\n")

    for sym, want_src, sym_params in todo:
        # 1) 切图到该品种
        r = http("POST", "/api/symbol", {"instId": sym})
        if not r.get("ok"):
            print(f"[{sym}] 切图失败: {r}")
            continue
        # 2) 安全校验：view.params 必须就是这个品种的（用 periods/multiplier 指纹确认）
        pv = http("GET", "/api/params")
        if (pv.get("periods") != sym_params.get("periods")
                or pv.get("multiplier") != sym_params.get("multiplier")):
            print(f"[{sym}] 中止！切图后 /api/params 指纹不匹配 "
                  f"(得到 {pv.get('periods')}x{pv.get('multiplier')}, "
                  f"期望 {sym_params.get('periods')}x{sym_params.get('multiplier')})，未做任何改动")
            continue
        # 3) 改 src
        r = http("POST", "/api/params", {"src": want_src})
        print(f"[{sym}] src 已改为 {r['params'].get('src')}（已落盘并重扫信号）")

    # 4) 切回原图表品种
    if cur != http("GET", "/api/symbol")["symbol"]:
        http("POST", "/api/symbol", {"instId": cur})
        print(f"\n已切回原图表品种: {cur}")

    print("\n" + "=" * 78)
    print("回读校验（GET /api/trade/symbols）：")
    live2 = {s["symbol"]: s for s in http("GET", "/api/trade/symbols")["symbols"]}
    ok = True
    for sym, path in TARGET.items():
        if sym not in live2:
            continue
        want = json.load(open(path, "r", encoding="utf-8"))
        got = live2[sym]["params"]
        bad = [f"{k}: {got.get(k)} != {want['params'].get(k)}"
               for k in ("periods", "multiplier", "src", "change_atr", "fast_len", "slow_len", "ma_type")
               if got.get(k) != want["params"].get(k)]
        print(f"  {sym}: {'OK 与回测配置完全一致' if not bad else '仍有差异 -> ' + '; '.join(bad)}")
        if bad:
            ok = False
    print("=" * 78)
    print("全部一致，线上口径已与回测对齐" if ok else "存在残留差异")


if __name__ == "__main__":
    main()
