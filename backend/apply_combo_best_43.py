# -*- coding: utf-8 -*-
"""把 INTC / AMD 的「2026 组合寻优最优解」下发到 43 页(43.108.10.84)线上配置。

来源（backend 下 README 见脚本尾部）
--------------------------------------------------------------------------------
寻优脚本：bt_home_intc_combo.py --sym INTC / --sym AMD
          (2026-02-26~10-11 / 2026-03-11~10-11，1h，fixed 100U×1x，v1 口径)
选的是「跨切点(0.70 / 0.60)都稳」的那组，不是 IS 冠军那组。

  INTC : ST 18×3.0 / 评分阈值 30 / tp1=3.0%(50%) / sl=4.0% / 保本 / 不跟 ST
         IS +49.6  OOS +22.6(0.7切点) +30.2(0.6切点)  全样本 +72.1
  AMD  : ST 15×4.0 / 评分阈值 30 / tp1=3.0%(50%) / sl=2.0% / 保本 / 不跟 ST
         IS +28.5  OOS +40.9(0.7切点) +39.9(0.6切点)  全样本 +69.4

口径要点（必须与回测一致，否则实盘≠回测）
--------------------------------------------------------------------------------
· score_engine 必须是 ""(=v1) —— 寻优跑的就是 v1 阶梯引擎
· use_scoring 必须 True，三档阈值统一 = 评分阈值(30)
· ST 的 src=hl2 / change_atr=True(RMA) 与线上现状一致，API 不支持改 → 不动
· tp2 / tp3 关闭：线上 clamp 上限是 100/0，等价于回测里的 999/0（永不触发）
  ⚠ tp3_pct 绝不能设 0：position_enhanced 里 tp3_pct<=0 会走「反向信号平仓」模式，
    导致所有价格类止盈止损失效
· sl_mode=pct + sl_pct + move_sl_to_entry(保本) + trail_with_st=False
· 其余字段（ER 阈值 / range·mtf·adx 过滤器 / min_score / grades / margin /
  leverage / allow_tfs / enabled）一律不动，保持线上原样

用法
--------------------------------------------------------------------------------
    python apply_combo_best_43.py             # dry-run：只打印 diff（默认）
    python apply_combo_best_43.py --apply     # 真正下发（会先备份旧配置）
    python apply_combo_best_43.py --apply --rescan   # 下发并强制重扫历史信号
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request

HOST = os.environ.get("BASE", "http://43.108.10.84:5174").rstrip("/")
HERE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "supertrend-apply/1.0", "Content-Type": "application/json"}

# ══════════════════════════════════════════════════════════════════
# 目标参数
# ══════════════════════════════════════════════════════════════════
TARGET = {
    "INTC-USDT-SWAP": {
        "periods": 18, "multiplier": 3.0,
        "score_engine": "",            # v1 阶梯引擎（线上原为 'v2'）
        "threshold": 30.0,
        "exit": dict(tp1_pct=3.0, sl_pct=4.0),
        "note": "IS +49.6 / OOS +22.6~+30.2 / 全样本 +72.1",
    },
    "AMD-USDT-SWAP": {
        "periods": 15, "multiplier": 4.0,
        "score_engine": "",            # 线上已经是 v1
        "threshold": 30.0,
        "exit": dict(tp1_pct=3.0, sl_pct=2.0),
        "note": "IS +28.5 / OOS +40.9~+40.0 / 全样本 +69.4",
    },
}

# 统一的 v1 出场口径（回测 build_sym 里写死的那套）
BASE_EXIT = dict(
    enabled=True,
    tp1_ratio=50.0,
    tp2_pct=100.0, tp2_ratio=0.0,              # 二档关闭
    tp3_pct=100.0, tp3_ratio=0.0, tp3_mode="pct",  # 三档关闭（不能用 0）
    move_sl_to_entry=True,                      # 保本
    sl_mode="pct",
    trail_with_st=False,                        # 不跟 ST
    protect_profit_at=1.5, protect_trail_pct=0.8,
    sl_buffer_atr=0.5, sl_min_pct=1.2,
    max_loss_enabled=False,
)


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def _post(payload: dict) -> dict:
    req = urllib.request.Request(
        HOST + "/api/trade/symbols", data=json.dumps(payload).encode("utf-8"),
        headers=UA, method="POST")
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def build_payload(cur: dict, tgt: dict, rescan: bool) -> tuple[dict, list]:
    """返回 (payload, diff列表)。diff = [(字段, 现值, 目标值)]"""
    payload = {"symbol": cur["symbol"]}
    diff = []

    def chg(key, val, shown=None):
        """顶层字段"""
        if val is not None and _norm(cur.get(key)) != _norm(val):
            diff.append((key, cur.get(key), val))
            payload[key] = val

    def chg_exit(key, val):
        er = cur.get("exit_rules") or {}
        if val is not None and _norm(er.get(key)) != _norm(val):
            diff.append((f"exit_rules.{key}", er.get(key), val))
            payload.setdefault("exit_rules", {})[key] = val

    # ── ST 参数 ──
    p = cur.get("params") or {}
    if p.get("periods") != tgt["periods"]:
        diff.append(("params.periods", p.get("periods"), tgt["periods"]))
        payload["periods"] = tgt["periods"]
    if _norm(p.get("multiplier")) != _norm(tgt["multiplier"]):
        diff.append(("params.multiplier", p.get("multiplier"), tgt["multiplier"]))
        payload["multiplier"] = tgt["multiplier"]

    # ── 评分闸门 ──
    chg("use_scoring", True)
    chg("score_engine", tgt["score_engine"])
    th = tgt["threshold"]
    chg("scoring_full_threshold", th)
    chg("scoring_half_threshold", th)
    chg("scoring_alert_threshold", th)

    # ── 出场（v1 口径）──
    for k, v in BASE_EXIT.items():
        chg_exit(k, v)
    for k, v in tgt["exit"].items():          # tp1_pct / sl_pct 品种差异
        chg_exit(k, v)

    if rescan:
        payload["rescan"] = True
    return payload, diff


def _norm(v):
    """数值比较用：'18' vs 18.0 视为相同；字符串也被 strip 后比较。"""
    if isinstance(v, str):
        return v.strip().lower()
    return v


def verify(cur: dict, tgt: dict) -> list:
    """下发后回读校验，返回不一致项列表。"""
    bad = []
    p = cur.get("params") or {}
    if p.get("periods") != tgt["periods"]:
        bad.append(f"params.periods={p.get('periods')} (应 {tgt['periods']})")
    if _norm(p.get("multiplier")) != _norm(tgt["multiplier"]):
        bad.append(f"params.multiplier={p.get('multiplier')} (应 {tgt['multiplier']})")
    if _norm(cur.get("score_engine")) != _norm(tgt["score_engine"]):
        bad.append(f"score_engine={cur.get('score_engine')!r} (应 {tgt['score_engine']!r})")
    if cur.get("use_scoring") is not True:
        bad.append(f"use_scoring={cur.get('use_scoring')} (应 True)")
    for k in ("scoring_full_threshold", "scoring_half_threshold", "scoring_alert_threshold"):
        if _norm(cur.get(k)) != _norm(tgt["threshold"]):
            bad.append(f"{k}={cur.get(k)} (应 {tgt['threshold']})")
    want = dict(BASE_EXIT, **tgt["exit"])
    er = cur.get("exit_rules") or {}
    for k, v in want.items():
        if _norm(er.get(k)) != _norm(v):
            bad.append(f"exit_rules.{k}={er.get(k)!r} (应 {v!r})")
    return bad


def dump_rollback(syms: dict):
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(HERE, "_bt2026_data", f"_rollback_43_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(syms, f, ensure_ascii=False, indent=1)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正下发（默认只打印 diff）")
    ap.add_argument("--rescan", action="store_true", help="下发后强制重扫历史信号")
    a = ap.parse_args()

    data = _get(HOST + "/api/trade/symbols")
    live = {s["symbol"]: s for s in data.get("symbols", [])}

    print("=" * 104)
    print(f"目标节点 {HOST}   （{'下发模式' if a.apply else 'DRY-RUN 预览，不加 --apply 不改任何东西'}）")
    print("=" * 104)

    missing = [s for s in TARGET if s not in live]
    if missing:
        print(f"!! 以下品种不在该节点，跳过：{missing}")

    all_ok, any_diff = True, False
    old = {}
    for sym, tgt in TARGET.items():
        cur = live.get(sym)
        if cur is None:
            continue
        old[sym] = cur
        payload, diff = build_payload(cur, tgt, a.rescan)
        p = cur.get("params") or {}
        print(f"\n──── {sym}  （{tgt['note']}）")
        print(f"    线上现状: ST={p.get('periods')}×{p.get('multiplier')}/{p.get('src')} "
              f"(change_atr={p.get('change_atr')}, fast/slow={p.get('fast_len')}/{p.get('slow_len')}, "
              f"{p.get('ma_type')})")
        print(f"    enabled={cur.get('enabled')}  use_scoring={cur.get('use_scoring')} "
              f"se={cur.get('score_engine')!r} thr={cur.get('scoring_full_threshold')}/"
              f"{cur.get('scoring_half_threshold')}/{cur.get('scoring_alert_threshold')}")
        er = cur.get("exit_rules") or {}
        print(f"    exit: tp1={er.get('tp1_pct')}%/{er.get('tp1_ratio')}%  "
              f"tp2={er.get('tp2_pct')}%/{er.get('tp2_ratio')}%  "
              f"tp3={er.get('tp3_pct')}%/{er.get('tp3_ratio')}%/{er.get('tp3_mode')}  "
              f"sl={er.get('sl_mode')}%{er.get('sl_pct')}  trail={er.get('trail_with_st')}  "
              f"保本={er.get('move_sl_to_entry')}")

        if not diff:
            print("    ✅ 已是目标配置，无需改动")
            continue
        any_diff = True
        print(f"    待改动 {len(diff)} 项：")
        for k, v0, v1 in diff:
            print(f"      {k:<34} {v0!r}  →  {v1!r}")

        if a.apply:
            try:
                resp = _post(payload)
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "ignore")[:300]
                print(f"    [FAIL] POST 失败 HTTP {e.code}: {body}")
                all_ok = False
                continue
            except Exception as e:  # noqa: BLE001
                print(f"    [FAIL] POST 失败: {e}")
                all_ok = False
                continue
            if not resp.get("ok"):
                print(f"    [FAIL] 返回 {resp}")
                all_ok = False
                continue
            # 回读校验
            after = {s["symbol"]: s for s in
                     _get(HOST + "/api/trade/symbols").get("symbols", [])}
            bad = verify(after.get(sym, {}), tgt)
            if bad:
                print("    [WARN] POST 成功但回读不一致：")
                for b in bad:
                    print(f"        - {b}")
                all_ok = False
            else:
                print("    [OK] 下发成功，回读校验一致")

    if a.apply:
        path = dump_rollback(old)
        print(f"\n旧配置已备份 → {os.path.basename(path)}")
        print("回滚：对照该文件把字段改回去（或用同样的 POST /api/trade/symbols 重发旧值）")
    elif any_diff:
        print("\n(DRY-RUN) 确认无误后加 --apply 真正下发")

    print("\n" + ("全部完成" if (not a.apply or all_ok) else "存在问题，请看上面输出"), flush=True)
    sys.exit(0 if (not a.apply or all_ok) else 1)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
