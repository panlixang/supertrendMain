# -*- coding: utf-8 -*-
"""量化「赢家 tp1=10% 是不是尾部依赖」：平仓原因拆分 + 收益集中度。

复刻 xval 的赢家/基线，跑 22-26全 与分年，输出：
  - tp1 触发占比 / 止损占比 / 反向信号平仓占比
  - 逐笔 pnl 集中度（top10%/top20% 笔占毛利比例、最大单笔）
直接回答：tp1=10% 是不是「几个月才触发一次、其余全止损」的尾部彩票策略。
口径沿用 bt_1h_exit_opt（1h+V3·ST10/3·费0.05%·fixed100U×10x）。
"""
from __future__ import annotations
import datetime as dt
import sqlite3

import bt_1h_exit_opt as M
from backtest_engine import run_backtest

UTC = dt.timezone.utc


def ts_of(y, m=1, d=1) -> int:
    return int(dt.datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


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


def run(lo, hi, init, params):
    cs = [c for c in M._G["base"] if lo <= c["ts"] < hi]
    return run_backtest(cs, M.ST_P, init_cash=init, fee_rate=M.FEE,
                        allow_short=True, sizing="fixed", margin_usdt=100.0,
                        leverage=10, exit_rules=M.ExitRules(**params),
                        v3_filter=True, gate_tf=M.TF,
                        candles_by_tf={M.TF: cs, "4h": M._G["cbtf4"]},
                        block_if=M.is_weekend_et, full_trades=True)


def main():
    con = sqlite3.connect("candle_data.db")
    end = con.execute(
        "select max(ts) from candles where symbol='BTC-USDT' and tf='15m'"
    ).fetchone()[0]
    con.close()
    M._init_worker()
    WINS = [("2022", ts_of(2022), ts_of(2023)),
            ("2023", ts_of(2023), ts_of(2024)),
            ("2024", ts_of(2024), ts_of(2025)),
            ("2025", ts_of(2025), ts_of(2026)),
            ("2026*", ts_of(2026), end),
            ("22-26全", ts_of(2022), end)]
    for nm, params in CASES:
        print("=" * 104)
        print(f"### {nm}")
        for lbl, lo, hi in WINS:
            init = 5000.0 if lbl == "22-26全" else 1000.0
            r = run(lo, hi, init, params)
            n = r["trades"]
            t1, st, rv = r["tp1_count"], r["stop_count"], r["reverse_count"]
            tr = 100 * t1 / n if n else 0
            sr = 100 * st / n if n else 0
            rr = 100 * rv / n if n else 0
            pnls = sorted((t["pnl"] for t in r["trades_list"]), reverse=True)
            tot = sum(p for p in pnls if p > 0)
            k10 = max(1, int(len(pnls) * 0.1))
            k20 = max(1, int(len(pnls) * 0.2))
            top10 = sum(pnls[:k10])
            top20 = sum(pnls[:k20])
            net = r["final"] - r["init_cash"]
            print(f"  {lbl:<8} 笔{n:>4} 净U{net:>+7.0f} | "
                  f"tp1触发{t1:>3}({tr:>4.1f}%) 止损{st:>3}({sr:>4.1f}%) "
                  f"反向{rv:>3}({rr:>4.1f}%) | "
                  f"top10%笔占毛利{tot and 100*top10/tot:>5.1f}% "
                  f"top20%占{tot and 100*top20/tot:>5.1f}% "
                  f"最大单{pnls[0] if pnls else 0:>+7.0f}U")
        print()


if __name__ == "__main__":
    main()
