# -*- coding: utf-8 -*-
"""INTC/AMD 的 ST 网格寻优（真实首页框架）。
- 用本地缓存 K 线（1h/4h），不依赖 OKX
- 出场固定为各自【原配好出场】：INTC 2.0/50 pct, AMD 4.0/50 pct
- 闸门用线上真实配置：INTC v2/40 range=True, AMD 关
- 只扫 ST 的 periods × multiplier（src 锁 hl2）
"""
import sys, os, json, copy, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_home_2026 as B

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "_bt2026_data")
TFS = ["1h", "4h"]
SRC = "hl2"
PERIODS = [5, 6, 7, 8, 9, 10, 11, 12, 14, 16, 18, 20, 22, 25]
MULTS = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
ORIG_EXIT = {
    "INTC": dict(tp1_pct=2.0, tp1_ratio=50.0, sl_pct=3.0, sl_mode="pct",
                 trail_with_st=False, move_sl_to_entry=True, exit_mode="single",
                 tp2_pct=999.0, tp2_ratio=0.0, tp3_pct=999.0, tp3_ratio=0.0, tp3_mode="pct"),
    "AMD":  dict(tp1_pct=4.0, tp1_ratio=50.0, sl_pct=3.0, sl_mode="pct",
                 trail_with_st=True, move_sl_to_entry=True, exit_mode="single",
                 tp2_pct=999.0, tp2_ratio=0.0, tp3_pct=999.0, tp3_ratio=0.0, tp3_mode="pct"),
}


def load_candles(base):
    d = json.load(open(os.path.join(DATA, f"{base}_USDT_SWAP.json")))
    lo = B.y26_lo()
    return {tf: [c for c in (d.get(tf) or []) if c["ts"] >= lo] for tf in TFS}


def run(sym, cbtf, periods, mult):
    gate_tf = sym["allow_tfs"][0]
    seg = cbtf.get(gate_tf) or []
    if len(seg) < 100:
        return None
    hi = seg[-1]["ts"]
    cbtf_seg = {tf: [c for c in arr if B.y26_lo() <= c["ts"] <= hi]
                for tf, arr in cbtf.items() if arr}
    params = dict(sym["params"])
    params["periods"], params["multiplier"], params["src"] = periods, mult, SRC
    ex = B.M.exit_rules(sym)
    cfg = B.M.trade_cfg(sym)
    r = B.run_backtest(seg, params, init_cash=B.INIT, fee_rate=0.0005,
                       allow_short=True, exit_rules=ex, sizing="fixed",
                       margin_usdt=B.MARGIN, leverage=B.LEV,
                       live_gate=cfg, gate_tf=gate_tf, candles_by_tf=cbtf_seg)
    if "error" in r:
        return None
    s = B.stats(r)
    return dict(n=s["n"], wr=s["wr"], pf=s["pf"], payoff=s["payoff"],
                tot=s["tot"], mdd=s["mdd_u"], blocked=r.get("er_blocked", 0))


def sweep(name, live):
    sym0 = live["43"][name]
    sym = copy.deepcopy(sym0)
    er = dict(sym0.get("exit_rules") or {})
    er.update(ORIG_EXIT[name])
    sym["exit_rules"] = er
    p = sym0["params"]
    print(f"\n########## {name} [43] 真实闸门: se={sym0.get('score_engine')!r} "
          f"f={sym0.get('scoring_full_threshold')} range={sym0.get('range_filter_enabled')} "
          f"| 原出场 tp1={ORIG_EXIT[name]['tp1_pct']}/{ORIG_EXIT[name]['tp1_ratio']} "
          f"sl={ORIG_EXIT[name]['sl_mode']} ##########", flush=True)
    cbtf = load_candles(name)
    rows, t0 = [], time.time()
    total = len(PERIODS) * len(MULTS)
    done = 0
    for P in PERIODS:
        for M in MULTS:
            rr = run(sym, cbtf, P, M)
            done += 1
            if rr:
                rows.append((P, M, rr))
            if done % 12 == 0:
                print(f"  ...进度 {done}/{total}", flush=True)
    rows.sort(key=lambda x: -x[2]["tot"])
    print(f"\n  === {name} ST 寻优 Top15（按 Σ收益 降序，共 {len(rows)} 组，耗时 {time.time()-t0:.0f}s）===", flush=True)
    print(f"  {'ST':<10}{'笔':>4}{'胜率':>7}{'PF':>7}{'盈亏比':>8}{'ΣU':>9}{'回撤U':>8}{'拦':>5}", flush=True)
    for P, M, rr in rows[:15]:
        print(f"  {f'{P}×{M}':<10}{rr['n']:>4}{rr['wr']:>6.1f}%{rr['pf']:>7.2f}"
              f"{rr['payoff']:>8.2f}{rr['tot']:>9.2f}{rr['mdd']:>8.2f}{rr['blocked']:>5}", flush=True)
    return rows


def main():
    live = B.load_live()
    for name in ("INTC", "AMD"):
        if name not in live.get("43", {}):
            print(f"\n##### {name} 不在 43 页首页配置"); continue
        sweep(name, live)


if __name__ == "__main__":
    main()
