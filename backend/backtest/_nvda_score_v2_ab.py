# -*- coding: utf-8 -*-
"""NVDA Score V2 A/B 实验。

A = 线上现行打分制 v1（score_v2=False）
B = Score V2：signal_quality 连续化(body/volume/距轨) + ATR soft(0-15)
    + MTF soft(4h 方向 0-20)，ER/breakout/penalty 与 v1 一致，raw 110 归一。

对比指标：净利 / 最大回撤 / PF / 胜率 / 交易笔数 / 平均每笔 / blocked，
并做前后半段稳定性验证 + 逐信号 v1 vs v2 打分/动作差异表。
"""
from __future__ import annotations

import bisect
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import replace  # noqa: E402

from backtest import run_backtest  # noqa: E402
from indicators import super_trend, st_signals  # noqa: E402
from regime_scoring import score_signal  # noqa: E402
from _live_cfg_backtest import exit_rules, trade_cfg, _get, ts_fmt  # noqa: E402

LIVE_URL = "http://47.84.106.154:5174/api/trade/symbols"  # NVDA 所在服务器
DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "_live_data", "NVDA.json")
TF = "1h"
TFS = ["15m", "1h", "4h", "1d"]   # 缓存里有的周期
MIN_TRADES = 8


def cut_to(candles: list[dict], ts: int) -> list[dict]:
    """取 ts 及以前的所有 K（含当前根），保持原序。"""
    return candles[:bisect.bisect_right([c["ts"] for c in candles], ts)]


def summarize(r: dict, tag: str) -> dict:
    tl = r.get("trade_list", [])
    avg_trade = round(sum(t["pnl_pct"] for t in tl) / len(tl), 2) if tl else None
    return {
        "tag": tag,
        "pnl_u": round(r["final"] - 100.0, 2),
        "return_pct": round(r["return_pct"], 2),
        "max_dd_pct": r["max_dd_pct"],
        "trades": r["trades"],
        "win_rate": r["win_rate"],
        "profit_factor": r["profit_factor"],
        "avg_trade_pct": avg_trade,
        "avg_bars": r["avg_bars"],
        "blocked": r["er_blocked"],
        "tp1": r["tp1_count"], "tp3": r["tp3_count"],
        "stops": r["stop_count"], "reverses": r["reverse_count"],
    }


def main():
    t0 = time.time()
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        cached = json.load(f)
    cbtf = {t: cached[t] for t in TFS if t in cached}
    candles = cbtf[TF]
    print(f"K线: " + ", ".join(f"{t}={len(cbtf[t])}" for t in TFS if t in cbtf)
          + f"  [{ts_fmt(candles[0]['ts'])} ~ {ts_fmt(candles[-1]['ts'])}]",
          flush=True)
    assert len(candles) > 500, "1h 数据不足"

    # ── 线上 NVDA 配置 ──
    live = _get(LIVE_URL)
    sym = next(s for s in live["symbols"] if "NVDA" in s["symbol"])
    p = sym["params"]
    cfgA = trade_cfg(sym)
    cfgB = replace(cfgA, score_v2=True)
    exit_rules_ = exit_rules(sym)
    print(f"NVDA 线上: ST {p['periods']}×{p['multiplier']} "
          f"gate={cfgA.allow_tfs} er={cfgA.er_hide_below}/{cfgA.er_weak_min}/"
          f"{cfgA.er_min}/{cfgA.er_trend} score={cfgA.scoring_full_threshold:g}/"
          f"{cfgA.scoring_half_threshold:g}/{cfgA.scoring_alert_threshold:g}",
          flush=True)

    common = dict(
        init_cash=100.0, fee_rate=0.0005, allow_short=True,
        exit_rules=exit_rules_, sizing="fixed",
        margin_usdt=sym["margin_usdt"], leverage=sym["leverage"],
        gate_tf=TF, candles_by_tf=cbtf,
    )

    # ── A / B 全量回测 ──
    print("\n[回测 A = v1 现行] ...", flush=True)
    ra = run_backtest(candles, p, live_gate=cfgA, **common)
    print(f"  A done pnl={ra['final']-100:.2f}U trades={ra['trades']} "
          f"in {time.time()-t0:.0f}s", flush=True)
    print("[回测 B = v2] ...", flush=True)
    rb = run_backtest(candles, p, live_gate=cfgB, **common)
    print(f"  B done pnl={rb['final']-100:.2f}U trades={rb['trades']} "
          f"in {time.time()-t0:.0f}s", flush=True)

    sa, sb = summarize(ra, "A(v1)"), summarize(rb, "B(v2)")
    cols = ["tag", "pnl_u", "max_dd_pct", "profit_factor", "win_rate",
            "trades", "avg_trade_pct", "avg_bars", "blocked", "tp1", "stops"]
    print("\n===== 全量 A/B 对比（同阈值 40/40/40）=====")
    print("  " + "  ".join(f"{c:>12}" for c in cols))
    for row in (sa, sb):
        print("  " + "  ".join(f"{row.get(c):>12}" for c in cols))

    # ── B 阈值重标定：v2 分数整体下移，必须重标阈值才能公平对比 ──
    print("\n[阈值重标定 B(v2): full=half=alert ∈ 25..55] ...", flush=True)
    thr_rows = []
    for thr in (25.0, 30.0, 35.0, 40.0, 45.0, 50.0, 55.0):
        cfg_t = replace(cfgB, scoring_full_threshold=thr,
                        scoring_half_threshold=thr,
                        scoring_alert_threshold=thr)
        rt = run_backtest(candles, p, live_gate=cfg_t, **common)
        if "error" in rt:
            continue
        row = summarize(rt, f"v2 thr={thr:g}")
        row["thr"] = thr
        thr_rows.append(row)
        print(f"  v2 thr={thr:g}: pnl={row['pnl_u']}U dd={row['max_dd_pct']}% "
              f"PF={row['profit_factor']} wr={row['win_rate']}% "
              f"trades={row['trades']}", flush=True)

    # 取 v2 最优档（约束笔数够）再比 A
    ok_t = [r for r in thr_rows if r["trades"] >= MIN_TRADES]
    best_b = (max(ok_t, key=lambda r: r["pnl_u"]) if ok_t else None)
    if best_b:
        cfgB2 = replace(cfgB, scoring_full_threshold=best_b["thr"],
                        scoring_half_threshold=best_b["thr"],
                        scoring_alert_threshold=best_b["thr"])
        sb2 = best_b
        print("\n===== 对等对比：A(v1 现行) vs B(v2 阈值重标定后最优) =====")
        print("  " + "  ".join(f"{c:>12}" for c in cols))
        for row in (sa, sb2):
            print("  " + "  ".join(f"{row.get(c):>12}" for c in cols))
        # 前后半段
        mid2 = len(candles) // 2
        print("\n===== B(最优档) 前后半段稳定性 =====")
        h1 = {k: [c for c in v if c["ts"] <= candles[mid2 - 1]["ts"]]
              for k, v in cbtf.items()}
        h2 = {k: [c for c in v if c["ts"] > candles[mid2 - 1]["ts"]]
              for k, v in cbtf.items()}
        r1 = run_backtest(h1[TF], p, live_gate=cfgB2, **{**common,
                                                         "candles_by_tf": h1})
        r2 = run_backtest(h2[TF], p, live_gate=cfgB2, **{**common,
                                                         "candles_by_tf": h2})
        s1 = summarize(r1, "H1") if "error" not in r1 else None
        s2 = summarize(r2, "H2") if "error" not in r2 else None
        f = (lambda s: f"{s['pnl_u']}U/{s['trades']}笔/dd{s['max_dd_pct']}%"
             if s else "err")
        print(f"  B*: H1={f(s1)}  H2={f(s2)}", flush=True)

    # ── 逐信号评分对照 ──
    print("\n[逐信号 v1 vs v2 评分] ...", flush=True)
    st = super_trend([c["o"] for c in candles], [c["h"] for c in candles],
                     [c["l"] for c in candles], [c["c"] for c in candles],
                     periods=p.get("periods", 15), multiplier=p.get("multiplier", 9.1),
                     src=p.get("src", "hl2"), change_atr=p.get("change_atr", True))
    signals = st_signals(candles, st, TF)
    ts2idx = {c["ts"]: i for i, c in enumerate(candles)}
    open_ts = set()
    ra_open, rb_open = set(), set()
    if "trade_list" in ra:
        ra_open = {t["entry_ts"] for t in ra["trade_list"]}
    if "trade_list" in rb:
        rb_open = {t["entry_ts"] for t in rb["trade_list"]}

    import strategy as strategy_mod
    rows = []
    for s in signals:
        idx = ts2idx.get(s["ts"])
        if idx is None or idx < 40:
            continue
        c_i = candles[:idx + 1]
        cbtf_i = {t: cut_to(arr, s["ts"]) for t, arr in cbtf.items()
                  if arr and arr[0]["ts"] <= s["ts"]}
        full = strategy_mod.evaluate(cbtf_i, p, s)
        a = score_signal(full, c_i, cfgA, cbtf_i, p)
        b = score_signal(full, c_i, cfgB, cbtf_i, p)
        rows.append({
            "ts": s["ts"], "date": ts_fmt(s["ts"]), "type": s["type"],
            "price": s["price"], "score0_3": s["score"],
            "A": {"total": a["total_score"], "action": a["action"],
                  "bd": {k: a["breakdown"][k] for k in
                         ("signal_quality", "er_momentum", "volatility",
                          "mtf_alignment", "breakout_boost", "penalties")}},
            "B": {"total": b["total_score"], "action": b["action"],
                  "bd": {k: round(b["breakdown"][k], 1) for k in
                         ("signal_quality", "er_momentum", "volatility",
                          "mtf_alignment", "breakout_boost", "penalties")},
                  "detail": b.get("detail")},
            "opened_A": s["ts"] in ra_open,
            "opened_B": s["ts"] in rb_open,
        })

    print(f"\n共 {len(rows)} 个翻转信号\n")
    hdr = (f"  {'date':<11}{'dir':<5}{'s3':>3}{'A_total':>8}{'A_act':<11}"
           f"{'B_total':>8}{'B_act':<11}{'A开':>4}{'B开':>4}")
    print(hdr)
    for x in rows:
        diff = x["A"]["total"] - x["B"]["total"]
        mark = " ←差异大" if abs(diff) >= 15 else ""
        print(
            f"  {x['date']:<11}{x['type']:<5}{x['score0_3']:>3}"
            f"{x['A']['total']:>8.1f}{x['A']['action']:<11}"
            f"{x['B']['total']:>8.1f}{x['B']['action']:<11}"
            f"{'●' if x['opened_A'] else '·':>4}{'●' if x['opened_B'] else '·':>4}"
            f"{mark}", flush=True,
        )

    # 差异最大的信号明细（打印 breakdown）
    print("\n===== v1→v2 分数变化最大的 8 个信号 =====")
    for x in sorted(rows, key=lambda r: abs(r["A"]["total"] - r["B"]["total"]),
                    reverse=True)[:8]:
        d = x["B"]["detail"] or {}
        print(f"  {x['date']} {x['type']} s{x['score0_3']} "
              f"v1={x['A']['total']:.1f}→v2={x['B']['total']:.1f}")
        print(f"    A bd: " + " ".join(f"{k}={v:g}" for k, v in x['A']['bd'].items()))
        print(f"    B bd: " + " ".join(f"{k}={v:g}" for k, v in x['B']['bd'].items()))
        if d:
            sq = d.get("signal", {})
            atr = d.get("atr", {})
            mtf = d.get("mtf", {})
            print(f"    v2 detail: body={sq.get('body')} vol={sq.get('volume')} "
                  f"dist={sq.get('dist')} atr_r={atr.get('atr_ratio')} "
                  f"mtf={mtf.get('trend')}({mtf.get('note')})")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "_nvda_score_v2_ab.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"cfg": {"params": p, "thresholds": {
            "full": cfgA.scoring_full_threshold, "half": cfgA.scoring_half_threshold,
            "alert": cfgA.scoring_alert_threshold}},
            "A": sa, "B": sb, "signals": rows}, f, ensure_ascii=False, indent=1)
    print(f"\nWrote {out} | 总耗时 {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
