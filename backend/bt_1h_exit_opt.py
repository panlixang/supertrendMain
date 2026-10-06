# -*- coding: utf-8 -*-
"""1h + V3 的出场参数寻优 —— 带样本外验证。

⚠️ 先说清楚这件事的风险
------------------------
上一个阶段的结论是：1h+V3 三年 440 笔，净 U 只有 +60U（生产出场）/ +260U（简化
出场），而同期买入持有 +97.7%。在这么薄的 alpha 上做参数寻优，**最容易的结果是把
样本内的噪声当规律**。所以本脚本不用「全样本取最优」这种必然过拟合的做法，而是：

    IS  (样本内) 2024-01-01 ~ 2025-06-30   ← 只在这段上调参、选候选
    OOS (样本外) 2025-07-01 ~ 2026-09-24   ← 参数锁死后丢到这里打分

所有「选谁」的动作只允许用 IS；OOS 只用来「打分」，绝不用它挑参数。
评判标准不是「IS 上谁第一」，而是「IS 上的好参数，到 OOS 上还在不在好参数里」。

调参空间（ExitRules，position.py:33-48）
---------------------------------------
tp1_pct / tp1_ratio / sl_mode / sl_pct / trail_with_st / move_sl_to_entry
生产现值：tp1=4.0% 平 50%，sl_mode='pct' sl_pct=3.0，保本开，跟 ST 开。

性能
----
单次 1h 回测约 13~22s，瓶颈是 features_from_candles 每次对整个历史重算 SuperTrend。
这里把喂给 V3 的输入截断为「以信号 bar 结尾的最近 600 根 + 4h 最近 300 根」——
这正是生产 pattern_trade.py:703-704 的取数方式（_kline(tf,600) / _kline('4h',300)）。
已实测验证：截断前后 trades / return / max_dd / PF / final 完全一致。
单进程约快 1.7x，再叠加多进程并行（12 workers）。
"""
from __future__ import annotations

import datetime as dt
import itertools
import sqlite3
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

from backtest_engine import run_backtest
from bt_2026_weekend import (ST_P, FEE, PROD_EXIT, SIMPLE_EXIT,
                             is_weekend_et, resample_ms)
from position import ExitRules
from sl2_tf_sweep import load_db, load_tf

UTC = dt.timezone.utc
TF = "1h"
MAXW = 12
INIT = 10000.0          # 保证金占权益 1%，防止连亏导致 open_pos 静默跳单


def ts_of(y, m=1, d=1) -> int:
    return int(dt.datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


# ─────────────────── worker（顶层函数，Windows spawn 下要可 pickle） ───────────────────

_G: dict = {}


def _init_worker():
    """子进程初始化：装 V3 输入截断补丁 + 各进程自载一份 K 线。"""
    import backtest_engine as BE

    _orig = BE.features_from_candles

    def wrapped(candles, i, side, candles_htf=None, N=600, M=300, **kw):
        # 保留「以 i 结尾的最近 N 根」并把 i 同步换算过去。
        # ⚠️ 不能直接 candles[-N:] 而不动 i —— 那样前面所有 bar 的 i 会变成负数
        # 索引，Python 会从末尾取到完全错误的 K 线（实测会让 165 笔变成 9 笔）。
        d = max(0, i - N + 1)
        candles = candles[d:]
        i -= d
        if candles_htf is not None and len(candles_htf) > M:
            candles_htf = candles_htf[-M:]
        return _orig(candles, i, side, candles_htf=candles_htf, **kw)

    BE.features_from_candles = wrapped
    _G["base"] = resample_ms(load_db("BTC-USDT", "15m"), 60 * 60_000, 4)
    _G["cbtf4"] = load_tf("BTC-USDT", "4h")


def _run(task):
    """task = (lo, hi, init, params) → (params, metrics | None)。"""
    lo, hi, init, params = task
    cs = [c for c in _G["base"] if lo <= c["ts"] < hi]
    if len(cs) < 500:
        return params, None
    try:
        r = run_backtest(cs, ST_P, init_cash=init, fee_rate=FEE, allow_short=True,
                         sizing="fixed", margin_usdt=100.0, leverage=10,
                         exit_rules=ExitRules(**params), v3_filter=True,
                         gate_tf=TF, candles_by_tf={TF: cs, "4h": _G["cbtf4"]},
                         block_if=is_weekend_et)
    except Exception as e:                                    # pragma: no cover
        return params, {"err": repr(e)[:110]}
    ret, dd = r["return_pct"], r["max_dd_pct"]
    return params, dict(trades=r["trades"], ret=ret, dd=dd,
                        pf=r["profit_factor"] or 0.0,
                        net=r["final"] - init,
                        calmar=(ret / dd if dd > 0 else 0.0),
                        skip=r["skipped_insufficient"])


# ─────────────────── 网格 ───────────────────

# 第 2 轮：放宽范围，修掉第 1 轮的边界效应（sl_pct 撞下界 1.5、tp1_pct 撞上界 6.0）。
# trail_with_st 第 1 轮已被强证据判定（IS 榜前 15 全是 False，生产现值 True 只排
# 92/320），本轮固定为 False，省下的算力用来把两个连续量走宽。
GRID = dict(
    tp1_pct=[4.0, 5.0, 6.0, 8.0, 10.0, 12.0],   # 上探，不再撞 6.0 的上界
    tp1_ratio=[30.0, 50.0, 70.0, 100.0],
    sl_pct=[0.6, 0.8, 1.0, 1.2, 1.5, 2.0],      # 下探，不再撞 1.5 的下界
    trail_with_st=[False],
)
FIXED = dict(enabled=True, move_sl_to_entry=True, sl_mode="pct",
             reverse_close=False)

PROD_PARAMS = dict(enabled=True, tp1_pct=4.0, tp1_ratio=50.0,
                   move_sl_to_entry=True, sl_mode="pct", sl_pct=3.0,
                   trail_with_st=True, reverse_close=False)


def grid_combos():
    keys = list(GRID)
    out = []
    for vals in itertools.product(*(GRID[k] for k in keys)):
        p = dict(FIXED)
        p.update(zip(keys, vals))
        out.append(p)
    return out


def key(p):
    return (p["tp1_pct"], p["tp1_ratio"], p["sl_pct"], p["trail_with_st"],
            p["sl_mode"], p["move_sl_to_entry"], p["enabled"])


def desc(p):
    return (f"tp1 {p['tp1_pct']:.1f}% / 平 {p['tp1_ratio']:.0f}% / "
            f"sl {p['sl_pct']:.1f}% / trail={str(p['trail_with_st'])[:1]}")


def sdesc(p):
    """短标签，顺序固定为 tp1 / 平仓% / sl。"""
    return f"{p['tp1_pct']:.1f}/{p['tp1_ratio']:.0f}/{p['sl_pct']:.1f}"


def neighbours(p):
    """每个维度 ±1 格的邻居组合。"""
    out = []
    for k, vals in GRID.items():
        if len(vals) < 2 or p.get(k) not in vals:
            continue
        i = vals.index(p[k])
        for j in (i - 1, i + 1):
            if 0 <= j < len(vals):
                q = dict(p)
                q[k] = vals[j]
                out.append(q)
    return out


def plate_scores(res):
    """返回 {key: (邻域平均净U, 邻居数, [各邻居净U])}。

    用于区分「尖峰」和「高原」：真正稳健的参数，改动一档之后应该还是好的。
    """
    by_k = {key(p): m for p, m in res}
    out = {}
    for p, m in res:
        ns = []
        for q in neighbours(p):
            mm = by_k.get(key(q))
            if mm:
                ns.append(mm["net"])
        out[key(p)] = (sum(ns) / len(ns) if ns else m["net"], len(ns), ns)
    return out


def print_plate(res, plate, n=12):
    rows = sorted(res, key=lambda x: -plate[key(x[0])][0])[:n]
    print(f"\n  【尖峰 vs 高原】按邻域均值排；"
          f"邻居 = 每个维度 ±1 格（标签顺序 tp1/平%/sl）")
    print(f"  {'参数':<16}{'自身IS':>9}{'邻域均':>9}{'邻域最小':>10}"
          f"{'邻域最大':>10}{'比值':>7}")
    print("  " + "─" * 61)
    for p, m in rows:
        avg, cnt, ns = plate[key(p)]
        ratio = avg / m["net"] if m["net"] else 0.0
        print(f"  {sdesc(p):<16}{m['net']:>9.0f}{avg:>9.0f}"
              f"{(min(ns) if ns else 0):>10.0f}{(max(ns) if ns else 0):>10.0f}"
              f"{ratio:>7.2f}")
    print("   比值 = 邻域均值 ÷ 自身。<0.5 = 孤立尖峰（大概率噪声）；"
          "接近 1 = 高原（改动参数不崩）。")


# ─────────────────── 跑 ───────────────────

def run_batch(lo, hi, combos, workers=MAXW, every=60):
    res = []
    tasks = [(lo, hi, INIT, p) for p in combos]
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker) as ex:
        futs = [ex.submit(_run, t) for t in tasks]
        for n, f in enumerate(as_completed(futs), 1):
            p, m = f.result()
            if m is not None and "err" not in m:
                res.append((p, m))
            if n % every == 0:
                print(f"      …{n}/{len(tasks)}", flush=True)
    return res


def run_local(lo, hi, params):
    """主进程里跑单个参数（用于 baseline 对照）。"""
    if not _G:
        _init_worker()
    return _run((lo, hi, INIT, params))[1]


def show_rows(rows, title, n=15, sort="calmar"):
    print(f"\n  {title}")
    print(f"  {'#':>3} {'tp1%':>6}{'平%':>6}{'sl%':>5}{'trail':>6}"
          f"│{'笔数':>6}{'净U':>9}{'收益%':>9}{'回撤%':>8}{'PF':>7}{'Calmar':>8}")
    print("  " + "─" * 68)
    rows = sorted(rows, key=lambda x: -x[1][sort])[:n]
    for i, (p, m) in enumerate(rows, 1):
        mark = "  ← 生产现值" if key(p) == key(PROD_PARAMS) else ""
        print(f"  {i:>3} {p['tp1_pct']:>6.1f}{p['tp1_ratio']:>6.0f}"
              f"{p['sl_pct']:>5.1f}{str(p['trail_with_st'])[:1]:>6}│"
              f"{m['trades']:>6}{m['net']:>9.0f}{m['ret']:>9.2f}"
              f"{m['dd']:>8.2f}{m['pf']:>7.2f}{m['calmar']:>8.2f}{mark}")
    return rows


def spearman(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    def rank(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0] * n
        for pos, i in enumerate(order):
            r[i] = pos
        return r
    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx) ** 0.5
    syy = sum((b - my) ** 2 for b in ry) ** 0.5
    return sxy / (sxx * syy) if sxx and syy else 0.0


def main():
    con = sqlite3.connect("candle_data.db")
    data_end = con.execute(
        "select max(ts) from candles where symbol='BTC-USDT' and tf='15m'"
    ).fetchone()[0]
    con.close()

    IS = (ts_of(2024), ts_of(2025, 7, 1))
    OOS = (ts_of(2025, 7, 1), data_end)

    print(f"数据末端 {dt.datetime.fromtimestamp(data_end/1000, tz=UTC):%Y-%m-%d}")
    print("口径：1h + V3（原生周期 + 4h 上下文）· 生产 ST(10,3.0) · 费率 0.05%/边")
    print("      fixed 100U×10x · 本金 10000U · 周末不开新仓 · V3 输入截断版（已验证等价）")
    print(f"\n  IS  样本内 2024-01-01 ~ 2025-06-30  ← 只允许在这段选参数")
    print(f"  OOS 样本外 2025-07-01 ~ 2026-09-24  ← 只打分，不挑参数")

    combos = grid_combos()
    print(f"\n网格 {len(combos)} 组合 = tp1_pct{len(GRID['tp1_pct'])} × "
          f"tp1_ratio{len(GRID['tp1_ratio'])} × sl_pct{len(GRID['sl_pct'])} × "
          f"trail{len(GRID['trail_with_st'])}（固定 保本=True, sl_mode=pct）")

    t0 = time.time()
    print(f"\n[1/3] IS 上跑 {len(combos)} 组合 …")
    is_res = run_batch(IS[0], IS[1], combos)
    print(f"      完成 {len(is_res)} 个，用时 {time.time()-t0:.0f}s")
    is_bad = sum(1 for _, m in is_res if m["skip"])
    if is_bad:
        print(f"      ⚠️ 有 {is_bad} 个组合触发资金枯竭跳单，结果不可用")

    top_c = show_rows(is_res, "IS 榜 · 按 Calmar（收益/回撤）排序", sort="calmar")
    top_n = show_rows(is_res, "IS 榜 · 按净 U 排序", sort="net")

    plate = plate_scores(is_res)
    print_plate(is_res, plate)

    print("\n  【基线对照 · IS】")
    for tag, rules in (("生产现值 4.0/50/sl3.0", PROD_EXIT),
                       ("简化出场 enabled=False", SIMPLE_EXIT)):
        m = run_local(IS[0], IS[1], dict(
            enabled=rules.enabled, tp1_pct=rules.tp1_pct,
            tp1_ratio=rules.tp1_ratio, move_sl_to_entry=rules.move_sl_to_entry,
            sl_mode=rules.sl_mode, sl_pct=rules.sl_pct,
            trail_with_st=rules.trail_with_st, reverse_close=rules.reverse_close))
        print(f"  {tag:<26} 笔数{m['trades']:>5}  净U{m['net']:>8.0f}"
              f"  收益{m['ret']:>8.2f}%  回撤{m['dd']:>6.2f}%  PF{m['pf']:>6.2f}"
              f"  Calmar{m['calmar']:>6.2f}")

    # ── OOS 打分：候选只用 IS 选出 ──
    # 三套选法都只用 IS，用来对比「哪种选法选出来的人，到 OOS 更稳」
    plate_pick = sorted(is_res, key=lambda x: -plate[key(x[0])][0])[:10]
    src = {}
    for lbl, rows in (("Calmar榜", top_c), ("净U榜", top_n),
                      ("邻域榜", plate_pick)):
        for p, _ in rows:
            src.setdefault(key(p), []).append(lbl)

    cand, seen = [PROD_PARAMS], {key(PROD_PARAMS)}
    for p, _ in list(top_c) + list(top_n) + plate_pick:
        k = key(p)
        if k not in seen:
            seen.add(k)
            cand.append(p)
    print(f"\n[2/3] 用 IS 选出 {len(cand)} 个候选（含生产现值），"
          f"锁死参数后丢到 OOS 打分 …")
    t0 = time.time()
    oos_res = run_batch(OOS[0], OOS[1], cand, every=40)
    print(f"      完成，用时 {time.time()-t0:.0f}s")
    show_rows(oos_res, "OOS 榜 · 按净 U 排序", n=20, sort="net")

    # ── IS 名次 vs OOS 名次 ──
    is_by_k = {key(p): m for p, m in is_res}
    oos_by_k = {key(p): m for p, m in oos_res}
    is_rank = {k: i + 1 for i, (k, _) in enumerate(
        sorted(is_by_k.items(), key=lambda kv: -kv[1]["net"]))}
    oos_rank = {k: i + 1 for i, (k, _) in enumerate(
        sorted(oos_by_k.items(), key=lambda kv: -kv[1]["net"]))}
    pairs = sorted((is_rank[k], oos_rank[k], k) for k in oos_by_k if k in is_rank)

    print("\n  【候选：IS 名次 → OOS 名次】（标签顺序 tp1/平%/sl）")
    print(f"  {'参数':<16}{'入选来源':<12}{'IS净U':>8}{'IS名':>6}"
          f"{'OOS净U':>9}{'OOS名':>7}{'变化':>7}")
    print("  " + "─" * 65)
    for ir, orank, k in pairs:
        p_tag = f"{k[0]}/{k[1]:.0f}/{k[2]}/{str(k[3])[:1]}"
        if k[4] != "pct" or not k[5]:
            p_tag += f" {k[4]},保本={str(k[5])[:1]}"
        mark = "  ←生产" if k == key(PROD_PARAMS) else ""
        tag = "/".join(src.get(k, []))
        print(f"  {p_tag:<16}{tag:<12}{is_by_k[k]['net']:>8.0f}{ir:>6}"
              f"{oos_by_k[k]['net']:>9.0f}{orank:>7}{ir - orank:>+7}{mark}")

    # ── 哪种 IS 选法更稳 ──
    print("\n  【哪种 IS 选法选出的人，到 OOS 更稳】")
    print(f"  {'选法':<14}{'n':>4}{'OOS均值U':>11}{'为正数':>9}{'OOS最好':>10}")
    print("  " + "─" * 48)
    for lbl in ("净U榜", "Calmar榜", "邻域榜"):
        ks = [k for k, v in src.items() if lbl in v and k in oos_by_k]
        if not ks:
            continue
        nets = [oos_by_k[k]["net"] for k in ks]
        pos = sum(1 for x in nets if x > 0)
        print(f"  {lbl:<14}{len(ks):>4}{sum(nets)/len(nets):>11.0f}"
              f"{pos}/{len(ks):>7}{max(nets):>10.0f}")

    # ── 最终候选：IS 不差 + OOS 为正 + 邻域是高原 ──
    n_is = len(is_res)
    rank_map = {k: i for i, (k, _) in enumerate(
        sorted(is_by_k.items(), key=lambda kv: -kv[1]["net"]))}
    both = []
    for k, m in oos_by_k.items():
        if k not in is_by_k or m["net"] <= 0:
            continue
        r = rank_map[k]
        if r > n_is // 3:                       # IS 必须进前 1/3
            continue
        avg = plate.get(k, (0, 0, []))[0]
        both.append((k, m, is_by_k[k], avg, r + 1))
    both.sort(key=lambda x: -x[1]["net"])
    print(f"\n  【最终候选】IS 进前 1/3 且 OOS 为正（共 {len(both)} 个）")
    if both:
        print(f"  {'tp1/平%/sl':<16}{'IS净U':>9}{'IS名次':>8}{'OOS净U':>9}"
              f"{'OOS回撤':>9}{'OOS PF':>8}{'邻域均':>8}")
        print("  " + "─" * 67)
        for k, mo, mi, avg, r in both[:12]:
            print(f"  {f'{k[0]}/{k[1]:.0f}/{k[2]}':<16}{mi['net']:>9.0f}"
                  f"{r:>8}{mo['net']:>9.0f}{mo['dd']:>9.2f}{mo['pf']:>8.2f}"
                  f"{avg:>8.0f}")
    else:
        print("    一个都没有 —— IS 上表现好的参数，样本外全军覆没。")

    if len(pairs) >= 3:
        rho = spearman([p[0] for p in pairs], [p[1] for p in pairs])
        print(f"\n  IS 名次 vs OOS 名次 秩相关 ρ = {rho:+.3f}（n={len(pairs)}）")
        print("   ρ ≈ 0 或负 → 样本内的好坏预测不了样本外，参数差异基本是噪声")
        print("   ρ 明显为正 → 才说明出场参数有稳定的效应")

    # ── 3) 第二阶段：再动两个开关（sl_mode / 保本），同样 IS 选、OOS 验 ──
    print("\n[3/3] 第二阶段：取 IS 榜前 3（按 Calmar），再变体 sl_mode='st' 与 "
          "保本=False，\n      候选仍由 IS 选定，OOS 只打分 …")
    top3 = sorted(is_res, key=lambda x: -x[1]["calmar"])[:3]
    specs = []
    for p, m0 in top3:
        specs.append((p, m0, {**p, "sl_mode": "st"}, "sl_mode=st"))
        specs.append((p, m0, {**p, "move_sl_to_entry": False}, "保本=False"))
    var_res = run_batch(OOS[0], OOS[1], [s[2] for s in specs], every=20)
    vmap = {key(p): m for p, m in var_res}
    # 同一组在原 IS 参数下的 OOS 表现，作为对照
    base_oos = {}
    base_res = run_batch(OOS[0], OOS[1], [s[0] for s in specs[::2]], every=20)
    for p, m in base_res:
        base_oos[key(p)] = m

    print(f"\n  {'参数变体':<40}{'笔数':>6}{'OOS净U':>9}{'vs原版':>9}{'回撤%':>8}{'PF':>7}")
    print("  " + "─" * 79)
    for p, m0, q, tag in specs:
        m = vmap.get(key(q))
        if not m:
            continue
        b = base_oos.get(key(p))
        delta = f"{m['net'] - b['net']:+.0f}" if b else "—"
        print(f"  {tag + ' @ ' + desc(p):<40}{m['trades']:>6}{m['net']:>9.0f}"
              f"{delta:>9}{m['dd']:>8.2f}{m['pf']:>7.2f}")

    print("\n" + "=" * 88)
    print("怎么看结果：只认 OOS 列的绝对值。IS 再漂亮、OOS 稳不住 → 过拟合。")
    print("            另外别忘了，同期买入持有 +97.7%。")
    print("=" * 88)


if __name__ == "__main__":
    main()
