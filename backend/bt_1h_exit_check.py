# -*- coding: utf-8 -*-
"""单独核算 ExitRules 出厂默认值 tp1=1.5% / 平 70% 的盈亏。

这组值是 position.py:37-38 的 dataclass 默认值——也就是前端不带任何覆盖时的
配置，两轮网格都没覆盖到它（第 1 轮 tp1 最低 1.0 但配的是旧 sl，第 2 轮 tp1
最低 4.0）。这里把它和几个相邻变体、以及之前的候选放同一张表里横向对比。

口径沿用 bt_1h_v3.py：1h + V3（原生周期 + 4h 上下文）· 生产 ST(10,3.0)
· 费率 0.05%/边 · fixed 100U×10x · 美东周末不开新仓 · V3 输入截断版（已验证等价）
分年本金 1000U，合计窗口 10000U（保证金占 1%，防止连亏导致 open_pos 静默跳单）。
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from concurrent.futures import ProcessPoolExecutor, as_completed

import bt_1h_exit_opt as M          # 复用 worker：V3 截断补丁 + K 线加载

UTC = dt.timezone.utc


def ts_of(y, m=1, d=1) -> int:
    return int(dt.datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


BASE = dict(enabled=True, reverse_close=False, move_sl_to_entry=True)

CASES = [
    # ── 出厂默认 tp1 1.5 / 平 70 这一族 ──
    ("默认 1.5/70  sl=st2.0  trail=T",
     {**BASE, "tp1_pct": 1.5, "tp1_ratio": 70.0,
      "sl_mode": "st", "sl_pct": 2.0, "trail_with_st": True}),
    ("1.5/70  sl=st2.0  trail=F",
     {**BASE, "tp1_pct": 1.5, "tp1_ratio": 70.0,
      "sl_mode": "st", "sl_pct": 2.0, "trail_with_st": False}),
    ("1.5/70  sl=pct2.0 trail=T",
     {**BASE, "tp1_pct": 1.5, "tp1_ratio": 70.0,
      "sl_mode": "pct", "sl_pct": 2.0, "trail_with_st": True}),
    ("1.5/70  sl=pct2.0 trail=F",
     {**BASE, "tp1_pct": 1.5, "tp1_ratio": 70.0,
      "sl_mode": "pct", "sl_pct": 2.0, "trail_with_st": False}),
    ("1.5/70  sl=pct3.0 trail=F",
     {**BASE, "tp1_pct": 1.5, "tp1_ratio": 70.0,
      "sl_mode": "pct", "sl_pct": 3.0, "trail_with_st": False}),
    ("1.5/70  sl=pct3.0 trail=T",
     {**BASE, "tp1_pct": 1.5, "tp1_ratio": 70.0,
      "sl_mode": "pct", "sl_pct": 3.0, "trail_with_st": True}),
    ("1.5/70  sl=pct1.2 trail=T",
     {**BASE, "tp1_pct": 1.5, "tp1_ratio": 70.0,
      "sl_mode": "pct", "sl_pct": 1.2, "trail_with_st": True}),
    ("1.5/70  sl=pct1.2 trail=F",
     {**BASE, "tp1_pct": 1.5, "tp1_ratio": 70.0,
      "sl_mode": "pct", "sl_pct": 1.2, "trail_with_st": False}),
    # ── 对照 ──
    ("生产现值 4.0/50 sl=pct3.0 trail=T",
     {**BASE, "tp1_pct": 4.0, "tp1_ratio": 50.0,
      "sl_mode": "pct", "sl_pct": 3.0, "trail_with_st": True}),
    ("上轮推荐 10.0/70 sl=pct1.2 trail=F",
     {**BASE, "tp1_pct": 10.0, "tp1_ratio": 70.0,
      "sl_mode": "pct", "sl_pct": 1.2, "trail_with_st": False}),
]


def main():
    con = sqlite3.connect("candle_data.db")
    end = con.execute(
        "select max(ts) from candles where symbol='BTC-USDT' and tf='15m'"
    ).fetchone()[0]
    con.close()

    wins = [("2024", ts_of(2024), ts_of(2025), 1000.0),
            ("2025", ts_of(2025), ts_of(2026), 1000.0),
            ("2026*", ts_of(2026), end, 1000.0),
            ("合计#", ts_of(2024), end, 10000.0)]

    print(f"数据末端 {dt.datetime.fromtimestamp(end/1000, tz=UTC):%Y-%m-%d}"
          f"（2026* 只到 9/24，是不完整年）")
    print("口径：1h + V3 · ST(10,3.0) · 费率 0.05%/边 · fixed 100U×10x · 周末不开新仓")
    print("      分年本金 1000U，合计# 本金 10000U（百分比不可跨这两组直接比）\n")

    res = {}
    tasks = {}
    with ProcessPoolExecutor(max_workers=8, initializer=M._init_worker) as ex:
        for name, params in CASES:
            for label, lo, hi, init in wins:
                tasks[ex.submit(M._run, (lo, hi, init, params))] = (name, label)
        for f in as_completed(tasks):
            p, m = f.result()
            if m is not None:
                res[tasks[f]] = m

    def cell(name, label):
        m = res.get((name, label))
        if not m:
            return f"{'—':>17}"
        return f"{m['net']:>+8.0f}U ({m['ret']:>+6.2f}%)"

    for title, field, fmt in (("净 U 与收益（括号为相对本金的 %）", "cell", None),):
        print("=" * 96)
        print(f"### {title}")
        print("=" * 96)
        print(f"  {'配置':<34}" + "".join(f"{l:>18}" for l, *_ in wins))
        print("  " + "─" * 92)
        for name, _ in CASES:
            print(f"  {name:<34}" + "".join(cell(name, l) for l, *_ in wins))
        print()

    print("=" * 96)
    print("### 回撤 / PF / 笔数（同一批数据）")
    print("=" * 96)
    print(f"  {'配置':<34}" + "".join(f"{l:>18}" for l, *_ in wins))
    print("  " + "─" * 92)
    for name, _ in CASES:
        row = f"  {name:<34}"
        for label, *_ in wins:
            m = res.get((name, label))
            row += (f"{m['dd']:>7.2f}% PF{m['pf']:>5.2f}"
                    if m else f"{'—':>18}")
        print(row)

    bad = [k for k, m in res.items() if m.get("skip")]
    if bad:
        print(f"\n  ⚠️ 有 {len(bad)} 组触发资金枯竭跳单（open_pos 因 equity<保证金 静默放过信号），")
        print("     这些格子的结果不可用：", sorted(set(bad)))
    else:
        print("\n  skip 全为 0 —— 没有资金枯竭截断，数字可信。")
    print("=" * 96)


if __name__ == "__main__":
    main()
