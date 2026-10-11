# -*- coding: utf-8 -*-
"""SPX 首页策略 · ST 参数 × 评分闸门 寻优（复用 bt_home_opt4.py 口径）。

SPX 在两页线上 + 本地快照里都没有配置，故手动构造一个基础 symbol：
- symbol = SPX-USDT-SWAP
- allow_tfs = ["1h"]
- exit_rules = 首页快照默认单 tp 出场（tp1=1.5%/70, sl=3.0%(pct), trail=False, maxloss=False）
其余与 bt_home_opt4.py 完全一致：固定 100U × 1x，tp1/sl/trail 出场，
2026-01-01 ~ 数据末尾，闸门全关扫 ST、再固定最优 ST 扫闸门，并算 OOS。
"""
from __future__ import annotations

import os
import sys
import time

import bt_home_2026 as H
from bt_home_2026 import (M, run_backtest, stats, y26_lo,
                          BARS26, MARGIN, LEV, INIT)

SYMS = ["SPX"]
TFS = ["1h", "4h", "1d"]

PERIODS = [5, 7, 9, 10, 11, 13, 15, 18, 21, 30]
MULTS = [2.0, 2.5, 3.0, 4.0, 5.0]
SRCS = ["hl2", "ohlc4", "close"]

GATE_OFF = ("", 0, False, False)
GATE_GRID = []
for se in ("v2", "quality_filter_v2"):
    for thr in (40, 50, 70):
        for rng in (False, True):
            GATE_GRID.append((se, thr, rng, True))


def make_base(symbol):
    return dict(
        symbol=symbol, enabled=True,
        margin_usdt=MARGIN, leverage=LEV, allow_tfs=["1h"],
        params={"periods": 10, "multiplier": 3.0, "src": "hl2",
                "change_atr": True, "fast_len": 20, "slow_len": 50,
                "ma_type": "EMA"},
        er_hide_below=0.0, er_weak_min=0.0, er_min=0.0, er_trend=0.0,
        quick_enabled=False, allow_grades=["A", "B", "C"], min_score=0,
        cooldown_sec=300, atr_filter_enabled=False, atr_vol_min=0.7,
        range_filter_enabled=False, range_size_max=0.15, range_touches_min=3,
        mtf_filter_enabled=False, mtf_consistency_min=0.6, mtf_flip_max=5,
        adx_filter_enabled=False, adx_min=20.0, adx_period=14,
        use_scoring=False, scoring_full_threshold=0.0,
        scoring_half_threshold=0.0, scoring_alert_threshold=0.0,
        use_dynamic_threshold=False, score_engine="",
        exit_rules=dict(
            enabled=True,
            tp1_pct=1.5, tp1_ratio=70.0,
            tp2_pct=999.0, tp2_ratio=0.0,
            tp3_pct=999.0, tp3_ratio=0.0, tp3_mode="pct",
            move_sl_to_entry=True,
            sl_mode="pct", sl_pct=3.0,
            trail_with_st=False, max_loss_enabled=False))


def build_sym(base, params, se, thr, rng, use_sc):
    s = dict(base)
    s["params"] = dict(params)
    s["score_engine"] = se
    s["scoring_full_threshold"] = thr
    s["scoring_half_threshold"] = thr
    s["scoring_alert_threshold"] = (thr * 0.7) if thr > 0 else 0.0
    s["use_scoring"] = use_sc
    s["use_dynamic_threshold"] = False
    s["range_filter_enabled"] = rng
    return s


def run(seg, cbtf_seg, sym):
    ex = M.exit_rules(sym)
    cfg = M.trade_cfg(sym)
    r = run_backtest(seg, sym["params"], init_cash=INIT, fee_rate=0.0005,
                     allow_short=True, exit_rules=ex, sizing="fixed",
                     margin_usdt=MARGIN, leverage=LEV, live_gate=cfg,
                     gate_tf=sym["allow_tfs"][0], candles_by_tf=cbtf_seg)
    if "error" in r:
        return None
    return stats(r)


def bh(seg):
    return (seg[-1]["c"] / seg[0]["c"] - 1) * 100 if len(seg) > 1 else 0.0


def main():
    t0 = time.time()
    base = {nm: make_base(f"{nm}-USDT-SWAP") for nm in SYMS}

    data = {}
    for nm, sym in base.items():
        cbtf = {}
        for tf in TFS:
            arr = M.fetch_candles(sym["symbol"], tf, BARS26[tf])
            arr = [c for c in arr if c["ts"] >= y26_lo()]
            cbtf[tf] = arr
        seg = cbtf.get("1h") or []
        if len(seg) < 100:
            print(f"!! {nm} 1h 数据不足 ({len(seg)})，SPX 可能无 K 线，退出")
            return
        hi = seg[-1]["ts"]
        cbtf_seg = {tf: [c for c in a if y26_lo() <= c["ts"] <= hi]
                    for tf, a in cbtf.items() if a}
        k = int(len(seg) * 0.60)
        test0 = seg[k]["ts"]
        seg_test = seg[k:]
        cbtf_test = {tf: [c for c in a if c["ts"] >= test0]
                     for tf, a in cbtf_seg.items() if a}
        data[nm] = dict(seg=seg, cbtf_seg=cbtf_seg, seg_test=seg_test,
                        cbtf_test=cbtf_test, k=k)

    report = {}
    for nm, d in data.items():
        print("\n" + "=" * 104)
        print(f"【{nm}】{base[nm]['symbol']}  "
              f"1h {len(d['seg'])} 根 {M.ts_fmt(d['seg'][0]['ts'])}~{M.ts_fmt(d['seg'][-1]['ts'])}  "
              f"买入持有(全) {bh(d['seg']):+.2f}% ｜ 买入持有(OOS后40%) {bh(d['seg_test']):+.2f}%")
        print("=" * 104)

        # ── Stage A：ST 参数，闸门全关 ──
        rows = []
        for src in SRCS:
            for p in PERIODS:
                for m in MULTS:
                    params = dict(periods=p, multiplier=m, src=src,
                                  change_atr=True, fast_len=20,
                                  slow_len=50, ma_type="EMA")
                    s = build_sym(base[nm], params, *GATE_OFF)
                    st = run(d["seg"], d["cbtf_seg"], s)
                    if not st or st["n"] < 10:
                        continue
                    stt = run(d["seg_test"], d["cbtf_test"], s)
                    rows.append(dict(p=p, m=m, src=src,
                                     tot=st["tot"], n=st["n"], pf=st["pf"],
                                     mdd=st["mdd_u"], wr=st["wr"],
                                     oos=(stt["tot"] if stt else 0.0),
                                     oos_n=(stt["n"] if stt else 0)))
        if not rows:
            print("   无有效 ST 组合（笔数<10）")
            continue
        rows.sort(key=lambda r: r["tot"], reverse=True)
        best_a = rows[0]
        print(f"\n── Stage A · ST 参数（闸门全关）有效 {len(rows)} 组，Top12（按全样本Σ净%）──")
        print(f"{'#':>3} {'per':>4} {'mult':>5} {'src':>6} {'笔':>5} {'Σ%':>8} "
              f"{'PF':>6} {'回撤U':>7} {'胜率':>6} {'OOS%':>8}")
        for i, r in enumerate(rows[:12], 1):
            print(f"{i:>3} {r['p']:>4} {r['m']:>5.1f} {r['src']:>6} {r['n']:>5} "
                  f"{r['tot']:>8.2f} {r['pf']:>6.2f} {r['mdd']:>7.2f} {r['wr']:>6.1f} "
                  f"{r['oos']:>8.2f}")

        bsrc = best_a["src"]
        lut = {(r["p"], r["m"]): r["tot"] for r in rows if r["src"] == bsrc}
        print(f"\n   邻域稳定性（src={bsrc} 全样本Σ净%，看最优点是高原/孤峰）：")
        print("    " + "".join(f"{m:>8.1f}" for m in MULTS))
        for p in PERIODS:
            line = f"{p:>4}"
            for m in MULTS:
                v = lut.get((p, m))
                line += f"{'—':>8}" if v is None else f"{v:>8.1f}"
            print(line)
        n_pos = sum(1 for r in rows if r["tot"] > 0)
        print(f"   ST 网格内全样本为正：{n_pos}/{len(rows)} 组")

        # ── Stage B：固定最优 ST，扫评分闸门 ──
        bp = dict(periods=best_a["p"], multiplier=best_a["m"], src=bsrc,
                  change_atr=True, fast_len=20, slow_len=50, ma_type="EMA")
        grows = []
        for se, thr, rng, use in [GATE_OFF] + GATE_GRID:
            s = build_sym(base[nm], bp, se, thr, rng, use)
            st = run(d["seg"], d["cbtf_seg"], s)
            if not st or st["n"] < 10:
                continue
            stt = run(d["seg_test"], d["cbtf_test"], s)
            grows.append(dict(se=se, thr=thr, rng=rng, use=use,
                              tot=st["tot"], n=st["n"], pf=st["pf"],
                              mdd=st["mdd_u"], wr=st["wr"],
                              oos=(stt["tot"] if stt else 0.0)))
        grows.sort(key=lambda r: r["tot"], reverse=True)
        print(f"\n── Stage B · 评分闸门（固定 ST {bp['periods']}×{bp['multiplier']}/{bsrc}）"
              f"共 {len(grows)} 组，Top10 ──")
        print(f"{'#':>3} {'score_engine':>18} {'thr':>4} {'range':>6} {'笔':>5} "
              f"{'Σ%':>8} {'PF':>6} {'回撤U':>7} {'胜率':>6} {'OOS%':>8}")
        for i, r in enumerate(grows[:10], 1):
            se_lbl = r["se"] if r["se"] else "(闸门关)"
            print(f"{i:>3} {se_lbl:>18} {r['thr']:>4} {str(r['rng']):>6} {r['n']:>5} "
                  f"{r['tot']:>8.2f} {r['pf']:>6.2f} {r['mdd']:>7.2f} {r['wr']:>6.1f} "
                  f"{r['oos']:>8.2f}")

        best_b = grows[0]
        report[nm] = dict(best_st=bp, best_gate=best_b,
                          gate_off_tot=grows[0]["tot"] if grows else 0.0,
                          bh_full=bh(d["seg"]), bh_oos=bh(d["seg_test"]))
        print(f"\n   ★ {nm} 推荐：ST {bp['periods']}×{bp['multiplier']}/{bsrc} ｜ "
              f"闸门 {best_b['se'] or '(关)'} thr={best_b['thr']} range={best_b['rng']} "
              f"→ 全样本 {best_b['tot']:+.2f}%  OOS {best_b['oos']:+.2f}% "
              f"｜ 买入持有 全 {bh(d['seg']):+.2f}% / OOS {bh(d['seg_test']):+.2f}%")

    print("\n" + "=" * 104)
    print("【SPX 推荐配置汇总】")
    print("=" * 104)
    print(f"{'品种':<10}{'ST(periods×mult/src)':<24}{'score_engine':<20}"
          f"{'thr':>5}{'range':>7}{'Σ%':>9}{'OOS%':>9}{'买入持有%':>11}")
    print("-" * 95)
    for nm, r in report.items():
        bp = r["best_st"]
        bg = r["best_gate"]
        st = f"{bp['periods']}×{bp['multiplier']}/{bp['src']}"
        se = bg["se"] or "(关)"
        print(f"{nm:<10}{st:<24}{se:<20}{bg['thr']:>5}{str(bg['rng']):>7}"
              f"{bg['tot']:>9.2f}{bg['oos']:>9.2f}{r['bh_full']:>11.2f}")
    print(f"\n总耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
