# -*- coding: utf-8 -*-
"""弱档(quick)逐笔 MFE / MAE 明细表（03-09 半年窗口）。

对每笔弱档成交，按 gate_tf 的每根 K 线高低点复算：
  MFE 最大有利幅度%： 多单 = (最高价-开仓价)/开仓价； 空单 = (开仓价-最低价)/开仓价
  MAE 最大不利幅度%： 多单 = (开仓价-最低价)/开仓价； 空单 = (最高价-开仓价)/开仓价
  是否触发止损      ： MAE 达到实盘止损距离（sl_pct 1.5 × lev_factor 2.5 = 3.75%）
  tp1 是否先于止损触及：逐根推进，先比较首次触及顺序；同一根内按回测口径
                        「先止损后止盈」处理（同根同时满足算止损）。

做法：把弱档规则设成 tp1=999（永不触发止盈）+ 开启 3.75% 价格止损，
      这样每笔要么打到止损、要么走信号离场，从而观察完整波动空间。
输出：控制台表格 + CSV（_weak_mfe_table.csv）+ JSON。
"""
from __future__ import annotations
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _live_cfg_backtest import _get, trade_cfg, exit_rules  # noqa: E402
from _weak_profile_matrix import (run_sym, LIVE_URLS, CACHE, DATA)  # noqa: E402
from position_enhanced import EnhancedExitRules  # noqa: E402

SYMS = ["BTC", "ETH", "SPCX", "NVDA", "CL"]
TP1_LEVELS = [1.0, 1.2, 1.3, 1.5, 1.8, 2.0, 2.5, 3.0]
# 实盘弱档止损：sl_pct=1.5, 杠杆10x -> lev_factor=2.5 -> 3.75% 价格距离
STOP_PCT = 1.5 * 2.5
# 统计窗口 03-01 ~ 09-30（按数据实际范围再收敛）
WIN_START = datetime(2026, 3, 1, tzinfo=timezone.utc)
WIN_END = datetime(2026, 9, 30, tzinfo=timezone.utc)


def quick_no_tp(stop_pct):
    """弱档规则：止盈永不触发，只留止损与信号离场。"""
    return EnhancedExitRules(
        enabled=True,
        tp1_pct=999.0, tp1_ratio=100.0,
        tp2_pct=999.0, tp2_ratio=0.0,
        tp3_pct=999.0, tp3_ratio=0.0, tp3_mode="pct",
        move_sl_to_entry=False, trail_with_st=False,
        sl_mode="pct", sl_pct=stop_pct, sl_buffer_atr=0.3, sl_min_pct=stop_pct,
        protect_profit_at=999.0, protect_trail_pct=0.0,
        max_loss_enabled=False, max_loss_pct=8.0,
    )


def dstr(ts):
    return datetime.fromtimestamp(ts / 1000, timezone.utc).strftime("%m-%d %H:%M")


def analyze(candles, ts_idx, side_long, entry, i0, i1, levels, stop_pct):
    """逐根推进，算 MFE/MAE 与『tp 与止损谁先到』。"""
    max_fav = 0.0
    max_adv = 0.0
    first_fav = {x: None for x in levels}
    first_stop = None
    for i in range(i0 + 1, i1 + 1):
        c = candles[i]
        hi, lo = float(c["h"]), float(c["l"])
        if side_long:
            fav = (hi - entry) / entry * 100
            adv = (entry - lo) / entry * 100
        else:
            fav = (entry - lo) / entry * 100
            adv = (hi - entry) / entry * 100
        max_fav = max(max_fav, fav)
        max_adv = max(max_adv, adv)
        for x in levels:
            if first_fav[x] is None and fav >= x:
                first_fav[x] = i
        if first_stop is None and adv >= stop_pct:
            first_stop = i
    reach = {}
    for x in levels:
        fi = first_fav[x]
        # 同根内按回测口径「先止损后止盈」：止损优先
        reach[x] = fi is not None and (first_stop is None or fi < first_stop)
    return max_fav, max_adv, first_stop is not None, reach


def main():
    t0 = time.time()
    sym_map: dict[str, dict] = {}
    for url in LIVE_URLS:
        try:
            d = _get(url)
            for x in d.get("symbols", []):
                sym_map.setdefault(
                    x["symbol"].replace("-USDT-SWAP", "").replace("-USDT", ""), x)
        except Exception:
            continue

    print(f"弱档止损距离 = 配置 sl_pct 1.5 × lev_factor(10x) 2.5 = {STOP_PCT}% 价格")
    print(f"统计窗口: {WIN_START:%Y-%m-%d} ~ {WIN_END:%Y-%m-%d}\n")

    all_rows = []
    for name in SYMS:
        if name not in sym_map or name not in CACHE:
            print(f"{name}: 缺失, 跳过"); continue
        sym = sym_map[name]
        cache = os.path.join(DATA, CACHE[name])
        if not os.path.exists(cache):
            print(f"{name}: 无缓存, 跳过"); continue
        cbtf = {k: v for k, v in json.load(open(cache, encoding="utf-8")).items()
                if isinstance(v, list) and v and "ts" in (v[0] or {})}
        gate_tf = sym["allow_tfs"][0]
        candles = cbtf.get(gate_tf)
        if not candles:
            print(f"{name}: 无 {gate_tf} 数据, 跳过"); continue
        d0 = datetime.fromtimestamp(candles[0]["ts"] / 1000, timezone.utc)
        d1 = datetime.fromtimestamp(candles[-1]["ts"] / 1000, timezone.utc)
        print(f"===== {name} [{gate_tf}] 数据 {d0:%Y-%m-%d} ~ {d1:%Y-%m-%d} "
              f"共 {len(candles)} 根 =====")

        cfg = trade_cfg(sym)
        normal = exit_rules(sym)
        res = run_sym(sym, cbtf, cfg, normal, quick_no_tp(STOP_PCT))
        if res is None or "error" in res:
            print("  回测失败"); continue
        tl = res.get("trades_list") or res.get("trade_list") or []
        ts_idx = {c["ts"]: i for i, c in enumerate(candles)}

        rows = []
        for t in tl:
            if t.get("profile") != "quick":
                continue
            e_dt = datetime.fromtimestamp(t["entry_ts"] / 1000, timezone.utc)
            if not (WIN_START <= e_dt <= WIN_END):
                continue
            i0 = ts_idx.get(t["entry_ts"])
            i1 = ts_idx.get(t["exit_ts"])
            if i0 is None or i1 is None:
                continue
            long_ = t["side"] == "long"
            entry = float(t["entry"])
            mfe, mae, stopped, reach = analyze(
                candles, ts_idx, long_, entry, i0, i1, TP1_LEVELS, STOP_PCT)
            rows.append({
                "symbol": name, "side": "多" if long_ else "空",
                "entry_dt": e_dt, "entry": entry,
                "mfe": mfe, "mae": mae, "stopped": stopped,
                "reach": reach, "pnl": t["pnl_pct"], "reason": t.get("reason", ""),
                "bars": t.get("bars", 0),
            })

        if not rows:
            print("  窗口内无弱档成交\n"); continue

        print(f"  {'方向':<4s}{'入场':<12s}{'最大有利%':>10s}{'最大不利%':>10s}"
              f"{'止损?':>7s}{'结果%':>9s}  {'各tp1是否先于止损触及'}")
        hdr = "  " + " " * 4 + " " * 12 + " " * 10 + " " * 10 + " " * 7 + " " * 9 + "  " \
              + " ".join(f"{x:g}" for x in TP1_LEVELS)
        print(hdr)
        for r in rows:
            flags = " ".join((" Y" if r["reach"][x] else " -") for x in TP1_LEVELS)
            print(f"  {r['side']:<4s}{dstr(r['entry_dt'].timestamp()*1000):<12s}"
                  f"{r['mfe']:>10.2f}{r['mae']:>10.2f}"
                  f"{('是' if r['stopped'] else '否'):>7s}{r['pnl']:>9.2f}  {flags}")

        n = len(rows)
        nstop = sum(1 for r in rows if r["stopped"])
        mf = sorted(r["mfe"] for r in rows)
        print(f"  --- 弱档 {n} 笔 | 触发止损 {nstop} 笔 ({nstop/n*100:.0f}%) "
              f"| MFE 中位 {mf[n//2]:.2f}% 均值 {sum(mf)/n:.2f}% 最大 {mf[-1]:.2f}%")
        cnt = {x: sum(1 for r in rows if r["reach"][x]) for x in TP1_LEVELS}
        print("  --- 先于止损触及 tp1 的笔数: "
              + "  ".join(f"{x:g}%:{cnt[x]}({cnt[x]/n*100:.0f}%)" for x in TP1_LEVELS))
        print()
        all_rows.extend(rows)

    # CSV
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "_weak_mfe_table.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["品种", "方向", "入场时间", "开仓价", "最大有利幅度%", "最大不利幅度%",
                    "是否触发止损"] + [f"tp1_{x:g}%先于止损" for x in TP1_LEVELS]
                   + ["本笔结果%", "离场原因", "持仓根数"])
        for r in all_rows:
            w.writerow([r["symbol"], r["side"], r["entry_dt"].strftime("%Y-%m-%d %H:%M"),
                        round(r["entry"], 6), round(r["mfe"], 2), round(r["mae"], 2),
                        "是" if r["stopped"] else "否"]
                       + ["是" if r["reach"][x] else "否" for x in TP1_LEVELS]
                       + [r["pnl"], r["reason"], r["bars"]])
    print(f"共 {len(all_rows)} 笔 | Wrote {csv_path}")
    print(f"耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
