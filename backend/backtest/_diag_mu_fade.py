# -*- coding: utf-8 -*-
"""临时诊断: MU(43 线上 v1 配置) 逐笔最大浮盈 vs 最终结果。
回答: "浮盈 +10%ROE 没止盈 -> 最后 -30%ROE 止损" 这类单在回测里占比。
用完即删。"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest
from _live_cfg_backtest import fetch_candles, trade_cfg, exit_rules, BIAS_TFS, BARS

LIVE_URL = "http://43.108.10.84:5174/api/trade/symbols"


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "supertrend-bt/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def ts_fmt(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%m-%d")


live = _get(LIVE_URL)
sym = next(s for s in live["symbols"] if s["symbol"] == "MU-USDT-SWAP")
gate_tf = sym["allow_tfs"][0]

# 最近 ~120 天(1h ≈ 2900 根)
LIMIT = 2900
candles = fetch_candles(sym["symbol"], gate_tf, LIMIT)
cbtf = {gate_tf: candles}
for tf in BIAS_TFS:
    if tf == gate_tf:
        continue
    extra = fetch_candles(sym["symbol"], tf, min(LIMIT, BARS.get(tf, 4500)))
    if extra:
        cbtf[tf] = extra

p = sym["params"]
r = run_backtest(
    candles, p,
    init_cash=100.0, fee_rate=0.0005, allow_short=True,
    exit_rules=exit_rules(sym),
    sizing="fixed", margin_usdt=sym["margin_usdt"], leverage=sym["leverage"],
    live_gate=trade_cfg(sym), gate_tf=gate_tf, candles_by_tf=cbtf,
    full_trades=True,
)
if "error" in r:
    print("ERROR:", r["error"])
    sys.exit(1)

lev = sym["leverage"]
tp1 = sym["exit_rules"]["tp1_pct"]
idx = {c["ts"]: i for i, c in enumerate(candles)}
trades = r["trade_list"]

rows = []
for t in trades:
    i0 = idx.get(t["entry_ts"])
    i1 = idx.get(t["exit_ts"])
    if i0 is None or i1 is None or i1 < i0:
        continue
    seg = candles[i0:i1 + 1]
    if t["side"] == "long":
        fav_pct = (max(c["h"] for c in seg) / t["entry"] - 1) * 100
    else:
        fav_pct = (1 - min(c["l"] for c in seg) / t["entry"]) * 100
    rows.append({**t, "fav_pct": fav_pct})  # 价格口径

print(f"MU 43(v1) {gate_tf}  bars={len(candles)}  "
      f"{ts_fmt(candles[0]['ts'])}~{ts_fmt(candles[-1]['ts'])}  lev={lev}x  TP1={tp1}%px")
print(f"总回合={len(rows)}  胜率={r['win_rate']}%  "
      f"avg_win={r['avg_win']}%px({r['avg_win']*lev:+.1f}%ROE)  "
      f"avg_loss={r['avg_loss']}%px({r['avg_loss']*lev:+.1f}%ROE)  "
      f"PF={r['profit_factor']}  stops={r['stop_count']} tp1={r['tp1_count']}")
print("-" * 100)

losses = [t for t in rows if t["pnl_pct"] <= 0]
wins = [t for t in rows if t["pnl_pct"] > 0]


def pct_of(group, cond):
    n = sum(1 for t in group if cond(t))
    return n, (n / len(group) * 100 if group else 0.0)


for tag, grp in (("全部", rows), ("亏损单", losses), ("盈利单", wins)):
    if not grp:
        print(f"{tag}: 无")
        continue
    n10, p10 = pct_of(grp, lambda t: t["fav_pct"] * lev >= 10)   # 曾浮盈 >=10%ROE
    n_tp1, p_tp1 = pct_of(grp, lambda t: t["fav_pct"] >= tp1)    # 曾触达止盈线
    n30, p30 = pct_of(grp, lambda t: t["fav_pct"] * lev >= 30)   # 曾浮盈 >=30%ROE
    favs = sorted(t["fav_pct"] * lev for t in grp)
    med = favs[len(favs) // 2]
    avg_final = sum(t["pnl_pct"] * lev for t in grp) / len(grp)
    print(f"{tag}(n={len(grp)}): 平均最终={avg_final:+.1f}%ROE | "
          f"曾浮盈>=10%ROE: {n10}({p10:.0f}%) | 曾触止盈线{round(tp1*lev)}%ROE: {n_tp1}({p_tp1:.0f}%) | "
          f"曾浮盈>=30%ROE: {n30}({p30:.0f}%) | 最大浮盈中位={med:+.1f}%ROE")

print("-" * 100)
print("亏损但曾浮盈>=10%ROE 的回合明细('到嘴的肉飞了'):")
cnt = 0
for t in sorted([t for t in losses if t["fav_pct"] * lev >= 10],
                key=lambda t: t["fav_pct"] * lev, reverse=True):
    cnt += 1
    if cnt > 15:
        break
    print(f"  {ts_fmt(t['entry_ts'])} {t['side']:>5} 开:{t['entry']:>8.2f} "
          f"最高浮盈:{t['fav_pct']*lev:+6.1f}%ROE({t['fav_pct']:+.2f}%px) -> 最终:{t['pnl_pct']*lev:+6.1f}%ROE 持{t['bars']}根")

out_path = os.path.join(os.path.dirname(__file__), "_diag_mu_fade.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump({"summary": {
        "trades": len(rows), "win_rate": r["win_rate"],
        "avg_win_roe": r["avg_win"] * lev, "avg_loss_roe": r["avg_loss"] * lev,
        "profit_factor": r["profit_factor"],
        "loss_faded_ge10roe": sum(1 for t in losses if t["fav_pct"] * lev >= 10),
        "loss_total": len(losses),
    }, "rows": rows}, f, ensure_ascii=False, indent=1)
print("\nWrote _diag_mu_fade.json")
