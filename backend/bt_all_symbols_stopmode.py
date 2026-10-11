# -*- coding: utf-8 -*-
"""对 43 上每个品种，用 V3-C 信号在固定 tp1=2/50 下比较两种止损方式：
   M_pct = pct/3 + trail=False   (固定封顶，不跟趋势)
   M_st  = st/2  + trail=True    (趋势感知跟踪)
并叠加该品种 43 当前配置做对照。
数据来自 backtest/_live_data 缓存（OKX 离线，TSLA/INTC 无缓存跳过）。
"""
from __future__ import annotations
import json, os, sys, datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bt_pattern_page import build_signals
from bt_sndk_pg_v3_exitopt import backtest   # 复用参数化回测

CACHE_DIR = "backtest/_live_data"
INIT = 1000.0
FEE = 0.05 / 100
TP1_PCT, TP1_RATIO = 2.0, 50.0   # 对比时固定，隔离"止损方式"单变量


def load_any(sym):
    base = h4 = None
    p = os.path.join(CACHE_DIR, f"{sym}.json")
    if os.path.exists(p):
        d = json.load(open(p, encoding="utf-8"))
        if isinstance(d, dict) and d.get("base") and d.get("h4"):
            base, h4 = d["base"], d["h4"]
    if base is None:
        for f in os.listdir(CACHE_DIR):
            if sym.split("-")[0] in f.upper() and f.endswith(".json"):
                d = json.load(open(os.path.join(CACHE_DIR, f), encoding="utf-8"))
                if isinstance(d, dict):
                    if d.get("1h") and d.get("4h"):
                        base, h4 = d["1h"], d["4h"]; break
                    if d.get("base") and d.get("h4"):
                        base, h4 = d["base"], d["h4"]; break
    # db 回退（candle_data.db 存的是去掉 -SWAP 的后缀，如 TSLA-USDT）
    if base is None:
        for db_sym in (sym, sym.replace("-SWAP", "")):
            b1, b4 = load_db(db_sym, "1h"), load_db(db_sym, "4h")
            if b1 and b4:
                base, h4 = b1, b4; break
    return base, h4


def get_43_configs():
    try:
        import urllib.request
        d = json.loads(urllib.request.urlopen(
            "http://43.108.10.84:5174/api/pattern/trade/config", timeout=15).read())
        return {s["symbol"]: s for s in (d.get("symbols") or [])}
    except Exception as e:
        print("读取43配置失败:", e)
        return {}


def main():
    cfgs43 = get_43_configs()
    syms = ["NVDA-USDT-SWAP", "CL-USDT-SWAP", "BTC-USDT-SWAP", "ETH-USDT-SWAP",
            "SKHYNIX-USDT-SWAP", "SNDK-USDT-SWAP", "MU-USDT-SWAP",
            "TSLA-USDT-SWAP", "INTC-USDT-SWAP"]

    for sym in syms:
        base, h4 = load_any(sym)
        if not base:
            print(f"\n##### {sym}: 无本地缓存数据（OKX 离线），跳过 #####")
            continue
        sigs, opens, highs, lows, closes, up_plot, dn_plot, flip_idx = build_signals(base, h4)
        target = [s for s in sigs if s.get("v3_execute")]   # V3-C
        print(f"\n##### {sym}  V3-C 信号 {len(target)} 笔  "
              f"(1h {len(base)}根 {dt.datetime.utcfromtimestamp(base[0]['ts']/1000):%Y-%m-%d}"
              f"~{dt.datetime.utcfromtimestamp(base[-1]['ts']/1000):%Y-%m-%d}) #####")

        # 两种候选止损方式（固定 tp1 隔离变量）
        r_pct = backtest(target, highs, lows, closes, up_plot, dn_plot, flip_idx,
                         TP1_PCT, TP1_RATIO, "pct", 3.0, False)
        r_st = backtest(target, highs, lows, closes, up_plot, dn_plot, flip_idx,
                        TP1_PCT, TP1_RATIO, "st", 2.0, True)
        # 43 当前配置（None→默认）
        c = cfgs43.get(sym, {})
        c_tp1 = c.get("tp1_pct") or 2.0
        c_rat = c.get("tp1_ratio") or 50.0
        c_mode = c.get("sl_mode") or "st"
        c_pct = c.get("sl_pct") or 2.0
        c_trail = c.get("trail_with_st")
        c_trail = True if c_trail is None else c_trail
        r_cur = backtest(target, highs, lows, closes, up_plot, dn_plot, flip_idx,
                         c_tp1, c_rat, c_mode, c_pct, c_trail)

        def line(tag, r):
            return (f"{tag:22s} 收益{r['ret']:+7.2f}% 回撤{r['dd']:6.2f}% "
                    f"PF{r['pf']:6.2f} 笔数{r['n']:3d} 胜率{r['wr']:5.1f}% "
                    f"Calmar{r['cal']:6.2f}")
        print(line("M_pct pct/3 trail=off", r_pct))
        print(line("M_st  st/2  trail=on ", r_st))
        print(line(f"43当前({c_mode}/{c_pct} tr={c_trail} tp{c_tp1}/{c_rat})", r_cur))

        # 判定
        if r_st["cal"] > r_pct["cal"] * 1.15:
            verdict = "→ 趋势感知(st/trail) 更优：波动/毛刺多，固定pct易被扫误损"
        elif r_pct["cal"] > r_st["cal"] * 1.15:
            verdict = "→ 固定pct(trail=off) 更优：强趋势，跟踪ST易被回调砍(chop)"
        else:
            verdict = "→ 两者相近，按品种性格二选一即可"
        print("判定:", verdict)


if __name__ == "__main__":
    main()
