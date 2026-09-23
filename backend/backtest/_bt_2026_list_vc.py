"""2026 BTC 1h · 逐笔详细列表（原始 SuperTrend 10x3.0 信号 + 4 条过滤特征）。

仅展示「同时开 ②波动异常(ATR%>0.8) + ④极端K(candle>3ATR)」两道过滤的结果：
    合计放行 = ②通过 且 ④通过（其余两条规则关闭）。
并给出放行信号在实盘出场（TP1 1.5% 平70% + 保本 + ST 跟踪 + 2%兜底）下的逐笔盈亏。

用法：python _bt_2026_list_vc.py
"""
from __future__ import annotations

import bisect
import csv
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bt_pattern_page as bp
from indicators import super_trend, ta_atr
from _live_cfg_backtest import fetch_candles

bp.NOTIONAL = 1000.0
bp.FEE = 0.05 / 100

SYMBOL = "BTC-USDT-SWAP"
ST_PERIODS, ST_MULT = 10, 3.0
START = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
BARS_1H = 9000
BOX_N = 48


def main():
    t0 = time.time()
    raw1 = fetch_candles(SYMBOL, "1h", BARS_1H)
    print(f"fetch 1h={len(raw1)} ({time.time()-t0:.0f}s)", flush=True)

    o = [c["o"] for c in raw1]; h = [c["h"] for c in raw1]
    l = [c["l"] for c in raw1]; c = [c["c"] for c in raw1]
    st = super_trend(o, h, l, c, periods=ST_PERIODS, multiplier=ST_MULT, change_atr=True)
    atr = st["atr"]
    flips = [f for f in (st["flips"] or []) if f["i"] < len(raw1) and raw1[f["i"]]["ts"] >= START]
    fi = [f["i"] for f in flips]
    flip_idx = set(fi)
    up_plot, dn_plot = st["up_plot"], st["dn_plot"]

    rows = []
    for f in flips:
        i = f["i"]; sd = 1 if f["type"] == "buy" else -1
        pos = bisect.bisect_left(fi, i)
        bslf = (i - fi[pos - 1]) if pos > 0 else 9999
        lo_w = max(0, i - BOX_N + 1)
        lo48 = min(l[lo_w:i + 1]); hi48 = max(h[lo_w:i + 1]); rng48 = hi48 - lo48
        ci = c[i]; ai = atr[i] or 0.0
        rpos = (ci - lo48) / rng48 if rng48 > 0 else 0.5
        crng = (h[i] - l[i]) / ai if ai > 0 else 0.0
        atr_pct = ai / ci * 100.0 if ci > 0 else 0.0
        pass_v = atr_pct <= 0.8          # ② 波动异常：>0.8 拦截 → 通过=不超过
        pass_c = crng <= 3.0             # ④ 极端K：>3 拦截 → 通过=不超过
        rows.append({
            "ts": raw1[i]["ts"], "i": i, "sd": sd, "entry": ci,
            "bslf": bslf, "atr_pct": atr_pct, "rpos": rpos, "crng": crng,
            "pass_v": pass_v, "pass_c": pass_c, "keep": pass_v and pass_c,
        })

    # 实盘出场逐笔盈亏（仅对放行信号）
    kept = [{"i": r["i"], "dir": r["sd"], "ts": r["ts"]} for r in rows if r["keep"]]
    tr = bp.backtest(kept, h, l, c, up_plot, dn_plot, flip_idx)
    pnl_by_i = {t["i"]: t["pnl"] for t in tr}

    n = len(rows)
    nk = len(kept)
    wins = sum(1 for t in tr if t["pnl"] > 0)
    net = sum(t["pnl"] for t in tr)
    print(f"\n2026 信号总数 {n} | ②+④ 放行 {nk} | 胜 {wins} 负 {nk-wins} "
          f"胜率 {wins/nk*100:.1f}% | 实盘净收益 {net:+.2f}U\n")

    hdr = (f"{'时间':<17}{'方向':<5}{'入场':>10}  {'bars':>5}{'ATR%':>7}"
           f"{'箱体Pos':>8}{'candle/ATR':>11}  ②  ④  放行   实盘PnL(U)")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        hhmm = datetime.fromtimestamp(r["ts"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        d = "buy" if r["sd"] > 0 else "sell"
        y = lambda b: "Y" if b else "N"
        pnl = pnl_by_i.get(r["i"])
        pnl_s = f"{pnl:+8.2f}" if pnl is not None else "     -"
        print(f"{hhmm:<17}{d:<5}{r['entry']:>10.1f}  {r['bslf']:>5}{r['atr_pct']:>7.2f}"
              f"{r['rpos']:>8.2f}{r['crng']:>11.2f}  {y(r['pass_v'])}  {y(r['pass_c'])}"
              f"   {'Y' if r['keep'] else 'N'}     {pnl_s}")

    # 落盘 CSV
    out = os.path.join(os.path.dirname(__file__), "_bt_2026_vc.csv")
    with open(out, "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.writer(fp)
        w.writerow(["time", "dir", "entry", "bars_since_last_flip", "ATR_percent",
                    "range_position", "candle_range_ATR", "pass_vol", "pass_candle",
                    "keep", "live_pnl_U"])
        for r in rows:
            hhmm = datetime.fromtimestamp(r["ts"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            w.writerow([hhmm, "buy" if r["sd"] > 0 else "sell", f"{r['entry']:.1f}",
                        r["bslf"], f"{r['atr_pct']:.2f}", f"{r['rpos']:.3f}",
                        f"{r['crng']:.2f}", r["pass_v"], r["pass_c"], r["keep"],
                        f"{pnl_by_i.get(r['i']):.2f}" if r["i"] in pnl_by_i else ""])
    print(f"\nWrote {out} ({n} rows)")


if __name__ == "__main__":
    main()
