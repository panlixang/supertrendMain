# -*- coding: utf-8 -*-
"""方向1：跨周期交叉验证 —— 2022(熊)/2023(复苏) 是从没参与调参的独立年。

复刻 exit_check 里标定的「赢家」(10/70/pct1.2/trail=F) 和「基线」(1.5/70/st2.0/trail=T)，
把它们跑在 2022、2023，并和已测的 2024-2026 拼成一张表，外加买入持有(BTC)对照。
一句话目标：看清赢家配置的好是 alpha 还是牛市 beta + IS 过拟合。

口径完全沿用 bt_1h_exit_check.py：1h+V3·ST(10,3)·费0.05%/边·fixed100U×10x·周末不开新仓·分年本金1000U。
"""
from __future__ import annotations
import datetime as dt
import sqlite3
from concurrent.futures import ProcessPoolExecutor, as_completed

import bt_1h_exit_opt as M

UTC = dt.timezone.utc


def ts_of(y, m=1, d=1) -> int:
    return int(dt.datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


# 复刻 exit_check 的赢家与基线（trail 以脚本标的为准）
CASES = [
    ("赢家 10/70 pct1.2 trail=F",
     dict(enabled=True, reverse_close=False, move_sl_to_entry=True,
          tp1_pct=10.0, tp1_ratio=70.0, sl_mode="pct", sl_pct=1.2,
          trail_with_st=False)),
    ("基线 1.5/70 st2.0 trail=T",
     dict(enabled=True, reverse_close=False, move_sl_to_entry=True,
          tp1_pct=1.5, tp1_ratio=70.0, sl_mode="st", sl_pct=2.0,
          trail_with_st=True)),
]


def bh(lo, hi, init):
    """买入持有对照：每年初买 init U 的 BTC，年末卖，扣双边费率。"""
    cs = [c for c in M._G["base"] if lo <= c["ts"] < hi]
    if len(cs) < 2:
        return None
    f = M.FEE
    p0, p1 = cs[0]["c"], cs[-1]["c"]
    peak, mdd = cs[0]["c"], 0.0
    for c in cs:
        if c["c"] > peak:
            peak = c["c"]
        mdd = max(mdd, (peak - c["c"]) / peak)
    qty = init * (1 - f) / p0
    final = qty * p1 * (1 - f)
    net = final - init
    return dict(net=net, ret=net / init * 100, dd=mdd * 100,
                pf=0.0, trades=1, skip=0, calmar=0.0)


def main():
    con = sqlite3.connect("candle_data.db")
    end = con.execute(
        "select max(ts) from candles where symbol='BTC-USDT' and tf='15m'"
    ).fetchone()[0]
    con.close()
    M._init_worker()  # 主进程先加载 _G（买入持有要用）

    WINS = [
        ("2022", ts_of(2022), ts_of(2023), 1000.0),
        ("2023", ts_of(2023), ts_of(2024), 1000.0),
        ("2024", ts_of(2024), ts_of(2025), 1000.0),
        ("2025", ts_of(2025), ts_of(2026), 1000.0),
        ("2026*", ts_of(2026), end, 1000.0),
        ("22-26全", ts_of(2022), end, 5000.0),
    ]
    print(f"数据末端 {dt.datetime.fromtimestamp(end/1000, tz=UTC):%Y-%m-%d}（2026* 不完整）")
    print("口径沿用 exit_check：1h+V3·ST(10,3)·费0.05%/边·fixed100U×10x·周末不开新仓·分年本金1000U")

    res, tasks = {}, {}
    with ProcessPoolExecutor(max_workers=8, initializer=M._init_worker) as ex:
        for nm, params in CASES:
            for lbl, lo, hi, init in WINS:
                tasks[ex.submit(M._run, (lo, hi, init, params))] = (nm, lbl)
        for fu in as_completed(tasks):
            p, m = fu.result()
            if m is not None:
                res[tasks[fu]] = m

    bhres = {lbl: bh(lo, hi, init) for lbl, lo, hi, init in WINS}

    def cell(rmap, name, lbl):
        m = rmap.get((name, lbl))
        if not m:
            return f"{'—':>17}"
        return f"{m['net']:>+8.0f}U ({m['ret']:>+6.2f}%)"

    def cellb(m):
        if not m:
            return f"{'—':>17}"
        return f"{m['net']:>+8.0f}U ({m['ret']:>+6.2f}%)"

    print("=" * 108)
    print("### 净U与收益%（括号=相对本金%；分年本金1000U，22-26全=5000U）")
    print("=" * 108)
    print(f"  {'配置':<28}" + "".join(f"{l:>18}" for l, *_ in WINS))
    print("  " + "─" * 104)
    for nm, _ in CASES:
        print(f"  {nm:<28}" + "".join(cell(res, nm, l) for l, *_ in WINS))
    print(f"  {'买入持有(BTC)':<28}" + "".join(cellb(bhres.get(l)) for l, *_ in WINS))

    print("\n" + "=" * 108)
    print("### 回撤% / PF / 笔数")
    print("=" * 108)
    print(f"  {'配置':<28}" + "".join(f"{l:>18}" for l, *_ in WINS))
    print("  " + "─" * 104)
    for nm, _ in CASES:
        row = f"  {nm:<28}"
        for lbl, *_ in WINS:
            m = res.get((nm, lbl))
            row += (f"{m['dd']:>7.2f}% PF{m['pf']:>5.2f} 笔{m['trades']:>4}"
                    if m else f"{'—':>18}")
        print(row)
    brow = f"  {'买入持有(BTC)':<28}"
    for lbl, *_ in WINS:
        m = bhres.get(lbl)
        brow += (f"{m['dd']:>7.2f}% PF0.00 笔  1" if m else f"{'—':>18}")
    print(brow)

    def net_of(name, *labels):
        return sum(res.get((name, l), {}).get("net", 0.0) for l in labels)

    print("\n" + "=" * 108)
    print("### 交叉验证判读（IS=调参见过的 2024-25；OOS全=从没见过的 2022+2023+2026*）")
    print("=" * 108)
    win, base = CASES[0][0], CASES[1][0]
    is_seg, oos_seg = ["2024", "2025"], ["2022", "2023", "2026*"]
    print(f"  赢家    IS(2024-25)       净U = {net_of(win, *is_seg):>+8.0f}")
    print(f"  赢家    OOS全(22-23-26*)  净U = {net_of(win, *oos_seg):>+8.0f}")
    print(f"  基线    IS(2024-25)       净U = {net_of(base, *is_seg):>+8.0f}")
    print(f"  基线    OOS全(22-23-26*)  净U = {net_of(base, *oos_seg):>+8.0f}")
    bh_is = bhres['2024']['net'] + bhres['2025']['net']
    bh_oos = bhres['2022']['net'] + bhres['2023']['net'] + bhres['2026*']['net']
    print(f"  买入持有 IS(2024-25)       净U = {bh_is:>+8.0f}")
    print(f"  买入持有 OOS全(22-23-26*)  净U = {bh_oos:>+8.0f}")
    print("  判读：若赢家 OOS全 转亏/远逊 IS → 好是牛市beta+过拟合，丢弃；")
    print("        若赢家 OOS全 仍稳赚且回撤可控 → 有真 alpha，值得深挖。")
    print("=" * 108)


if __name__ == "__main__":
    main()
