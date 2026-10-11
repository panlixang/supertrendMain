# -*- coding: utf-8 -*-
"""43 形态识别页策略 vs 47 首页策略 —— 同数据(本地缓存)对比。

形态识别页策略(bt_pattern_page)：ST(10,3.0) 翻转 + 4h 形态方向对齐(策略B) /
V3 过滤(策略C) + 单档 TP(1.5%/70%) + 保本 + 跟随ST/反向信号离场。
复用 47 回测那套本地缓存 1h/4h K 线，窗口与 backtest_stock_47 / mu_47 / cl_47 完全一致。

为与 47 首页(embedded 10x 杠杆 + 复利)同口径，形态页每笔 ret_pct 放大 10x 后顺序复利，
并据同一杠杆曲线算最大回撤。原生(无杠杆、Σ单笔%)也一并给出。
"""
import json
import os
import sys
import datetime as dt
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bt_pattern_page as P

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data")
UTC = ZoneInfo("UTC")
LEV = 10  # 对齐 47 首页 leverage=10


def ts(y, m, d, h=0, mi=0):
    return int(dt.datetime(y, m, d, h, mi, tzinfo=UTC).timestamp() * 1000)


def load(sym_file):
    d = json.load(open(os.path.join(DATA, sym_file), encoding="utf-8"))
    return d["1h"], d["4h"]


def windows_for(name, base):
    if name in ("NVDA", "SKHYNIX"):
        n = len(base)
        mid = n // 2
        mid_ts = base[mid]["ts"]
        lo = base[0]["ts"]
        hi = base[-1]["ts"]
        return [("前半", lo, mid_ts), ("后半", mid_ts, hi), ("整体", lo, hi)]
    if name == "MU":
        return [("前3月", ts(2026, 4, 1), ts(2026, 6, 30, 23, 59)),
                ("近3月", ts(2026, 7, 1), ts(2026, 9, 15, 23, 59)),
                ("整体", base[0]["ts"], ts(2026, 9, 15, 23, 59))]
    if name == "CL":
        return [("前半", ts(2026, 3, 4), ts(2026, 5, 31, 23, 59)),
                ("后半", ts(2026, 6, 1), ts(2026, 8, 31, 23, 59)),
                ("整体", ts(2026, 3, 4), ts(2026, 8, 31, 23, 59))]


def comp_levered(tr, lev=LEV):
    eq = 100.0
    for t in tr:
        eq *= (1 + (t["ret_pct"] * lev) / 100)
    return eq - 100


def maxdd(tr, lev=LEV):
    eq = 100.0
    peak = 100.0
    m = 0.0
    for t in tr:
        eq *= (1 + (t["ret_pct"] * lev) / 100)
        peak = max(peak, eq)
        m = max(m, (peak - eq) / peak * 100)
    return m


def run_symbol(name, sym_file):
    base, h4 = load(sym_file)
    sigs, opens, highs, lows, closes, up_plot, dn_plot, flip_idx = P.build_signals(base, h4)
    print(f"\n===== {name} 形态识别页 (ST10/3 + 4h对齐 + 单档TP) =====")
    print(f"  信号总数={len(sigs)}  1h {dt.datetime.fromtimestamp(base[0]['ts']/1000,UTC):%Y-%m-%d}"
          f"~{dt.datetime.fromtimestamp(base[-1]['ts']/1000,UTC):%Y-%m-%d} n={len(base)}")
    wins = {}
    for label, lo, hi in windows_for(name, base):
        win = [s for s in sigs if lo <= s["ts"] < hi]
        if not win:
            print(f"  [{label}] 无信号，跳过")
            continue
        rows = {}
        for key, _, ok in P.STRATS:
            sub = [s for s in win if ok(s)]
            tr = P.backtest(sub, highs, lows, closes, up_plot, dn_plot, flip_idx)
            m = P.metrics(tr)
            rows[key] = (m, comp_levered(tr), maxdd(tr))
        b = rows["B"]  # 页面默认：4h 形态对齐
        c = rows["C"]  # V3 过滤
        print(f"  [{label}] B(4h对齐): 收益={b[1]:.1f}%(10x复利) / 原生Σ={b[0]['ret']:.1f}% "
              f"PF比={b[0]['payoff']:.2f} 笔={b[0]['n']} 胜率={b[0]['wr']:.1f}% 回撤={b[2]:.1f}%")
        print(f"           C(V3过滤): 收益={c[1]:.1f}%(10x复利) / 原生Σ={c[0]['ret']:.1f}% "
              f"PF比={c[0]['payoff']:.2f} 笔={c[0]['n']} 胜率={c[0]['wr']:.1f}% 回撤={c[2]:.1f}%")
        wins[label] = (b, c)
    return wins


def main():
    for name, f in [("NVDA", "NVDA.json"), ("SKHYNIX", "SKHYNIX.json"),
                    ("MU", "MU.json"), ("CL", "cl_half_cache.json")]:
        run_symbol(name, f)


if __name__ == "__main__":
    main()
