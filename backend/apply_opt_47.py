# -*- coding: utf-8 -*-
"""
把本地最新寻优参数下发到 47 服务（47.84.106.154:5174）的品种挂单参数。

数据源（回测验证过的最优配置）：
  backend/backtest/nvda/nvda_config.json   -> NVDA-USDT-SWAP
  backend/backtest/sndk/sndk_config.json   -> SNDK-USDT-SWAP

用法：
  python apply_opt_47.py            # dry-run，只打印差异，不写入
  python apply_opt_47.py --apply    # 真正下发（会先备份线上配置再写，写完回读校验）

注意：
  1) 只下发"策略参数"：指标 periods/multiplier、评分闸门、ER 闸门、标准档出场规则。
     margin_usdt / leverage / enabled / allow_tfs / sizing 等仓位与开关项不动。
  2) API 不支持改 src / change_atr / fast_len / slow_len / ma_type，
     这些差异只会提示，不会下发，也不会报错。
  3) 47 页上 NVDA / SNDK 都是 enabled=True（真实下单），下发直接改变实盘行为。
"""

import os
import sys
import json
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

HOST = os.environ.get("HOST47", "http://47.84.106.154:5174")
BACKEND = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(BACKEND, "_bt2026_data")

TARGET = {
    "NVDA-USDT-SWAP": os.path.join(BACKEND, "backtest", "nvda", "nvda_config.json"),
    "SNDK-USDT-SWAP": os.path.join(BACKEND, "backtest", "sndk", "sndk_config.json"),
}

# 品种级可直接下发的字段
CFG_FIELDS = [
    "er_hide_below", "er_weak_min", "er_min", "er_trend",
    "use_scoring",
    "scoring_full_threshold", "scoring_half_threshold", "scoring_alert_threshold",
    "use_dynamic_threshold", "score_engine",
]
PARAM_FIELDS = ["periods", "multiplier"]
EXIT_FIELDS = [
    "enabled",
    "tp1_pct", "tp1_ratio", "tp2_pct", "tp2_ratio",
    "tp3_pct", "tp3_ratio", "tp3_mode",
    "move_sl_to_entry", "sl_mode", "sl_pct", "trail_with_st", "reverse_close",
    "max_loss_enabled", "max_loss_pct",
    "sl_buffer_atr", "sl_min_pct", "protect_profit_at", "protect_trail_pct",
]
# API 不支持，只提示
UNSUPPORTED = ["src", "change_atr", "fast_len", "slow_len", "ma_type"]
# 不动的仓位/开关项，只提示
FROZEN = ["enabled", "margin_usdt", "leverage", "allow_tfs", "sizing_mode", "equity_pct"]


def http(method, path, payload=None, timeout=30):
    url = HOST + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def load_live():
    return {s["symbol"]: s for s in http("GET", "/api/trade/symbols")["symbols"]}


def build_plan(sym, live, want):
    """返回 (payload, diffs, notes)"""
    payload = {"symbol": sym}
    diffs, notes = [], []

    for f in CFG_FIELDS:
        w = want.get(f)
        if w is None:
            continue
        cur = live.get(f)
        if isinstance(w, str):
            w = w.strip().lower()
        if cur != w:
            payload[f] = w
            diffs.append(("cfg." + f, cur, w))

    for f in PARAM_FIELDS:
        w = want["params"].get(f)
        if w is None:
            continue
        cur = live["params"].get(f)
        if cur != w:
            payload[f] = w
            diffs.append(("params." + f, cur, w))

    exit_patch = {}
    for f in EXIT_FIELDS:
        w = want["exit_rules"].get(f)
        if w is None:
            continue
        cur = live["exit_rules"].get(f)
        if cur != w:
            exit_patch[f] = w
            diffs.append(("exit." + f, cur, w))
    if exit_patch:
        payload["exit_rules"] = exit_patch

    for f in UNSUPPORTED:
        cur = live["params"].get(f)
        w = want["params"].get(f)
        if w is not None and cur != w:
            notes.append(f"[不可下发] params.{f}: 线上 {cur} -> 期望 {w} "
                         f"（API 不支持改该字段，线上将保持 {cur}）")

    for f in FROZEN:
        cur = live.get(f)
        w = want.get(f)
        if w is not None and cur != w:
            notes.append(f"[仓位/开关，不动] {f}: 线上 {cur} / 本地配置 {w}")

    qd = []
    for f in EXIT_FIELDS:
        w = want.get("exit_rules_quick", {}).get(f)
        if w is None:
            continue
        cur = live["exit_rules_quick"].get(f)
        if cur != w:
            qd.append((f, cur, w))
    if qd:
        notes.append("[快进快出档差异，未下发] " +
                     "; ".join(f"{k}: {c} -> {w}" for k, c, w in qd))

    payload["rescan"] = True
    return payload, diffs, notes


def main():
    apply = "--apply" in sys.argv
    print(f"目标服务: {HOST}   模式: {'APPLY(真实写入)' if apply else 'DRY-RUN(只预览)'}")
    print("=" * 78)

    live_all = load_live()
    plans = {}
    for sym, path in TARGET.items():
        if sym not in live_all:
            print(f"[跳过] {sym} 不在 47 服务的品种列表里")
            continue
        with open(path, "r", encoding="utf-8") as f:
            want = json.load(f)
        payload, diffs, notes = build_plan(sym, live_all[sym], want)
        plans[sym] = payload

        print(f"\n### {sym}")
        print(f"  线上持仓: {'有 (注意: 改出场规则会影响该仓位后续管理)' if live_all[sym].get('position') else '无'}")
        if not diffs:
            print("  无需改动，已与本地最优配置一致")
        else:
            print(f"  待下发 {len(diffs)} 项：")
            for k, c, w in diffs:
                print(f"    {k:<28} {c}  ->  {w}")
        for n in notes:
            print(f"  {n}")

    if not apply:
        print("\n" + "=" * 78)
        print("DRY-RUN 结束，未写入任何改动。确认无误后加 --apply 执行。")
        return

    ts = time.strftime("%Y%m%d_%H%M%S")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    bpath = os.path.join(BACKUP_DIR, f"_rollback_47_{ts}.json")
    with open(bpath, "w", encoding="utf-8") as f:
        json.dump(live_all, f, ensure_ascii=False, indent=2)
    print(f"\n线上配置已备份 -> {bpath}")

    for sym, payload in plans.items():
        if len(payload) <= 2:          # 只有 symbol + rescan
            print(f"\n[{sym}] 无差异，跳过下发")
            continue
        r = http("POST", "/api/trade/symbols", payload)
        if not r.get("ok"):
            print(f"\n[{sym}] 下发失败: {r.get('error')}")
            continue
        print(f"\n[{sym}] 下发成功")

    print("\n" + "=" * 78)
    print("回读校验：")
    live2 = load_live()
    allok = True
    for sym, payload in plans.items():
        if sym not in live2:
            continue
        cur, want = live2[sym], json.load(open(TARGET[sym], "r", encoding="utf-8"))
        bad = []
        for f in CFG_FIELDS:
            w = want.get(f)
            if w is None:
                continue
            if isinstance(w, str):
                w = w.strip().lower()
            if cur.get(f) != w:
                bad.append(f"cfg.{f}: {cur.get(f)} != {w}")
        for f in PARAM_FIELDS:
            if cur["params"].get(f) != want["params"].get(f):
                bad.append(f"params.{f}: {cur['params'].get(f)} != {want['params'].get(f)}")
        for f in EXIT_FIELDS:
            w = want["exit_rules"].get(f)
            if w is None:
                continue
            if cur["exit_rules"].get(f) != w:
                bad.append(f"exit.{f}: {cur['exit_rules'].get(f)} != {w}")
        print(f"  {sym}: {'OK 全部一致' if not bad else '不一致 -> ' + '; '.join(bad)}")
        if bad:
            allok = False
    print("=" * 78)
    print("全部校验通过" if allok else "存在不一致，请检查上面输出")


if __name__ == "__main__":
    main()
