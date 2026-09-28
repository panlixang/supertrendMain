# -*- coding: utf-8 -*-
"""
4h 方向：出场「平仓比例」是全项目最被忽视的一阶参数
================================================================================

发现
----
在 4h 上做出场口径对账时，`tp1_ratio`（TP1 触发时平掉多少仓）出现了
**跨资产、跨倍数的单调剂量-反应**：

    tp1_ratio 越大（越早把仓位砍光）→ 收益越差。

生产用的是 `tp1_ratio=70`（+1.5% 就平掉 70%），而这个值在
`{100, 70, 50, 30}` 四档里 **6 个（资产×倍数）格子里每一次都是最差的非零档**。

机制很直白：SuperTrend 的全部价值在于「让利润奔跑」。
在 +1.5% 就把 70% 仓位砍掉，等于用「高胜率、低赔率」替换掉趋势跟踪，
剩下的 30% 又没有足够权重把趋势吃回来。

本脚本把这件事做成交叉验证：
  · 2 个资产（BTC / ETH）× 3 个倍数（3.0/4.0/5.0）× 4 档 tp1_ratio
  · 输出 平均盈利 / 平均亏损 / 盈亏比 —— 用来验证机制，而不是只看总收益
  · 汇总符号一致性：用「符号检验」而不是挑最大 p 值
  · 对推荐配置做逐年拆解

纪律
----
这是**参数扫描**，必须防选择偏差。辩护点不是"某一格 p 小"，而是：
  ① 单调性：四档随 tp1_ratio 单调（不是挑最大值）；
  ② 符号一致：跨 6 格方向一致；
  ③ 机制可解释：avg_win / avg_loss 的比值应当同向变化。
三者同时成立才认。

用法
----
    cd backend
    python sl2_exit_ratio.py
    python sl2_exit_ratio.py --tfs 1h,4h,12h --mults 3,4,5
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import defaultdict

import numpy as np

from backtest_engine import run_backtest
from position import ExitRules
from sl2_riskeval import sign_test
from sl2_tf_sweep import load_tf

_SIMPLE = ExitRules(enabled=False, trail_with_st=False)


def prod(tp1_ratio, tp1=1.5, sl_mode="pct", sl_pct=3.0):
    """生产出场规则，只改 tp1_ratio。"""
    return ExitRules(enabled=True, tp1_pct=tp1, tp1_ratio=tp1_ratio,
                     move_sl_to_entry=True, sl_mode=sl_mode, sl_pct=sl_pct,
                     trail_with_st=True, reverse_close=False)


def main(argv=None):
    ap = argparse.ArgumentParser(description="出场平仓比例（tp1_ratio）交叉验证")
    ap.add_argument("--symbols", default="BTC-USDT,ETH-USDT")
    ap.add_argument("--tfs", default="4h")
    ap.add_argument("--mults", default="3,4,5")
    ap.add_argument("--period", type=int, default=10)
    ap.add_argument("--fee", type=float, default=0.0005)
    ap.add_argument("--ratios", default="100,70,50,30",
                    help="TP1 触发时平掉的仓位%（越小=留得越多）")
    a = ap.parse_args(argv)

    kw = dict(init_cash=10000.0, fee_rate=a.fee, sizing="equity")
    ratios = [float(x) for x in a.ratios.split(",") if x.strip()]
    rows: list[dict] = []

    print("=" * 126)
    print("出场「平仓比例」交叉验证：tp1_pct=1.5% 固定，只改 TP1 触发时平掉多少仓")
    print("=" * 126)
    print("  列含义：剩余 = 100 − 平仓%，就是「留着跟趋势」那条腿的权重")

    for sym in [s.strip() for s in a.symbols.split(",") if s.strip()]:
        for tf in [t.strip() for t in a.tfs.split(",") if t.strip()]:
            for mult in [float(x) for x in a.mults.split(",") if x.strip()]:
                cs = load_tf(sym, tf)
                if len(cs) < 300:
                    continue
                p = {"periods": a.period, "multiplier": mult,
                     "src": "hl2", "change_atr": True}
                years = len(cs) * {"1h": 1, "4h": 4, "12h": 12}.get(tf, 1) / (24 * 365)
                print(f"\n{'#'*126}")
                print(f"### {sym} {tf} × SuperTrend({a.period},{mult}) · {len(cs)} 根 "
                      f"· {years:.2f} 年 · 买入持有基准")
                print(f"{'#'*126}")

                rH = run_backtest(cs, p, **kw, exit_rules=_SIMPLE, full_trades=True)
                print(f"  参照 简化出场（全仓持有到反向翻向）："
                      f"笔数 {rH['trades']} · 胜率 {rH['win_rate']:.1f}% · "
                      f"收益 {rH['return_pct']:+.2f}% · 回撤 {rH['max_dd_pct']:.1f}% · "
                      f"同期买入持有 {rH['hold_pct']:+.1f}%")

                print(f"  {'剩余%':>6}{'笔数':>7}{'笔/周':>7}{'胜率%':>8}"
                      f"{'均盈%':>8}{'均亏%':>8}{'盈亏比':>8}{'收益%':>10}{'回撤%':>8}"
                      f"{'超额pp':>10}{'TP1次':>7}")
                cell = {}
                for ratio in ratios:
                    r = run_backtest(cs, p, **kw, exit_rules=prod(ratio),
                                     full_trades=True)
                    if r.get("error"):
                        continue
                    ride = 100.0 - ratio
                    pf = (r["avg_win"] / abs(r["avg_loss"])
                          if r["avg_loss"] else float("nan"))
                    tag = f"{ride:>6.0f}"
                    mark = ""
                    if ratio == 70.0:
                        mark = "  ← 生产现值"
                    print(f"  {tag}{r['trades']:>7}{r['trades']/years/52:>7.1f}"
                          f"{r['win_rate']:>8.1f}{r['avg_win']:>8.2f}{r['avg_loss']:>8.2f}"
                          f"{pf:>8.2f}{r['return_pct']:>10.2f}{r['max_dd_pct']:>8.1f}"
                          f"{r['alpha_pct']:>10.1f}{r['tp1_count']:>7}{mark}")
                    cell[ratio] = {"ret": r["return_pct"], "pf": pf,
                                   "dd": r["max_dd_pct"], "win": r["win_rate"],
                                   "aw": r["avg_win"], "al": r["avg_loss"]}
                    rows.append({"symbol": sym, "tf": tf, "mult": mult,
                                 "ratio": ratio, "ride": ride,
                                 "trades": r["trades"], "win": r["win_rate"],
                                 "ret": r["return_pct"], "dd": r["max_dd_pct"],
                                 "alpha": r["alpha_pct"], "pf": pf,
                                 "avg_win": r["avg_win"], "avg_loss": r["avg_loss"]})
                # 单调性检查（只看 100→70→50→30 这一序列）
                seq = [cell[x]["ret"] for x in ratios if x in cell]
                if len(seq) >= 3:
                    mono = all(seq[i] <= seq[i + 1] for i in range(len(seq) - 1))
                    print(f"  → 收益随「平仓比例下降」单调上升：{'是' if mono else '否'}"
                          f"（{ ' → '.join(f'{v:+.1f}' for v in seq) }）")

    # ── 跨格汇总 ──
    print("\n" + "=" * 126)
    print("跨格汇总：用符号一致性判，而不是挑某一格的最小 p 值")
    print("=" * 126)
    if rows:
        for ride in sorted({r["ride"] for r in rows}, reverse=True):
            sub = [r for r in rows if r["ride"] == ride]
            print(f"  剩余 {ride:>3.0f}%（平 {100-ride:.0f}%）：{len(sub)} 格 · "
                  f"收益均值 {np.mean([r['ret'] for r in sub]):+.2f}% · "
                  f"回撤均值 {np.mean([r['dd'] for r in sub]):.1f}% · "
                  f"盈亏比均值 {np.nanmean([r['pf'] for r in sub]):.2f}")

        # 关键对照：剩余 50% vs 生产（剩余 30%）
        for ride in (50.0, 70.0):
            pairs = []
            for r in rows:
                if r["ride"] != ride:
                    continue
                base = [x for x in rows if x["symbol"] == r["symbol"]
                        and x["tf"] == r["tf"] and x["mult"] == r["mult"]
                        and x["ride"] == 30.0]
                if base:
                    pairs.append(r["ret"] - base[0]["ret"])
            if pairs:
                pos = sum(1 for d in pairs if d > 0)
                print(f"\n  剩余 {ride:.0f}% vs 生产（剩余 30%）：变好 {pos}/{len(pairs)}"
                      f" · 符号检验 p={sign_test(pos, len(pairs) - pos):.4f}"
                      f" · 平均改善 {np.mean(pairs):+.2f} pp")
        print(f"\n  全部 {len(rows)} 格 · 这是参数扫描，单格 p 值不可直接采信；")
        print(f"  采信依据 = 单调性 + 符号一致性 + 盈亏比同向（三者见上表）。")

    print("\n" + "=" * 126)
    print("判读：")
    print("  · 若「剩余%」升高时 收益↑ 且 盈亏比↑ 且 回撤 基本不变 → 生产 tp1_ratio=70 设错了")
    print("  · 若只在 4h 成立、1h 不成立 → 这是「持有周期够长」才有的效应，与 1h 的结论不冲突")
    print("  · 若 12h 上效应更强 → 说明真正的一阶变量是「让趋势跑」，不是「过滤信号」")
    print("=" * 126)
    return 0


if __name__ == "__main__":
    sys.exit(main())
