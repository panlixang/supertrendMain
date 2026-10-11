# -*- coding: utf-8 -*-
"""SPCX 形态识别页 + V3 + 1h 出场网格寻优（固定 100U / 1x / 不复利口径）。

信号：bt_pattern_page.build_signals —— 1h SuperTrend(10,3.0) 翻转 + 4h 形态上下文
      + V3 趋势闸门（filter_v3，即策略 C）。
数据：OKX SPCX-USDT-SWAP 实时拉取（1h/4h，合约 2026-06-07 上线，样本约 4 个月）。
寻优：tp1_pct × tp1_ratio × sl_mode(sl_pct) × trail_with_st，按 Calmar=收益%/回撤% 排序。
      同时给出 笔数 / 胜率 / PF / 均盈均亏 / 收益(U) / 最大回撤(U)。

⚠️ SPCX 样本仅 ~4 个月（约 3000 根 1h），寻优结论过拟合风险极高，仅作参考，
   严禁直接照搬最优参数上线。

用法：
    python bt_spcx_optimize.py            # 基准 + 寻优 Top
    python bt_spcx_optimize.py --long-only
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import history                                   # noqa: E402
from bt_pattern_page import build_signals         # noqa: E402
from bt_2026_43_47 import backtest, metrics, NOTIONAL, FEE, SL_FALLBACK  # noqa: E402

SYM = "SPCX-USDT-SWAP"
DATA = os.path.join(HERE, "_bt2026_data", SYM.replace("-", "_") + ".json")
UTC = dt.timezone.utc


def f(ms):
    return dt.datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M")


def load(force=False):
    if (not force) and os.path.exists(DATA):
        d = json.load(open(DATA, encoding="utf-8"))
        return d["1h"], d["4h"]
    b = history.fetch_candles("1h", limit=6000, symbol=SYM)
    h = history.fetch_candles("4h", limit=2000, symbol=SYM)
    if not b or not h:
        raise SystemExit("SPCX OKX 行情拉取失败")
    d = {"1h": [dict(ts=c.ts, o=c.o, h=c.h, l=c.l, c=c.c, vol=c.vol) for c in b],
         "4h": [dict(ts=c.ts, o=c.o, h=c.h, l=c.l, c=c.c, vol=c.vol) for c in h]}
    json.dump(d, open(DATA, "w", encoding="utf-8"))
    return d["1h"], d["4h"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--long-only", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()

    base, h4 = load(a.refresh)
    print(f"{SYM} 1h {len(base)} 根 {f(base[0]['ts'])}~{f(base[-1]['ts'])}  "
          f"4h {len(h4)} 根 {f(h4[0]['ts'])}~{f(h4[-1]['ts'])}")

    sigs, opens, highs, lows, closes, up_plot, dn_plot, flip_idx = build_signals(base, h4)
    c = [s for s in sigs if s.get("v3_execute")]
    v = [s for s in c if s["dir"] > 0]
    target = v if a.long_only else c
    tag = "V(仅多)" if a.long_only else "C(V3)"
    print(f"信号: 全部翻转 {len(sigs)} | V3放行(C) {len(c)} | V3+仅多(V) {len(v)}")
    print(f"回测目标 {tag}：{len(target)} 笔信号  （固定 {NOTIONAL:.0f}U/笔、1x、不复利、单边费 {FEE*100:.2f}%）")

    # 基准：形态识别页通用默认档（tp1=1.5/70 sl=st/2 trail=on 保本on）
    base_cfg = dict(tp1_pct=1.5, tp1_ratio=70.0, sl_mode="st", sl_pct=SL_FALLBACK,
                    move_sl_to_entry=True, trail_with_st=True, reverse_close=False)
    b = metrics(backtest(target, highs, lows, closes, up_plot, dn_plot, flip_idx, base_cfg))
    print(f"\n== 基准(页面默认档 tp1=1.5/70 sl=st/2 trail=on) ==\n"
          f"   笔数 {b['n']}  胜率 {b['wr']:.1f}%  PF {b['pf']:.2f}  均盈/均亏 {b['payoff']:.2f}  "
          f"收益 {b['tot']:+.2f}U  回撤 {b['dd']:.2f}U  Calmar {b['tot']/b['dd']:.2f}")

    # 网格
    tp1_pcts = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0]
    ratios = [30.0, 50.0, 70.0, 100.0]
    sl_cfgs = [("pct", 3.0), ("st", 2.0)]
    trails = [True, False]

    rows = []
    total = len(tp1_pcts) * len(ratios) * len(sl_cfgs) * len(trails)
    k = 0
    for p in tp1_pcts:
        for r in ratios:
            for m, sp in sl_cfgs:
                for tr in trails:
                    k += 1
                    cfg = dict(tp1_pct=p, tp1_ratio=r, sl_mode=m, sl_pct=sp,
                               move_sl_to_entry=True, trail_with_st=tr, reverse_close=False)
                    m_ = metrics(backtest(target, highs, lows, closes, up_plot, dn_plot,
                                          flip_idx, cfg))
                    cal = m_["tot"] / m_["dd"] if m_["dd"] > 0 else 0.0
                    rows.append((cal, m_["tot"], m_["dd"], m_["pf"], m_["payoff"],
                                 m_["n"], m_["wr"], m_["avg"], p, r, m, sp, tr))

    def sls(m, sp):
        return f"{m}/{sp:.0f}" if m == "pct" else f"st({sp:.0f})"

    rows.sort(reverse=True, key=lambda x: x[0])
    print(f"\n=== {tag} 出场寻优 Top15（按 Calmar=收益%/回撤%，共 {total} 组合）===")
    print(f"{'Calmar':>6} {'收益U':>8} {'回撤U':>7} {'PF':>6} {'均盈/亏':>8} "
          f"{'笔':>4} {'胜率%':>6} {'单笔%':>7}  tp1%  ratio  sl      trail")
    for cal, tot, dd, pf, po, n, wr, avg, p, r, m, sp, tr in rows[:15]:
        pfs = "inf" if pf == float("inf") else f"{pf:.2f}"
        pos = "inf" if po == float("inf") else f"{po:.2f}"
        print(f"{cal:6.2f} {tot:>+8.2f} {dd:>7.2f} {pfs:>6} {pos:>8} "
              f"{n:>4} {wr:>6.1f} {avg:>+7.3f}  {p:4.1f}  {r:3.0f}  {sls(m,sp):>7}  {tr}")

    # 另看 Top 收益
    rows2 = sorted(rows, key=lambda x: x[1], reverse=True)
    print(f"\n=== {tag} 收益 Top5（仅看绝对收益，过拟合敏感）===")
    for cal, tot, dd, pf, po, n, wr, avg, p, r, m, sp, tr in rows2[:5]:
        print(f"   收益 {tot:+.2f}U 回撤 {dd:.2f}U Calmar {cal:.2f} "
              f"tp1={p}/{r:.0f} sl={sls(m,sp)} trail={tr}")

    print("\n[警告] SPCX 样本仅 ~5 个月（约 3700 根 1h），上述最优组合极可能过拟合，"
          "仅供方向参考；实盘前请用更长样本或实盘灰度验证。")


if __name__ == "__main__":
    main()
