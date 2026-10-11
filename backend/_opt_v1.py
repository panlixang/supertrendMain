# -*- coding: utf-8 -*-
"""INTC/AMD 在 V1(趋势跟随阶梯引擎) 框架下的 ST × 出场 × 闸门分数 联合寻优。

框架：score_engine='' → 走 V1 阶梯引擎；live_gate=cfg 应用 ER阈值 + 信号打分 + 动态阈值
+ range 过滤。引擎中 v3_filter 与 live_gate 互斥，故 V1 口径只走 live_gate（不传 v3_filter）。
断点续跑：每算一组立即追加写入 JSONL，崩溃/中断后重跑会自动跳过已算组合。
用法: python _opt_v1.py INTC   /   python _opt_v1.py AMD
"""
import sys, os, json, copy, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_home_2026 as B
from backtest_engine import run_backtest

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "_bt2026_data")
TFS_ALL = ["15m", "1h", "4h", "1d"]

ST_GRID = [(p, m) for p in (5, 7, 9, 10, 12) for m in (2.0, 2.5, 3.0, 3.5)]
TP1 = [1.5, 2.0, 3.0, 4.0]
SL = [2.0, 3.0]
TRAIL = [False, True]
GATE = [30, 40, 50]


def get_sym(name):
    live = B.load_live()
    for page, d in live.items():
        if name in d:
            return d[name], page
    raise RuntimeError(f"{name} 不在线上配置")


def build_cache(name):
    return json.load(open(os.path.join(DATA, f"{name}_USDT_SWAP.json"), encoding="utf-8"))


def run_v1(name, cache, st, ex, gate):
    sym0, page = get_sym(name)
    sym = copy.deepcopy(sym0)
    sym["score_engine"] = ""
    sym["use_scoring"] = True
    sym["use_dynamic_threshold"] = True
    sym["scoring_full_threshold"] = gate
    sym["scoring_half_threshold"] = gate
    sym["scoring_alert_threshold"] = gate
    sym["allow_tfs"] = ["1h"]
    sym["params"] = {"periods": st[0], "multiplier": st[1], "src": "hl2",
                     "change_atr": True, "fast_len": 20, "slow_len": 50, "ma_type": "EMA"}
    sym["exit_rules"] = {
        "enabled": True, "tp1_pct": ex[0], "tp1_ratio": 50.0,
        "tp2_pct": 999.0, "tp2_ratio": 0.0, "tp3_pct": 999.0, "tp3_ratio": 0.0, "tp3_mode": "pct",
        "move_sl_to_entry": True, "sl_mode": "pct", "sl_pct": ex[1],
        "trail_with_st": ex[2], "sl_buffer_atr": 0.5, "sl_min_pct": 1.2,
        "protect_profit_at": 1.5, "protect_trail_pct": 0.8,
        "max_loss_enabled": False, "max_loss_pct": 10.0,
    }
    lo = B.y26_lo()
    cbtf = {}
    for tf in TFS_ALL:
        arr = cache.get(tf, [])
        cbtf[tf] = [c for c in arr if c["ts"] >= lo]
    gate_tf = "1h"
    seg = cbtf.get(gate_tf) or []
    if len(seg) < 100:
        return dict(err=f"{gate_tf} K线不足 {len(seg)}")
    hi = seg[-1]["ts"]
    cbtf_seg = {tf: [c for c in arr if lo <= c["ts"] <= hi] for tf, arr in cbtf.items() if arr}
    exr = B.M.exit_rules(sym)
    cfg = B.M.trade_cfg(sym)
    r = run_backtest(
        seg, sym["params"], init_cash=B.INIT, fee_rate=0.0005, allow_short=True,
        exit_rules=exr, sizing="fixed", margin_usdt=B.MARGIN, leverage=B.LEV,
        live_gate=cfg, gate_tf=gate_tf, candles_by_tf=cbtf_seg)
    if "error" in r:
        return dict(err=r["error"])
    s = B.stats(r)
    s["blocked"] = r.get("er_blocked", 0)
    s["start"] = B.M.ts_fmt(seg[0]["ts"])
    s["end"] = B.M.ts_fmt(hi)
    return s


def main():
    name = (sys.argv[1] if len(sys.argv) > 1 else "INTC").upper()
    cache = build_cache(name)
    jsonl = os.path.join(HERE, f"_opt_v1_{name}.jsonl")
    # 断点续跑：加载已有结果
    done = {}
    loaded = []
    if os.path.exists(jsonl):
        with open(jsonl, encoding="utf-8") as f:
            for line in f:
                try:
                    o = json.loads(line)
                    key = (tuple(o["st"]), o["tp1"], o["sl"], o["tr"], o["gate"])
                    done[key] = True
                    loaded.append((o["st"], o["tp1"], o["sl"], o["tr"], o["gate"], o["s"]))
                except Exception:
                    pass
    print(f"== {name} V1 联合寻优 (ST{len(ST_GRID)} × tp1{len(TP1)} × sl{len(SL)} "
          f"× trail{len(TRAIL)} × gate{len(GATE)}) 已缓存 {len(loaded)} 组 ==", flush=True)
    rows = list(loaded)
    total = len(ST_GRID) * len(TP1) * len(SL) * len(TRAIL) * len(GATE)
    done_count = len(rows)
    fout = open(jsonl, "a", encoding="utf-8")
    for st in ST_GRID:
        for tp1 in TP1:
            for sl in SL:
                for tr in TRAIL:
                    for g in GATE:
                        key = (st, tp1, sl, tr, g)
                        if key in done:
                            continue
                        s = run_v1(name, cache, st, (tp1, sl, tr), g)
                        done_count += 1
                        if "err" in s:
                            print(f"  ERR st={st} tp1={tp1} sl={sl} tr={tr} g={g}: {s['err']}", flush=True)
                            continue
                        rows.append((st, tp1, sl, tr, g, s))
                        rec = {"st": list(st), "tp1": tp1, "sl": sl, "tr": tr, "gate": g, "s": s}
                        fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        fout.flush()
                        if done_count % 25 == 0:
                            print(f"  进度 {done_count}/{total} ({done_count*100//total}%)", flush=True)
    fout.close()
    rows.sort(key=lambda x: x[5]["tot"], reverse=True)
    print(f"\n########## {name} V1 联合寻优 Top20（共 {len(rows)} 组合）##########", flush=True)
    print(f"  {'ST':<9}{'tp1':>5}{'sl':>5}{'tr':>5}{'gate':>6}{'笔':>6}{'胜率':>8}"
          f"{'PF':>7}{'盈亏比':>8}{'Σ%':>9}{'回撤U':>9}{'拦':>6}  起~止", flush=True)
    for st, tp1, sl, tr, g, s in rows[:20]:
        print(f"  {st[0]}×{st[1]:<4}{tp1:>5}{sl:>5}{('T' if tr else 'F'):>5}{g:>6}"
              f"{s.get('n',0):>6}{s.get('wr',0):>8.1f}{s.get('pf',0):>7.2f}{s.get('payoff',0):>8.2f}"
              f"{s.get('tot',0):>+9.2f}{s.get('mdd_u',0):>9.2f}{s.get('blocked',0):>6}  "
              f"{s.get('start','')}~{s.get('end','')}", flush=True)
    outp = os.path.join(HERE, f"_opt_v1_{name}.txt")
    with open(outp, "w", encoding="utf-8") as f:
        f.write(f"# {name} V1 联合寻优 Top20（ST×tp1×sl×trail×gate）\n")
        for st, tp1, sl, tr, g, s in rows[:20]:
            f.write(f"{st[0]}×{st[1]} tp1={tp1} sl={sl} tr={tr} gate={g} "
                    f"n={s.get('n',0)} wr={s.get('wr',0):.1f} pf={s.get('pf',0):.2f} "
                    f"payoff={s.get('payoff',0):.2f} tot={s.get('tot',0):+.2f} "
                    f"mdd={s.get('mdd_u',0):.2f} blocked={s.get('blocked',0)}\n")
    print(f"\n[明细已写入 {outp}]", flush=True)


if __name__ == "__main__":
    main()
