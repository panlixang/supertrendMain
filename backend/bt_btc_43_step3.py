# -*- coding: utf-8 -*-
"""STEP 3：候选方案的分段验证（防止又一次给出过拟合单点）。

STEP 2 在空间上加出了「tp1 越小越好」的单调性，但那可能只是把参数往
「持仓更久」这个方向推（简化出场 +94.24% 是这条路的终点）。
要判断能不能信，必须看它在**不参与挑选的时间段**里是否也成立：
上半段 vs 下半段分开跑，两边都得赢才算数。

也顺手验 reverse_close —— 这是面板现成开关，改了直接等于「持有到翻向」。
"""
from __future__ import annotations

import datetime as dt

from backtest_engine import run_backtest
from position import ExitRules
from sl2_tf_sweep import load_tf

FEE = 0.0005
FIXED = dict(sizing="fixed", margin_usdt=100.0, leverage=10, fee_rate=FEE)


def prod(tp1=1.5, ratio=70.0, sl_mode="pct", sl_pct=3.0, reverse=False):
    return ExitRules(enabled=True, tp1_pct=tp1, tp1_ratio=ratio,
                     move_sl_to_entry=True, sl_mode=sl_mode, sl_pct=sl_pct,
                     trail_with_st=True, reverse_close=reverse)


def seg(cs, lo_ms, hi_ms):
    return [c for c in cs if lo_ms <= c["ts"] <= hi_ms]


def run_it(cs, tag, rules, allow_short=True):
    p = {"periods": 10, "multiplier": 3.0, "src": "hl2", "change_atr": True}
    out = []
    for label, part in cs.items():
        if len(part) < 300:
            out.append((label, None))
            continue
        out.append((label, run_backtest(part, p, init_cash=1000.0, **FIXED,
                                        allow_short=allow_short,
                                        exit_rules=rules)))
    cells = []
    for label, r in out:
        if not r or r.get("error"):
            cells.append(f"  {label}  {(r or {}).get('error', '无结果')}")
            continue
        cells.append(f"  {label}  {r['trades']:>4}笔 胜率{r['win_rate']:>5.1f}% "
                     f"收益{r['return_pct']:>8.2f}% 回撤{r['max_dd_pct']:>6.1f}% "
                     f"爆仓{r['liq_count']:>3}")
    line = f"{tag:<26}" + cells[0].strip()
    print(line)
    for c in cells[1:]:
        print(f"{'':<26}{c.strip()}")


def main():
    cs = load_tf("BTC-USDT", "4h")
    mid = len(cs) // 2
    mid_ts = cs[mid]["ts"]
    halves = {
        "上半": seg(cs, cs[0]["ts"], mid_ts),
        "下半": seg(cs, mid_ts, cs[-1]["ts"]),
        "全程": cs,
    }
    print(f"数据 BTC-USDT 4h {len(cs)} 根｜切点 "
          f"{dt.datetime.fromtimestamp(mid_ts/1000, dt.timezone.utc):%Y-%m-%d}"
          f"（前半 {len(halves['上半'])} 根 / 后半 {len(halves['下半'])} 根）")
    print(f"仓位口径 fixed 100U×10x / 本金 1000U / 费率 {FEE*100:.2f}%/边\n")
    print("  " + "─" * 74)

    CANDS = [
        ("① 线上现值 4.0/50", dict(tp1=4.0, ratio=50.0)),
        ("② 2.5 / 50", dict(tp1=2.5, ratio=50.0)),
        ("③ 1.5 / 20", dict(tp1=1.5, ratio=20.0)),
        ("④ 1.0 / 30", dict(tp1=1.0, ratio=30.0)),
        ("⑤ 1.5 / 70（旧默认）", dict(tp1=1.5, ratio=70.0)),
    ]
    for tag, kw in CANDS:
        run_it(halves, tag, prod(**kw))
        print("  " + "─" * 74)

    print("\n【面板现成开关：reverse_close=true ⇒ 只按反向信号平仓，TP/保本/跟踪全失效】")
    run_it(halves, "⑥ reverse_close=true", prod(reverse=True))
    print("  " + "─" * 74)
    print("\n【毛上限参照：出场全关，翻向反手】")
    run_it(halves, "⑦ 简化出场", ExitRules(enabled=False, trail_with_st=False))
    print("  " + "─" * 74)


if __name__ == "__main__":
    main()
