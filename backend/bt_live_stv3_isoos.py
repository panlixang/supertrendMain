# -*- coding: utf-8 -*-
"""线上 43 页（形态识别页）ST+V3 策略 —— INTC / AMD 回测收益 + IS/OOS 验证。

配置来源：_bt2026_data/_intc_base43.json / _base43_AMD.json（线上 43 页真实配置：
ST 翻转 + V3 过滤 + position 出场状态机，与线上页面同口径）。
K 线来源：_bt2026_data/{INTC,AMD}_USDT_SWAP.json（2026-02-26~10-11）。
引擎：backtest_engine.run_backtest + bt_home_2026.stats（固定 100U×1x 口径）。

同时给 v3_filter=True（显式 ST+V3 策略）与 v3_filter=False（仅 live_gate）两种口径。
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_home_2026 as B
from backtest_engine import run_backtest

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "_bt2026_data")
SYMS = {
    "INTC": ("_intc_base43.json", "INTC_USDT_SWAP.json"),
    "AMD":  ("_base43_AMD.json",  "AMD_USDT_SWAP.json"),
}
TFS = ["15m", "1h", "4h", "1d"]


def load(name):
    cfgf, dataf = SYMS[name]
    sym = json.load(open(os.path.join(DATA, cfgf), encoding="utf-8"))
    d = json.load(open(os.path.join(DATA, dataf), encoding="utf-8"))
    cands = {tf: d.get(tf, []) for tf in TFS}
    return sym, cands


def run(sym, cands, lo, hi, v3):
    cbtf = {tf: [c for c in arr if lo <= c["ts"] <= hi] for tf, arr in cands.items() if arr}
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
    if s is None or "err" in s:
        print(f"  {label}: {s.get('err','无数据')}"); return
    n = s["n"]
    print(f"  {label}: 笔={n} 胜率={s['wr']:.1f}% PF={s['pf']:.2f} "
          f"均盈/均亏={s['payoff']:.2f} 收益={s['tot']:+.2f}U 回撤={s['mdd_u']:.2f}U "
          f"单笔={s['tot']/n:+.3f}% 复利={s['comp']:+.2f}%")


def main():
    for name in SYMS:
        sym, cands = load(name)
        lo = cands["1h"][0]["ts"]; hi = cands["1h"][-1]["ts"]
        print(f"\n########## {name}  窗口 {B.M.ts_fmt(lo)}~{B.M.ts_fmt(hi)} "
              f"1h {len(cands['1h'])} 根  ST{int(sym['params']['periods'])}"
              f"×{sym['params']['multiplier']} ##########")
        for v3 in (True, False):
            tag = "ST+V3(v3_filter=True)" if v3 else "仅ST+live_gate(v3_filter=False)"
            print(f"\n  ----- {tag} -----")
            sf = run(sym, cands, lo, hi, v3)
            report("全样本", sf)
            if sf and "err" not in sf and sf["n"] > 5:
                cut = cands["1h"][int(len(cands["1h"]) * 0.7)]["ts"]
                cd = B.M.ts_fmt(cut)
                print(f"  IS/OOS 切点={cd}")
                report(f"IS(~{cd})", run(sym, cands, lo, cut, v3))
                report(f"OOS({cd}~)", run(sym, cands, cut, hi, v3))


if __name__ == "__main__":
    main()
