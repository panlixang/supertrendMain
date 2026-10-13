# -*- coding: utf-8 -*-
"""
把 CL 的寻优最优参数下发到 47 服务（47.84.106.154:5174）线上。

数据源（回测验证过的寻优最优）：
  backend/backtest/cl/cl_config_opt_config.json   -> CL-USDT-SWAP

用法：
  python apply_cl_47.py            # dry-run，只预览差异，不写入
  python apply_cl_47.py --apply    # 真正下发（备份 -> 下发 -> src 绕过 -> 回读校验）

处理两类字段：
  A. 品种级可直接下发（/api/trade/symbols）：
     periods/multiplier、ER 闸门、评分闸门、exit_rules 全套。
  B. src（API 不支持）：走「切图到 CL + POST /api/params {"src"}」绕过，
     state.params 是视图委托指向 current_symbol 的 store.params，
     所以等于是改到品种级，且 save_settings 落盘重启不丢。
     绕过前有 periods/multiplier 指纹安全校验，防切错品种误改别人参数。

注意：
  1) margin_usdt / leverage / enabled / allow_tfs / sizing 等仓位与开关项不动。
  2) change_atr / fast_len / slow_len / ma_type 不可下发，只提示。
  3) CL 线上 enabled=true（真实下单），下发直接改变实盘行为。
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
    "CL-USDT-SWAP": os.path.join(BACKEND, "backtest", "cl", "cl_config_opt_config.json"),
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
# API 不支持（其中 src 走绕过）
UNSUPPORTED = ["change_atr", "fast_len", "slow_len", "ma_type"]
# 不动的仓位/开关项
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
                         f"（API 不支持，线上将保持 {cur}）")

    cur_src = live["params"].get("src")
    want_src = want["params"].get("src")
    if want_src and cur_src != want_src:
        notes.append(f"[src 走绕过] params.src: 线上 {cur_src} -> 期望 {want_src} "
                     f"（/api/trade/symbols 改不了，用切图 + /api/params 绕过）")

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


def apply_src(sym, want_src, sym_params):
    """切图到 sym -> 指纹校验 -> POST /api/params {src} -> 切回原品种。"""
    cur_chart = http("GET", "/api/symbol")["symbol"]
    r = http("POST", "/api/symbol", {"instId": sym})
    if not r.get("ok"):
        print(f"[{sym}] 切图失败: {r}")
        return False
    pv = http("GET", "/api/params")
    if (pv.get("periods") != sym_params.get("periods")
            or pv.get("multiplier") != sym_params.get("multiplier")):
        print(f"[{sym}] 中止！切图后 /api/params 指纹不匹配 "
              f"(得到 {pv.get('periods')}x{pv.get('multiplier')}, "
              f"期望 {sym_params.get('periods')}x{sym_params.get('multiplier')})，未改 src")
        http("POST", "/api/symbol", {"instId": cur_chart})
        return False
    r = http("POST", "/api/params", {"src": want_src})
    print(f"[{sym}] src 已改为 {r['params'].get('src')}（已落盘并重扫信号）")
    if cur_chart != http("GET", "/api/symbol")["symbol"]:
        http("POST", "/api/symbol", {"instId": cur_chart})
        print(f"  已切回原图表品种: {cur_chart}")
    return True


def main():
    do_apply = "--apply" in sys.argv
    print(f"目标服务: {HOST}   模式: {'APPLY(真实写入)' if do_apply else 'DRY-RUN(只预览)'}")
    print("=" * 78)

    live_all = load_live()
    plans = {}
    for sym, path in TARGET.items():
        if sym not in live_all:
            print(f"[跳过] {sym} 不在 47 服务的品种列表里")
            print(f"  线上品种: {sorted(live_all)}")
            continue
        with open(path, "r", encoding="utf-8") as f:
            want = json.load(f)
        payload, diffs, notes = build_plan(sym, live_all[sym], want)
        plans[sym] = (payload, want, live_all[sym])

        print(f"\n### {sym}")
        print(f"  enabled(线上)={live_all[sym].get('enabled')}  "
              f"margin={live_all[sym].get('margin_usdt')}U  "
              f"leverage={live_all[sym].get('leverage')}x")
        print(f"  线上持仓: {'有 (改出场规则会影响该仓位后续管理)' if live_all[sym].get('position') else '无'}")
        print(f"  线上 ST: {live_all[sym]['params'].get('periods')}x"
              f"{live_all[sym]['params'].get('multiplier')} src="
              f"{live_all[sym]['params'].get('src')}")
        print(f"  目标 ST: {want['params'].get('periods')}x"
              f"{want['params'].get('multiplier')} src={want['params'].get('src')}")
        if not diffs:
            print("  可下发字段：无需改动，已一致")
        else:
            print(f"  待下发 {len(diffs)} 项：")
            for k, c, w in diffs:
                print(f"    {k:<28} {c}  ->  {w}")
        for n in notes:
            print(f"  {n}")

    if not do_apply:
        print("\n" + "=" * 78)
        print("DRY-RUN 结束，未写入任何改动。确认无误后加 --apply 执行。")
        return

    ts = time.strftime("%Y%m%d_%H%M%S")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    bpath = os.path.join(BACKUP_DIR, f"_rollback_47_cl_{ts}.json")
    with open(bpath, "w", encoding="utf-8") as f:
        json.dump(live_all, f, ensure_ascii=False, indent=2)
    print(f"\n线上配置已备份 -> {bpath}")

    for sym, (payload, want, before) in plans.items():
        if len(payload) <= 2:
            print(f"\n[{sym}] 可下发字段无差异，跳过 POST")
        else:
            r = http("POST", "/api/trade/symbols", payload)
            if not r.get("ok"):
                print(f"\n[{sym}] 下发失败: {r.get('error')}")
                continue
            print(f"\n[{sym}] 可下发字段下发成功")
        # src 绕过（用下发后的 periods/multiplier 做指纹）
        live_now = load_live()[sym]
        want_src = want["params"].get("src")
        if want_src and live_now["params"].get("src") != want_src:
            apply_src(sym, want_src, live_now["params"])

    print("\n" + "=" * 78)
    print("回读校验：")
    live2 = load_live()
    allok = True
    for sym, (payload, want, before) in plans.items():
        if sym not in live2:
            continue
        cur = live2[sym]
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
        if cur["params"].get("src") != want["params"].get("src"):
            bad.append(f"params.src: {cur['params'].get('src')} != {want['params'].get('src')}")
        print(f"  {sym}: {'OK 全部一致' if not bad else '不一致 -> ' + '; '.join(bad)}")
        if bad:
            allok = False
    print("=" * 78)
    print("全部校验通过，线上口径已与回测对齐" if allok else "存在不一致，请检查上面输出")


if __name__ == "__main__":
    main()
