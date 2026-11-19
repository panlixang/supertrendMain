# -*- coding: utf-8 -*-
"""
把 47 服务的品种及其全部参数配置复制到 43 服务。

覆盖范围:
  - SymbolCfgIn 支持的所有字段（仓位、ER、评分闸门、四大过滤器、MA30、quick 等）
  - exit_rules / exit_rules_quick 出场规则
  - params 中的 periods / multiplier
  - params 中的 src / change_atr / fast_len / slow_len / ma_type
    （这些 /api/trade/symbols 改不了，需要 切图到该品种 → POST /api/params → 改完切回）

安全:
  1) 开始前校验 43 的剩余配额，配额不足直接中止，避免加到一半被上限拦断
  2) 每步改 params 前用 periods/multiplier 指纹确认切图到了目标品种，不匹配则跳过
  3) 操作前备份 43 线上配置；结束后切回原图表品种并回读逐字段校验

用法:
  python copy_47_to_43.py            # dry-run，只预览
  python copy_47_to_43.py --apply    # 真正执行
"""

import os
import sys
import json
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

BACKEND = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(BACKEND, "_bt2026_data")

HOST47 = os.environ.get("HOST47", "http://47.84.106.154:5174")
HOST43 = os.environ.get("HOST43", "http://43.108.10.84:5174")

# SymbolCfgIn 支持的顶层字段（symbol / exit_rules* / rescan 单独处理）
SYM_FIELDS = [
    "enabled", "margin_usdt", "leverage", "allow_tfs", "sizing_mode", "equity_pct",
    "er_hide_below", "er_weak_min", "er_min", "er_trend",
    "quick_enabled", "allow_grades", "min_score", "cooldown_sec",
    "atr_filter_enabled", "atr_vol_min",
    "range_filter_enabled", "range_size_max", "range_touches_min",
    "mtf_filter_enabled", "mtf_consistency_min", "mtf_flip_max",
    "adx_filter_enabled", "adx_min", "adx_period",
    "use_scoring", "scoring_full_threshold", "scoring_half_threshold",
    "scoring_alert_threshold", "use_dynamic_threshold",
    "score_engine", "shadow_engine",
    "ma30_dir_enabled", "ma30_tf", "ma30_period",
]
# 只能走 /api/params 的指标字段
PARAM_FIELDS = ["src", "change_atr", "fast_len", "slow_len", "ma_type"]
EXIT_FIELDS = [
    "enabled",
    "tp1_pct", "tp1_ratio", "tp2_pct", "tp2_ratio",
    "tp3_pct", "tp3_ratio", "tp3_mode",
    "move_sl_to_entry", "sl_mode", "sl_pct", "trail_with_st", "reverse_close",
    "max_loss_enabled", "max_loss_pct",
    "sl_buffer_atr", "sl_min_pct", "protect_profit_at", "protect_trail_pct",
]


def http(host, method, path, payload=None, timeout=40):
    url = host + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def load_map(host):
    d = http(host, "GET", "/api/trade/symbols")
    return d["max"], {s["symbol"]: s for s in d["symbols"]}


def build_payload(src_sym, s):
    p = {"symbol": src_sym}
    for f in SYM_FIELDS:
        v = s.get(f)
        if v is not None:
            p[f] = v
    p["periods"] = s["params"]["periods"]
    p["multiplier"] = s["params"]["multiplier"]
    for key in ("exit_rules", "exit_rules_quick"):
        d = s.get(key) or {}
        sub = {k: d[k] for k in EXIT_FIELDS if k in d and d[k] is not None}
        if sub:
            p[key] = sub
    p["rescan"] = True
    return p


def main():
    apply = "--apply" in sys.argv
    print(f"模式: {'APPLY(真实写入)' if apply else 'DRY-RUN(只预览)'}")
    print(f"源 {HOST47}  ->  目标 {HOST43}")
    print("=" * 96)

    max47, m47 = load_map(HOST47)
    max43, m43 = load_map(HOST43)
    print(f"47: {len(m47)} 个品种 (上限 {max47})")
    print(f"43: {len(m43)} 个品种 (上限 {max43}) -> {[k.replace('-USDT-SWAP','') for k in m43]}")

    todo = [sym for sym in m47 if sym not in m43]
    print(f"\n待复制到 43 的品种 ({len(todo)}): "
          f"{[s.replace('-USDT-SWAP','') for s in todo]}")

    need = len(m43) + len(todo)
    if need > max43:
        print(f"\n[配额不足] 43 上限 {max43}，复制后需 {need} 个。"
              f"\n  -> 请先把 43 的 state.py 里 MAX_SYMBOLS 改为 ≥{need} 并重启该服务，再执行。"
              f"\n  -> 或减少源品种数量。为避免加到一半被截断，本次不执行。")
        return

    for sym in todo:
        s = m47[sym]
        p = s["params"]
        print(f"\n### {sym.replace('-USDT-SWAP','')}")
        print(f"  ST {p['periods']}x{p['multiplier']} {p['src']} | "
              f"gate {s.get('scoring_full_threshold')}/{s.get('scoring_alert_threshold')} | "
              f"ER {s.get('er_trend')}/{s.get('er_min')} | "
              f"margin {s.get('margin_usdt')}U {s.get('leverage')}x | enabled={s.get('enabled')}")
        print(f"  filters: atr={s.get('atr_filter_enabled')} range={s.get('range_filter_enabled')}"
              f"({s.get('range_size_max')}) mtf={s.get('mtf_filter_enabled')} adx={s.get('adx_filter_enabled')}")

    if not apply:
        print("\n" + "=" * 96)
        print("DRY-RUN 结束，未写入任何改动。确认后加 --apply 执行。")
        return

    os.makedirs(BACKUP_DIR, exist_ok=True)
    bpath = os.path.join(BACKUP_DIR, f"_rollback_43_copy_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(bpath, "w", encoding="utf-8") as f:
        json.dump(m43, f, ensure_ascii=False, indent=2)
    print(f"\n[备份] 43 线上配置 -> {bpath}")

    # 1) 新建品种 + 写入 SymbolCfgIn 支持的参数
    ok_syms = []
    for sym in todo:
        payload = build_payload(sym, m47[sym])
        r = http(HOST43, "POST", "/api/trade/symbols", payload)
        if not r.get("ok"):
            print(f"  [{sym}] 创建失败: {r.get('error')}")
            continue
        print(f"  [{sym}] 已创建并写入参数")
        ok_syms.append(sym)

    # 2) 修正只能走 /api/params 的字段（src 等）
    need_param = []
    for sym in ok_syms:
        want = m47[sym]["params"]
        cur = http(HOST43, "GET", "/api/trade/symbols")
        got = {s["symbol"]: s for s in cur["symbols"]}.get(sym, {}).get("params", {})
        diff = {k: want[k] for k in PARAM_FIELDS if k in want and got.get(k) != want[k]}
        if diff:
            need_param.append((sym, diff, want))

    if need_param:
        orig = http(HOST43, "GET", "/api/symbol")["symbol"]
        print(f"\n[指标参数修正] 需改写 {len(need_param)} 个品种的 {sorted({k for _, d, _ in need_param for k in d})}")
        for sym, diff, want in need_param:
            r = http(HOST43, "POST", "/api/symbol", {"instId": sym})
            if not r.get("ok"):
                print(f"  [{sym}] 切图失败，跳过")
                continue
            pv = http(HOST43, "GET", "/api/params")
            if (pv.get("periods") != want.get("periods")
                    or pv.get("multiplier") != want.get("multiplier")):
                print(f"  [{sym}] 指纹不匹配(得到 {pv.get('periods')}x{pv.get('multiplier')}，"
                      f"期望 {want.get('periods')}x{want.get('multiplier')})，跳过未改")
                continue
            http(HOST43, "POST", "/api/params", diff)
            print(f"  [{sym}] 已改写 {diff}")
        cur_sym = http(HOST43, "GET", "/api/symbol")["symbol"]
        if cur_sym != orig:
            http(HOST43, "POST", "/api/symbol", {"instId": orig})
            print(f"  已切回原图表品种: {orig}")

    # 3) 回读校验
    print("\n" + "=" * 96)
    print("回读校验（逐字段比对 47 源配置）：")
    _, after = load_map(HOST43)
    for sym in ok_syms:
        if sym not in after:
            print(f"  {sym}: 未创建成功")
            continue
        bad = []
        src47, dst = m47[sym], after[sym]
        for f in SYM_FIELDS:
            if f in src47 and src47[f] is not None and dst.get(f) != src47[f]:
                bad.append(f"{f}: {dst.get(f)} != {src47[f]}")
        for f in ("periods", "multiplier") + tuple(PARAM_FIELDS):
            if dst["params"].get(f) != src47["params"].get(f):
                bad.append(f"params.{f}: {dst['params'].get(f)} != {src47['params'].get(f)}")
        for key in ("exit_rules", "exit_rules_quick"):
            for f in EXIT_FIELDS:
                w = (src47.get(key) or {}).get(f)
                if w is not None and (dst.get(key) or {}).get(f) != w:
                    bad.append(f"{key}.{f}: {(dst.get(key) or {}).get(f)} != {w}")
        print(f"  {sym.replace('-USDT-SWAP',''):<9} {'OK 与 47 完全一致' if not bad else '差异 -> ' + '; '.join(bad)}")
    print("=" * 96)
    _, fin = load_map(HOST43)
    print(f"43 现有品种 {len(fin)} 个: {[k.replace('-USDT-SWAP','') for k in fin]}")


if __name__ == "__main__":
    main()
