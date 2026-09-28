# -*- coding: utf-8 -*-
"""
① ER 分档出场回测：弱趋势档走「快进快出」、趋势档走「吃波段」
================================================================================

为什么这件事值得做
------------------
① 生产里这套框架**已经接着但关着**：
   `regime.py` 的 `classify` 会把 ER ∈ [er_weak_min, er_min) 判为 "edge"、profile="quick"，
   生产默认 `er_weak_min=0.12`、`er_min=0.15`，但 **`quick_enabled` 默认 False**。
   `backtest_engine.run_backtest` 也支持 `er_weak_min + exit_rules_quick`，
   可是**没有任何研究脚本传过**，`sweep_er()` 只扫闸门阈值、不扫分档出场。

② 有直接先验证据：同一信号换出场口径 →
   紧止盈(1.5%/70%) 在**震荡年 2022** 净期望 +0.140%/笔、胜率 64.3%；
   趋势跟踪在**趋势年 2023/2026** 更优。两套出场各赢一半年份且方向相反。

必须有的对照（否则会自欺）
--------------------------
打开弱档会**同时**带来两件事：
  (a) 多开了一批原本被拦掉的 ER∈[弱档下界, er_min) 的信号；
  (b) 这批信号改用了另一套出场规则。
只报"开弱档后变好"，无法区分是 (a) 还是 (b) 的功劳。所以：

  · **对照 B（关键）**：两档都开，但 quick 用与 normal **完全相同**的规则
    → 收益差 = 纯粹「多开单」的贡献。
  · **实验 C**：两档都开，quick 用真正的「快进快出」规则
    → C − B = **差异化出场** 的真正贡献。这才是本脚本要测的东西。

用法
----
    cd backend
    python sl2_exitregime.py
    python sl2_exitregime.py --symbols BTC-USDT,ETH-USDT --er-mins 0.15,0.20
"""
from __future__ import annotations

import argparse
import sys

from backtest_engine import run_backtest
from position import ExitRules
from sl2_tf_sweep import load_tf

_SIMPLE = ExitRules(enabled=False, trail_with_st=False)

# 生产「吃波段」档（pattern_trade.json 公共部分；止损来源按品种可换）
NORMAL = dict(enabled=True, tp1_pct=1.5, tp1_ratio=70.0,
              move_sl_to_entry=True, sl_mode="pct", sl_pct=3.0,
              trail_with_st=True, reverse_close=False)
# regime.py 文档写的「快进快出」档：0.8% 全平 / 1% 固定止损 / 不跟随
QUICK = dict(enabled=True, tp1_pct=0.8, tp1_ratio=100.0,
             move_sl_to_entry=False, sl_mode="pct", sl_pct=1.0,
             trail_with_st=False, reverse_close=False)


def R(**kw):
    d = dict(NORMAL)
    d.update(kw)
    return ExitRules(**d)


def show(tag, r, note=""):
    if not r or r.get("error"):
        print(f"  {tag:<44}{r.get('error') if r else '无结果'}")
        return None
    print(f"  {tag:<44}{r['trades']:>6}{r['quick_trades']:>7}{r['win_rate']:>8.1f}"
          f"{r['return_pct']:>10.2f}{r['max_dd_pct']:>8.1f}{r['alpha_pct']:>10.1f}"
          f"{r['avg_bars']:>8.1f}   {note}")
    return r


def header():
    print(f"  {'配置':<44}{'笔数':>6}{'弱档':>7}{'胜率%':>8}"
          f"{'收益%':>10}{'回撤%':>8}{'超额pp':>10}{'均持仓':>8}")


def run_symbol(sym, tf, p, fee, er_mins, er_weaks, kw):
    cs = load_tf(sym, tf)
    if len(cs) < 500:
        print(f"\n[{sym}] 数据不足（{len(cs)} 根），跳过")
        return
    print(f"\n{'#'*122}\n### {sym} {tf} · {len(cs)} 根\n{'#'*122}")

    # ── 参照组 ──
    header()
    rS = show("参照0：简化出场（下一反向翻向全平）",
              run_backtest(cs, p, **kw, exit_rules=_SIMPLE), "口径对照，非生产")
    r0 = show("参照1：生产原样（无 ER 闸门，单档 normal）",
              run_backtest(cs, p, **kw, exit_rules=R()), "= pattern_trade.json")

    # ── 单档：只用 er_min 做闸门（关弱档）──
    print("\n  ── 组A：单档 normal + er_min 闸门（拦掉低 ER，不开弱档）──")
    header()
    singles = {}
    for emin in er_mins:
        r = run_backtest(cs, p, **kw, exit_rules=R(), er_min=emin)
        singles[emin] = r
        show(f"A er_min={emin:.2f}（<{emin} 全拦）", r,
             f"拦掉 {r['er_blocked'] if r else 0} 次信号" if r else "")

    # ── 双档：开弱档 ──
    for emin in er_mins:
        for ew in er_weaks:
            if ew >= emin:
                continue
            print(f"\n  ── 组B/C：er_min={emin:.2f} · er_weak_min={ew:.2f}"
                  f"（弱档 band = [{ew:.2f}, {emin:.2f})）──")
            header()
            # 对照 B：quick 用和 normal 一样的规则 → 纯度 = 「多开单」
            rB = show("B 对照：quick 规则 == normal 规则（纯度=多开单）",
                      run_backtest(cs, p, **kw, exit_rules=R(),
                                   exit_rules_quick=R(),
                                   er_min=emin, er_weak_min=ew))
            # 实验 C：quick 用真正的快进快出
            rC = show("C 实验：quick = 0.8%全平/1%止损/不跟随",
                      run_backtest(cs, p, **kw, exit_rules=R(),
                                   exit_rules_quick=R(**QUICK),
                                   er_min=emin, er_weak_min=ew))
            if rB and rC and not rB.get("error") and not rC.get("error"):
                d = rC["return_pct"] - rB["return_pct"]
                qn = rC.get("quick_trades", 0)
                print(f"      → 差异化出场的净贡献 C − B = {d:+.2f} pp"
                      f"（弱档 {qn} 笔，占 {qn/max(1,rC['trades'])*100:.0f}%）"
                      + ("  ✅ 差异化出场有价值" if d > 0 else "  ❌ 差异化出场无价值/有害"))
                if r0 and not r0.get("error"):
                    print(f"      → 相对生产原样 {rC['return_pct'] - r0['return_pct']:+.2f} pp")


def main(argv=None):
    ap = argparse.ArgumentParser(description="ER 分档出场回测")
    ap.add_argument("--symbols", default="BTC-USDT,ETH-USDT")
    ap.add_argument("--tf", default="1h")
    ap.add_argument("--period", type=int, default=10)
    ap.add_argument("--mult", type=float, default=3.0)
    ap.add_argument("--fee", type=float, default=0.0005)
    ap.add_argument("--er-mins", default="0.15,0.20,0.25")
    ap.add_argument("--er-weaks", default="0.06,0.08,0.10,0.12")
    a = ap.parse_args(argv)

    p = {"periods": a.period, "multiplier": a.mult, "src": "hl2", "change_atr": True}
    kw = dict(init_cash=10000.0, fee_rate=a.fee, sizing="equity")
    er_mins = [float(x) for x in a.er_mins.split(",") if x.strip()]
    er_weaks = [float(x) for x in a.er_weaks.split(",") if x.strip()]

    print("=" * 122)
    print("① ER 分档出场：弱趋势档「快进快出」 vs 趋势档「吃波段」")
    print("=" * 122)
    print(f"SuperTrend({a.period},{a.mult}) · {a.tf} · 费率 {a.fee*100:.2f}%/边 · equity 满仓复利")
    print(f"normal 档 = tp1 1.5%/平70% + 保本 + ST跟踪（生产）")
    print(f"quick  档 = tp1 0.8%/全平 + 1% 固定止损 + 不跟随（regime.py 文档口径）")
    print(f"关键对照：B = quick 用 normal 规则（纯度=多开单）｜ C−B = 差异化出场的净贡献")

    for sym in [s.strip() for s in a.symbols.split(",") if s.strip()]:
        run_symbol(sym, a.tf, p, a.fee, er_mins, er_weaks, kw)

    print("\n" + "=" * 122)
    print("判读：")
    print("  · 若 C − B ≤ 0 → 「按 ER 分档切出场」没有价值，弱档多开的单只是噪声（甚至更差）")
    print("  · 若 C − B > 0 且 C > 参照1 → 分档出场是真收益，值得在生产里打开 quick_enabled")
    print("  · 若 B ≫ 参照1 而 C ≈ B → 价值来自「多开低 ER 的单」而非差异化出场，说明")
    print("    闸门阈值本身设错了（把 er_min 调低即可，不需要两套规则）")
    print("=" * 122)
    return 0


if __name__ == "__main__":
    sys.exit(main())
