# -*- coding: utf-8 -*-
"""被过滤信号解剖：v1 开仓但 v2 拒的信号，是被哪个分项压下去的？

对每笔 filtered 交易重算 v2 breakdown，输出：
  - 逐笔明细（breakdown 全字段 + pnl）
  - 拒因归类表：决定性低分项（该分项明显低于其满分）× 笔数 × 收益
    → 指导 profile 化该调哪个权重。
"""
from __future__ import annotations

import bisect
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import replace  # noqa: E402

from backtest import run_backtest  # noqa: E402
from indicators import super_trend, st_signals  # noqa: E402
from regime_scoring import score_signal  # noqa: E402
from _live_cfg_backtest import exit_rules, trade_cfg, _get, ts_fmt  # noqa: E402

LIVE_URL = "http://47.84.106.154:5174/api/trade/symbols"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data")
# (sym_key, 数据文件, v2 判档阈值)
CASES = [
    ("NVDA-USDT-SWAP", "NVDA.json", 45.0),
    ("CL-USDT-SWAP", "cl_half_cache.json", 55.0),
    ("SKHYNIX-USDT-SWAP", "SKHYNIX.json", 55.0),
]
TF = "1h"
FULL_MAX = {  # 各分项满分（v2 口径）
    "signal_quality": 30.0, "er_momentum": 25.0, "volatility": 15.0,
    "mtf_alignment": 20.0, "breakout_boost": 20.0, "penalties": 0.0,
}
BAD_IF_ABOVE = {  # 分项得分低于该值视为"决定性偏低"候选
    "signal_quality": 15.0, "er_momentum": 15.0, "volatility": 10.0,
    "mtf_alignment": 12.0,
}


def cut_to(candles: list[dict], ts: int) -> list[dict]:
    return candles[:bisect.bisect_right([c["ts"] for c in candles], ts)]


def main():
    live = _get(LIVE_URL)
    for sym_key, fname, thr in CASES:
        name = sym_key.split("-")[0]
        sym = next(s for s in live["symbols"] if s["symbol"] == sym_key)
        p = sym["params"]
        cfgA = trade_cfg(sym)
        cfgB = replace(trade_cfg(sym), score_v2=True,
                       scoring_full_threshold=thr, scoring_half_threshold=thr,
                       scoring_alert_threshold=max(cfgA.scoring_alert_threshold, thr))
        ex = exit_rules(sym)
        cached = json.load(open(os.path.join(DATA, fname), encoding="utf-8"))
        cbtf = {k: v for k, v in cached.items() if isinstance(v, list) and v}
        candles = cbtf[TF]
        common = dict(init_cash=100.0, fee_rate=0.0005, allow_short=True,
                      exit_rules=ex, sizing="fixed",
                      margin_usdt=sym["margin_usdt"], leverage=sym["leverage"],
                      gate_tf=TF, candles_by_tf=cbtf, full_trades=True)
        ra = run_backtest(candles, p, live_gate=cfgA, **common)
        rb = run_backtest(candles, p, live_gate=cfgB, **common)
        if "error" in ra or "error" in rb:
            print(f"\n== {name}: fail"); continue
        open_b = {t["entry_ts"] for t in rb["trade_list"]}
        filtered = [t for t in ra["trade_list"] if t["entry_ts"] not in open_b]
        retained = [t for t in ra["trade_list"] if t["entry_ts"] in open_b]

        st = super_trend([c["o"] for c in candles], [c["h"] for c in candles],
                         [c["l"] for c in candles], [c["c"] for c in candles],
                         periods=p.get("periods", 15), multiplier=p.get("multiplier", 9.1),
                         src=p.get("src", "hl2"), change_atr=p.get("change_atr", True))
        signals = st_signals(candles, st, TF)
        ts2s = {s["ts"]: s for s in signals}
        ts2idx = {c["ts"]: i for i, c in enumerate(candles)}

        import strategy as strategy_mod
        rows = []
        for t in filtered:
            s = ts2s.get(t["entry_ts"])
            if s is None:
                rows.append({"ts": t["entry_ts"], "no_signal": True, "pnl_pct": t["pnl_pct"]})
                continue
            idx = ts2idx[s["ts"]]
            c_i = candles[:idx + 1]
            cbtf_i = {k: cut_to(arr, s["ts"]) for k, arr in cbtf.items()
                      if arr and arr[0]["ts"] <= s["ts"]}
            full = strategy_mod.evaluate(cbtf_i, p, s)
            b = score_signal(full, c_i, cfgB, cbtf_i, p)
            rows.append({
                "ts": s["ts"], "date": ts_fmt(s["ts"]), "side": t["side"],
                "pnl_pct": t["pnl_pct"], "score": b["total_score"],
                "bd": {k: b["breakdown"].get(k) for k in FULL_MAX},
                "mtf_note": (b.get("detail") or {}).get("mtf", {}).get("note"),
            })

        print(f"\n{'=' * 78}\n== {name}: filtered {len(filtered)} 笔 (thr={thr:g}) | "
              f"其中 {sum(1 for r in rows if not r.get('no_signal'))} 笔可定位信号"
              f"\n{'=' * 78}")
        if not rows:
            print("  无"); continue

        # 拒因归类：决定性偏低的分项
        cause_stat: dict[str, dict] = {}
        for r in rows:
            if r.get("no_signal"):
                ck = "no_signal"
            else:
                low = []
                for k, above in BAD_IF_ABOVE.items():
                    v = r["bd"].get(k)
                    if v is not None and v < above:
                        low.append(f"{k}={v:g}")
                ck = " + ".join(low) if low else "其它(偏高分仍被拒?)"
            d = cause_stat.setdefault(ck, {"n": 0, "pnl": 0.0, "pcts": []})
            d["n"] += 1
            d["pnl"] += r["pnl_pct"]
            d["pcts"].append(r["pnl_pct"])
        print("\n  ── filtered 拒因归类 ──")
        for ck, d in sorted(cause_stat.items(), key=lambda kv: -abs(kv[1]["pnl"])):
            print(f"    {ck:<44} n={d['n']:>2}  Σpnl={d['pnl']:+6.2f}%")

        print("\n  ── filtered 逐笔明细 ──")
        for r in sorted(rows, key=lambda x: x["pnl_pct"], reverse=True):
            if r.get("no_signal"):
                print(f"    {ts_fmt(r['ts'])}  no_signal  pnl={r['pnl_pct']:+.2f}%")
                continue
            print(f"    {r['date']} {r['side']:<5} pnl={r['pnl_pct']:+.2f}% "
                  f"v2={r['score']:.0f} | sq={r['bd']['signal_quality']:g} "
                  f"er={r['bd']['er_momentum']:g} vol={r['bd']['volatility']:g} "
                  f"mtf={r['bd']['mtf_alignment']:g} bo={r['bd']['breakout_boost']:g} "
                  f"pen={r['bd']['penalties']:g} | {r['mtf_note'] or ''}")

        rp = sum(t["pnl_pct"] for t in retained)
        fp = sum(t["pnl_pct"] for t in filtered)
        print(f"\n  retained: {len(retained)}笔 Σpnl={rp:+.2f}% | "
              f"filtered: {len(filtered)}笔 Σpnl={fp:+.2f}%")


if __name__ == "__main__":
    main()
