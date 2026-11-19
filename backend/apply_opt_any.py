# -*- coding: utf-8 -*-
"""
把本地寻优最优参数下发到 47 / 43 服务的品种挂单参数。

当前目标:
  47  SKHYNIX-USDT-SWAP  <- backtest/skhynix/skhynix_live_config_opt_config.json
  43  ETH-USDT-SWAP      <- backtest/eth/eth_live_config_opt_config.json

用法:
  python apply_opt_any.py            # dry-run，只打印差异，不写入
  python apply_opt_any.py --apply    # 真正下发（会先备份线上配置，写完回读校验）

注意:
  1) 只下发"策略参数"：指标 periods/multiplier、评分闸门、ER 闸门、标准档出场规则。
     margin_usdt / leverage / enabled / allow_tfs / sizing 等仓位与开关项不动。
  2) API 不支持改 src / change_atr / fast_len / slow_len / ma_type，
     这些差异只会提示，不会下发 —— 若 src 不同，线上实际口径会与回测不一致。
  3) 两个品种线上都是 enabled=True（真实下单），下发直接改变实盘行为。
"""

import os
import sys
import json
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

BACKEND = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(BACKEND, "_bt2026_data")

HOSTS = {
    "47": os.environ.get("HOST47", "http://47.84.106.154:5174"),
    "43": os.environ.get("HOST43", "http://43.108.10.84:5174"),
}

# (服务器, 线上 symbol, 本地寻优最优 config 路径)
TARGETS = [
    ("47", "MU-USDT-SWAP",
     os.path.join(BACKEND, "backtest", "MU", "mu_relax_opt_config.json")),
]

CFG_FIELDS = [
    "er_hide_below", "er_weak_min", "er_min", "er_trend",
    "use_scoring",
    "scoring_full_threshold", "scoring_half_threshold", "scoring_alert_threshold",
    "use_dynamic_threshold", "score_engine",
    # 组合过滤器（四大闸门）：SymbolCfgIn 支持，勿再漏
    "atr_filter_enabled", "atr_vol_min",
    "range_filter_enabled", "range_size_max", "range_touches_min",
    "mtf_filter_enabled", "mtf_consistency_min", "mtf_flip_max",
    "adx_filter_enabled", "adx_min", "adx_period",
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
UNSUPPORTED = ["src", "change_atr", "fast_len", "slow_len", "ma_type"]
FROZEN = ["enabled", "margin_usdt", "leverage", "allow_tfs", "sizing_mode", "equity_pct"]


def http(host, method, path, payload=None, timeout=30):
    url = host + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def load_live(host):
    return {s["symbol"]: s for s in http(host, "GET", "/api/trade/symbols")["symbols"]}


def build_plan(sym, live, want):
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
            notes.append(f"[仓位/开关，不动] {f}: 线上 {cur} / 本地 {w}")

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
    print(f"模式: {'APPLY(真实写入)' if apply else 'DRY-RUN(只预览)'}")
    print("=" * 80)

    os.makedirs(BACKUP_DIR, exist_ok=True)
    live_cache, plans = {}, []

    for tag, sym, path in TARGETS:
        host = HOSTS[tag]
        if tag not in live_cache:
            live_cache[tag] = load_live(host)
        live_all = live_cache[tag]
        print(f"\n### 服务 {tag} ({host})  {sym}")
        if sym not in live_all:
            print("  [跳过] 线上没有该品种")
            continue
        with open(path, "r", encoding="utf-8") as f:
            want = json.load(f)
        payload, diffs, notes = build_plan(sym, live_all[sym], want)
        plans.append((tag, host, sym, path, payload))
        print(f"  线上持仓: {'有（改出场规则会影响该仓位后续管理）' if live_all[sym].get('position') else '无'}")
        if not diffs:
            print("  无需改动，已与本地寻优配置一致")
        else:
            print(f"  待下发 {len(diffs)} 项：")
            for k, c, w in diffs:
                print(f"    {k:<26} {c}  ->  {w}")
        for n in notes:
            print(f"  {n}")

    if not apply:
        print("\n" + "=" * 80)
        print("DRY-RUN 结束，未写入任何改动。确认无误后加 --apply 执行。")
        return

    ts = time.strftime("%Y%m%d_%H%M%S")
    for tag in live_cache:
        bpath = os.path.join(BACKUP_DIR, f"_rollback_{tag}_{ts}.json")
        with open(bpath, "w", encoding="utf-8") as f:
            json.dump(live_cache[tag], f, ensure_ascii=False, indent=2)
        print(f"\n[备份] 服务 {tag} 线上配置 -> {bpath}")

    for tag, host, sym, path, payload in plans:
        if len(payload) <= 2:
            print(f"\n[{tag} {sym}] 无差异，跳过下发")
            continue
        r = http(host, "POST", "/api/trade/symbols", payload)
        if not r.get("ok"):
            print(f"\n[{tag} {sym}] 下发失败: {r.get('error')}")
            continue
        print(f"\n[{tag} {sym}] 下发成功")

    print("\n" + "=" * 80)
    print("回读校验：")
    for tag, host, sym, path, payload in plans:
        live2 = load_live(host)
        if sym not in live2:
            continue
        cur = live2[sym]
        want = json.load(open(path, "r", encoding="utf-8"))
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
        src_cur = cur["params"].get("src")
        src_want = want["params"].get("src")
        print(f"  {tag} {sym}: {'OK 全部一致' if not bad else '不一致 -> ' + '; '.join(bad)}")
        if src_cur != src_want:
            print(f"      [提醒] src 仍为 {src_cur}（期望 {src_want}），API 不支持改，线上口径与回测不同")
    print("=" * 80)


if __name__ == "__main__":
    main()
