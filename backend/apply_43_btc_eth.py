# -*- coding: utf-8 -*-
"""把 BTC/ETH 的定稿参数下发到 43 服务（43.108.10.84:5174）。

目标配置（均来自 1h 回测，2021-01~2026-11）：
  BTC-USDT-SWAP  ST11x6 + 无 ATR 过滤 + 3.0ATR 止损(sl_mode=st 走轨道线)
                 + TP1 2% 平 30% + 保本 + 剩余反向平仓
                 回测：+111.5% / 净PF 1.76 / OOS 1.32 / 压力 1.16 / 全周期回撤 8.45%
  ETH-USDT-SWAP  ST12x4 + ATR ratio>=1.3 + 无初始止损 + TP1 1.5% 平 20% + 保本 + 反向平
                 回测：+42.0% / 净PF 3.17 / DEV 2.55 / VAL 2.53 / OOS 4.21 / 压力 3.90
                 / 最差段 2.53 / 全周期回撤 5.03%（六年全正）

用法：
  python apply_43_btc_eth.py            # dry-run，只打印差异
  python apply_43_btc_eth.py --apply    # 下发（先备份线上配置，写完回读校验）

注意：
  1) BTC 的 3.0ATR 硬止损用 sl_mode="st" 表达（轨道线本来就由 ATR 决定，≈3ATR）；
     sl_pct=2.4 只是轨道线无效时的兜底（BTC 1h 的 3ATR 中位≈2.4%）。
  2) ETH 的「无初始止损」用 sl_mode=pct + sl_pct=10 表达（10x 杠杆下约等于强平线），
     只作保险丝，不参与策略逻辑。
  3) 两个品种都不跟轨（trail_with_st=False）：剩余仓位只等反向信号平。
  4) ETH 的 allow_tfs 由 15m 改为 1h（15x6/R1.2 是 1h 参数，15m 未验证）。
"""

import json
import os
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

HOST = os.environ.get("HOST43", "http://43.108.10.84:5174")
BACKEND = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(BACKEND, "_antichop_data")

TARGETS = {
    "BTC-USDT-SWAP": {
        "st_periods": 11,
        "st_multiplier": 6.0,
        "atr_ratio_enabled": False,
        "exit_mode": "single",
        "reverse_close": False,
        "move_sl_to_entry": True,
        "trail_with_st": False,
        "tp1_pct": 2.0, "tp1_ratio": 30.0,
        "tp2_pct": 999.0, "tp2_ratio": 0.0,
        "sl_mode": "st",            # 轨道线作初始硬止损（≈3ATR）
        "sl_pct": 2.4,              # 仅轨道线无效时兜底
    },
    "ETH-USDT-SWAP": {
        "allow_tfs": ["1h"],        # 由 15m 改 1h
        "st_periods": 12,
        "st_multiplier": 4.0,
        "atr_ratio_enabled": True,
        "atr_ratio_threshold": 1.3,
        "atr_ratio_window": 50,
        "range_gate": False,        # 关掉与 ATR ratio>=1.3 冲突的区间闸门
        "exit_mode": "single",
        "reverse_close": False,
        "tp1_pct": 1.5, "tp1_ratio": 20.0,
        "tp2_pct": 999.0, "tp2_ratio": 0.0,
        "move_sl_to_entry": True,
        "trail_with_st": False,
        "sl_mode": "pct", "sl_pct": 10.0,
    },
}


def _req(path: str, body: dict | None = None, timeout: int = 20) -> dict:
    url = HOST + path
    if body is None:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
    else:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def current() -> dict:
    d = _req("/api/pattern/trade/config")
    return {s["symbol"]: s for s in (d.get("symbols") or [])}


def diff_rows(before: dict, targets: dict) -> list[tuple]:
    rows = []
    for sym, patch in targets.items():
        cur = before.get(sym) or {}
        for k, v in patch.items():
            old = cur.get(k, "<缺失>")
            if old != v:
                rows.append((sym, k, old, v))
    return rows


def main() -> None:
    apply = "--apply" in sys.argv
    before = current()
    missing = [s for s in TARGETS if s not in before]
    if missing:
        print(f"✗ 线上没有这些品种: {missing}")
        return
    for sym in TARGETS:
        pos = before[sym].get("position")
        print(f"{sym}: enabled={before[sym].get('enabled')} "
              f"margin={before[sym].get('margin_usdt')} lev={before[sym].get('leverage')} "
              f"allow_tfs={before[sym].get('allow_tfs')} 持仓={pos or '空'}")

    rows = diff_rows(before, TARGETS)
    print(f"\n待改字段 {len(rows)} 项：")
    for sym, k, old, new in rows:
        print(f"  {sym:<14} {k:<20} {old!r:>22} -> {new!r}")

    if not rows:
        print("\n已是最新，无需下发。")
        return
    if not apply:
        print("\n[dry-run] 加 --apply 才会真正写入。")
        return

    os.makedirs(BACKUP_DIR, exist_ok=True)
    bak = os.path.join(BACKUP_DIR, f"live_cfg_43_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(bak, "w", encoding="utf-8") as fh:
        json.dump(before, fh, ensure_ascii=False, indent=2)
    print(f"\n[备份] {bak}")

    for sym, patch in TARGETS.items():
        r = _req("/api/pattern/trade/symbols",
                 {"action": "update", "symbol": sym, **patch})
        print(f"[下发] {sym}: ok={r.get('ok')} err={r.get('error')}")

    after = current()
    left = diff_rows(after, TARGETS)
    print("\n[回读校验]")
    for sym in TARGETS:
        a = after[sym]
        print(f"  {sym:<14} st={a.get('st_periods')}x{a.get('st_multiplier')} "
              f"atr_ratio={a.get('atr_ratio_enabled')}/{a.get('atr_ratio_threshold')} "
              f"tfs={a.get('allow_tfs')} exit={a.get('exit_mode')} "
              f"rev={a.get('reverse_close')} tp1={a.get('tp1_pct')}/{a.get('tp1_ratio')} "
              f"be={a.get('move_sl_to_entry')} trail={a.get('trail_with_st')} "
              f"sl={a.get('sl_mode')}/{a.get('sl_pct')} gate={a.get('range_gate')}")
    print("  残留差异:", left if left else "无")


if __name__ == "__main__":
    main()
