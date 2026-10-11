# -*- coding: utf-8 -*-
"""INTC 线上 43 页真实配置 IS/OOS 验证。

配置来源：_bt2026_data/_intc_base43.json（线上 43 页 INTC：ST 7×3.0/hl2/change_atr、
tp1 0.8%/30% + sl_mode=st + trail + range过滤 + filter_v3 + 阈值40 + score_engine v2）。
K 线来源：_bt2026_data/INTC_USDT_SWAP.json（1h 5443 根，2026-02-26~10-11）。
引擎：backtest_engine.run_backtest + bt_home_2026.stats（与线上页面同口径）。

先复现你贴的 +58.79U/63笔/65.1%/PF2.09/回撤10.59U/单笔+0.93%，再按 70/30 切 IS/OOS。
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_home_2026 as B
from backtest_engine import run_backtest

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "_bt2026_data", "_intc_base43.json")
DATA = os.path.join(HERE, "_bt2026_data", "INTC_USDT_SWAP.json")
TFS = ["15m", "1h", "4h", "1d"]


def load():
    sym = json.load(open(CFG, encoding="utf-8"))
    d = json.load(open(DATA, encoding="utf-8"))
    cands = {tf: d.get(tf, []) for tf in TFS}
    return sym, cands


def slice_candles(cands, lo, hi):
    return {tf: [c for c in arr if lo <= c["ts"] <= hi] for tf, arr in cands.items() if arr}


def run(sym, cands, lo, hi, v3):
    cbtf = slice_candles(cands, lo, hi)
    gate_tf = sym["allow_tfs"][0]
    seg = cbtf.get(gate_tf)
    if not seg:
        return dict(err="no seg")
    ex = B.M.exit_rules(sym)
    cfg = B.M.trade_cfg(sym)
    r = run_backtest(seg, sym["params"], init_cash=B.INIT, fee_rate=0.0005,
                     allow_short=True, exit_rules=ex, sizing="fixed",
                     margin_usdt=B.MARGIN, leverage=B.LEV,
                     live_gate=cfg, gate_tf=gate_tf, candles_by_tf=cbtf,
                     v3_filter=v3)
    if "error" in r:
        return dict(err=r["error"])
    return B.stats(r)


def report(label, s):
    if s is None:
        print(f"  {label}: 无数据"); return
    if "err" in s:
        print(f"  {label}: ERR {s['err']}"); return
    n = s["n"]
    print(f"  {label}: 笔={n} 胜率={s['wr']:.1f}% PF={s['pf']:.2f} "
          f"均盈/均亏={s['payoff']:.2f} 收益={s['tot']:+.2f}U 回撤={s['mdd_u']:.2f}U "
          f"单笔={s['tot']/n:+.3f}% 复利={s['comp']:+.2f}%")


def main():
    sym, cands = load()
    lo = cands["1h"][0]["ts"]
    hi = cands["1h"][-1]["ts"]
    print(f"窗口 {B.M.ts_fmt(lo)} ~ {B.M.ts_fmt(hi)}  1h {len(cands['1h'])} 根")
    for v3 in (False, True):
        print(f"\n########## v3_filter={v3} ##########")
        sf = run(sym, cands, lo, hi, v3)
        report("全样本(对照 +58.79U/63笔/65.1%/PF2.09/回撤10.59U)", sf)
        if sf and "err" not in sf and sf["n"] > 5:
            cut = cands["1h"][int(len(cands["1h"]) * 0.7)]["ts"]
            cut_dt = B.M.ts_fmt(cut)
            print(f"  IS/OOS 切点={cut_dt}（前70% IS / 后30% OOS）")
            report(f"IS(~{cut_dt})", run(sym, cands, lo, cut, v3))
            report(f"OOS({cut_dt}~)", run(sym, cands, cut, hi, v3))


if __name__ == "__main__":
    main()
