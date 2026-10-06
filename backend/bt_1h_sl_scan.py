# -*- coding: utf-8 -*-
"""在 tp1=1.5% / 平 70%（ExitRules 出厂默认）下，扫描止损参数该取多少。

上一轮 bt_1h_exit_check.py 只零散测了几个点，够回答问题、不够回答问题背后的
"选哪个值"。这里把 sl_mode × trail_with_st × sl_pct 三个维度铺满，看 sl 的取值
曲线到底长什么样——是单调的、还是有内部峰值、还是根本没差别（= 噪声）。

口径沿用 bt_1h_v3.py：1h + V3（原生周期 + 4h 上下文）· 生产 ST(10,3.0)
· 费率 0.05%/边 · fixed 100U×10x · 美东周末不开新仓 · V3 输入截断版（已验证等价）
分年本金 1000U，合计窗口 10000U（保证金占 1%，防止 equity 跌破保证金后
open_pos 静默跳单，那会让合计行失真）。
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from concurrent.futures import ProcessPoolExecutor, as_completed

import bt_1h_exit_opt as M          # 复用 worker：V3 截断补丁 + K 线加载

UTC = dt.timezone.utc


def ts_of(y, m=1, d=1) -> int:
    return int(dt.datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


BASE = dict(enabled=True, reverse_close=False, move_sl_to_entry=True,
            tp1_pct=1.5, tp1_ratio=70.0)

SL_VALS = [0.8, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]


def build_cases():
    out = []
    for mode in ("pct", "st"):
        for trail in (True, False):
            for sl in SL_VALS:
                out.append((mode, trail, sl,
                            {**BASE, "sl_mode": mode, "sl_pct": sl,
                             "trail_with_st": trail}))
    return out


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

    cases = build_cases()
    print(f"数据末端 {dt.datetime.fromtimestamp(end/1000, tz=UTC):%Y-%m-%d}"
          f"（2026* 不完整年；合计# 本金 10000U，与分年 1000U 百分比不可直接比）")
    print("固定 tp1=1.5% / 平 70% / 保本=True，扫描 sl_mode × trail × sl_pct"
          f" = {len(cases)} 组合 × {len(wins)} 窗口\n")

    res = {}
    owner = {}
    with ProcessPoolExecutor(max_workers=12, initializer=M._init_worker) as ex:
        for mode, trail, sl, params in cases:
            for label, lo, hi, init in wins:
                owner[ex.submit(M._run, (lo, hi, init, params))] = \
                    (mode, trail, sl, label)
        for f in as_completed(owner):
            p, m = f.result()
            if m is not None:
                res[owner[f]] = m

    for mode in ("pct", "st"):
        for trail in (True, False):
            tag = (f"sl_mode={mode}   trail_with_st={trail}")
            print("=" * 88)
            print(f"### {tag}")
            print("=" * 88)
            print(f"  {'sl%':>5} │{'2024':>16}{'2025':>16}{'2026*':>16} │"
                  f"{'合计#净U':>10}{'合计收益':>10}{'回撤':>8}{'PF':>7}")
            print("  " + "─" * 84)
            best = None
            for sl in SL_VALS:
                tot = res.get((mode, trail, sl, "合计#"))
                if not tot:
                    continue
                if best is None or tot["net"] > best[1]["net"]:
                    best = (sl, tot)
                row = f"  {sl:>5.1f} │"
                for label, *_ in wins[:3]:
                    m = res.get((mode, trail, sl, label))
                    row += (f"{m['net']:>+9.0f}U({m['ret']:>+5.1f}%)"
                            if m else f"{'—':>16}")
                row += (f" │{tot['net']:>+10.0f}{tot['ret']:>+10.2f}%"
                        f"{tot['dd']:>8.2f}{tot['pf']:>7.2f}")
                print(row)
            if best:
                print(f"   本组三年最优：sl_pct={best[0]}  → 合计 {best[1]['net']:+.0f}U"
                      f"（{best[1]['ret']:+.2f}%），回撤 {best[1]['dd']:.2f}%，"
                      f"PF {best[1]['pf']:.2f}")
            print()

    # 全局最优（三年合计）
    allrows = []
    for mode in ("pct", "st"):
        for trail in (True, False):
            for sl in SL_VALS:
                tot = res.get((mode, trail, sl, "合计#"))
                if tot:
                    allrows.append((mode, trail, sl, tot))
    allrows.sort(key=lambda x: -x[3]["net"])
    print("=" * 88)
    print("### 三年合计净 U 总排行（前 8）")
    print("=" * 88)
    print(f"  {'#':>3} {'参数':<34}{'合计净U':>10}{'收益%':>9}{'回撤%':>8}{'PF':>7}"
          f"{'2026*净U':>10}")
    print("  " + "─" * 78)
    for i, (mode, trail, sl, tot) in enumerate(allrows[:8], 1):
        y26 = res.get((mode, trail, sl, "2026*"), {})
        tag = f"sl={mode}{sl:.1f} trail={str(trail)[:1]}"
        print(f"  {i:>3} {tag:<34}{tot['net']:>+10.0f}{tot['ret']:>9.2f}"
              f"{tot['dd']:>8.2f}{tot['pf']:>7.2f}"
              f"{(y26.get('net', 0)):>+10.0f}")

    nets = [r[3]["net"] for r in allrows]
    pos26 = [r[3]["net"] for r in allrows
             if r[3]["net"] > 0 and res.get((r[0], r[1], r[2], "2026*"), {})
             .get("net", 0) > 0]
    print(f"\n  {len(allrows)} 个组合里，三年合计为正的有 "
          f"{sum(1 for n in nets if n > 0)} 个；")
    print(f"  三年为正【且】纯样本外 2026 也为正的，只有 {len(pos26)} 个。")

    bad = [k for k, m in res.items() if m.get("skip")]
    if bad:
        print(f"\n  ⚠️ {len(bad)} 格触发资金枯竭跳单，结果不可用：{sorted(set(bad))}")
    else:
        print("\n  skip 全为 0 —— 没有资金枯竭截断，数字可信。")
    print("=" * 88)


if __name__ == "__main__":
    main()
