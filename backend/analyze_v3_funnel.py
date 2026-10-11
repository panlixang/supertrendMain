"""V3 过滤网在 NVDA/SNDK/TSLA 上的放/拦漏斗 + 各子集收益对照。

复用 bt_pattern_page 的信号与回测逻辑，把同一堆原始翻转信号按
「4h 形态过滤(B)」与「V3 趋势打分(C)」两套闸门拆开，看 V3 到底
放了多少、拦了多少、准不准。

窗口取全样本（美股永续历史仅 2026-03 起，等价于之前"近6月"行）。
"""
import os, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_pattern_page as bt
from signal_v3 import PATH_MATURE, PATH_EARLY, PATH_SLOW, PATH_FUSE, PATH_NONE

SYMS = ["NVDA-USDT-SWAP", "SNDK-USDT-SWAP", "TSLA-USDT-SWAP"]


def analyze(sym):
    base, h4 = bt.load(sym)
    sigs, opens, highs, lows, closes, up_plot, dn_plot, flip_idx = bt.build_signals(base, h4)
    tss = [c["ts"] for c in base]
    start, end = tss[0], tss[-1] + 1          # 全样本
    win = [s for s in sigs if start <= s["ts"] < end]

    print(f"\n{'='*78}\n  {sym}   全样本 {bt.fmt(tss[0])} ~ {bt.fmt(tss[-1])}   "
          f"原始翻转信号 = {len(win)} 笔\n{'='*78}")

    print("  4h 形态方向分布:", dict(Counter(s["pdir"] for s in win)))
    print("  V3 路径分布     :", dict(Counter(s["v3_path"] for s in win)))

    def sub(name, pred):
        tr = bt.backtest([s for s in win if pred(s)], highs, lows, closes,
                         up_plot, dn_plot, flip_idx)
        if not tr:
            print(f"    {name:<26} n=   0")
            return
        m = bt.metrics(tr)
        print(f"    {name:<26} n={m['n']:>4}  胜率={m['wr']:>5.1f}%  "
              f"收益={m['ret']:>+7.2f}%  回撤={m['max_dd']:>6.2f}%")

    print("  ── 闸门 / 子集对照 ──")
    sub("ALL  全部翻转(A)", lambda s: True)
    sub("B    仅4h对齐",            lambda s: s["pass_4h"])
    sub("C    V3放行",              lambda s: s["v3_execute"])
    sub("B∩C  4h对齐且V3放行",      lambda s: s["pass_4h"] and s["v3_execute"])
    sub("Bonly 4h对齐但被V3拦掉",   lambda s: s["pass_4h"] and not s["v3_execute"])
    sub("Conly V3放行但4h没对齐",   lambda s: s["v3_execute"] and not s["pass_4h"])

    print("  ── V3 各路径自身收益（看路径准不准）──")
    for p, lab in [(PATH_MATURE, "成熟 trend(score=100)"),
                   (PATH_EARLY, "早期启动(score=80)"),
                   (PATH_SLOW, "慢热接住(score=60)"),
                   (PATH_NONE, "未通过(被拦)")]:
        tr = bt.backtest([s for s in win if s["v3_path"] == p], highs, lows, closes,
                         up_plot, dn_plot, flip_idx)
        if not tr:
            print(f"    {lab:<22} n=   0")
            continue
        m = bt.metrics(tr)
        print(f"    {lab:<22} n={m['n']:>4}  胜率={m['wr']:>5.1f}%  "
              f"收益={m['ret']:>+7.2f}%  回撤={m['max_dd']:>6.2f}%")


if __name__ == "__main__":
    for s in SYMS:
        analyze(s)
