# -*- coding: utf-8 -*-
"""INTC/AMD 联合寻优：ST × 闸门分数(INTC) × 出场(tp1/sl/trail) 全组合。
- 本地缓存 K 线（1h/4h），不依赖 OKX
- 真实首页框架：各自线上 gate/range 配置（INTC v2+range，AMD 关）
- 只扫 ST + 闸门阈值 + 出场止盈止损
"""
import sys, os, json, copy, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_home_2026 as B

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "_bt2026_data")
TFS = ["1h", "4h"]
SRC = "hl2"
PERIODS = [5, 7, 9, 10, 12]
MULTS = [2.0, 2.5, 3.0, 3.5]
TP1 = [1.5, 2.0, 3.0, 4.0]
SL = [2.0, 3.0]
TRAIL = [False, True]
GATES_INTC = [40, 45, 50]   # scoring_full_threshold，保持 v2+range
TP1_RATIO = 50.0


def load_candles(base):
    d = json.load(open(os.path.join(DATA, f"{base}_USDT_SWAP.json")))
    lo = B.y26_lo()
    return {tf: [c for c in (d.get(tf) or []) if c["ts"] >= lo] for tf in TFS}


def run(sym, cbtf, periods, mult, gate, ex):
    s2 = copy.deepcopy(sym)
    if gate is not None:
        s2["scoring_full_threshold"] = gate
    er = dict(sym.get("exit_rules") or {})
    er.update(ex)
    s2["exit_rules"] = er
    gate_tf = s2["allow_tfs"][0]
    seg = cbtf.get(gate_tf) or []
    if len(seg) < 100:
        return None
    hi = seg[-1]["ts"]
    cbtf_seg = {tf: [c for c in arr if B.y26_lo() <= c["ts"] <= hi]
                for tf, arr in cbtf.items() if arr}
    params = dict(s2["params"])
    params["periods"], params["multiplier"], params["src"] = periods, mult, SRC
    ex_r = B.M.exit_rules(s2)
    cfg = B.M.trade_cfg(s2)
    r = B.run_backtest(seg, params, init_cash=B.INIT, fee_rate=0.0005,
                       allow_short=True, exit_rules=ex_r, sizing="fixed",
                       margin_usdt=B.MARGIN, leverage=B.LEV,
                       live_gate=cfg, gate_tf=gate_tf, candles_by_tf=cbtf_seg)
    if "error" in r:
        return None
    s = B.stats(r)
    return dict(n=s["n"], wr=s["wr"], pf=s["pf"], payoff=s["payoff"],
                tot=s["tot"], mdd=s["mdd_u"], blocked=r.get("er_blocked", 0))


def exit_dict(tp, sl, trail):
    return dict(tp1_pct=tp, tp1_ratio=TP1_RATIO, sl_pct=sl, sl_mode="pct",
                trail_with_st=trail, move_sl_to_entry=True, exit_mode="single",
                tp2_pct=999.0, tp2_ratio=0.0, tp3_pct=999.0, tp3_ratio=0.0, tp3_mode="pct")


def sweep(name, live):
    sym0 = live["43"][name]
    cbtf = load_candles(name)
    gates = GATES_INTC if sym0.get("score_engine") else [None]
    rows, t0 = [], time.time()
    total = len(PERIODS) * len(MULTS) * len(TP1) * len(SL) * len(TRAIL) * len(gates)
    done = 0
    for P in PERIODS:
        for M in MULTS:
            for tp in TP1:
                for sl in SL:
                    for tr in TRAIL:
                        for g in gates:
                            rr = run(sym0, cbtf, P, M, g, exit_dict(tp, sl, tr))
                            done += 1
                            if rr:
                                rows.append((P, M, tp, sl, tr, g, rr))
                            if done % 50 == 0:
                                print(f"  [{name}] 进度 {done}/{total} "
                                      f"({100*done//total}%)", flush=True)
    rows.sort(key=lambda x: -x[6]["tot"])
    print(f"\n########## {name} 联合寻优 Top20（共 {len(rows)} 组，"
          f"耗时 {time.time()-t0:.0f}s）##########", flush=True)
    print(f"  {'ST':<9}{'tp1':>5}{'sl':>5}{'tr':>4}{'gate':>6}"
          f"{'笔':>4}{'胜率':>7}{'PF':>7}{'盈亏比':>8}{'ΣU':>9}{'回撤U':>8}{'拦':>5}", flush=True)
    for P, M, tp, sl, tr, g, rr in rows[:20]:
        gs = str(g) if g is not None else "off"
        print(f"  {f'{P}×{M}':<9}{tp:>5}{sl:>5}{('T' if tr else 'F'):>4}{gs:>6}"
              f"{rr['n']:>4}{rr['wr']:>6.1f}%{rr['pf']:>7.2f}"
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
