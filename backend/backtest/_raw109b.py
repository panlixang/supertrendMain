"""原始 SuperTrend(10x3.0) 信号 · 追加 ST 状态 / 信号K / 信号后 字段。

在 _raw109 的基础上，对每一个翻转信号补充：
  ST flip 前 30 根：
    flip_count    前 30 根内 ST 翻转次数（震荡度，越多越来回洗）
    previous_st_age  上一轮趋势持续根数（= 当前根 − 上一根翻转根）
    ER20         效率比 = |收盘−20根前收盘| / Σ|逐根涨跌|（1=强趋势 0=纯震荡）
    ADX          ADX(14)
    ATR%         ATR/价格×100
  信号K（翻转根）：
    body_atr     实体/ATR
    wick_ratio   影线/实体 = (range−|body|)/|body|（0=光头光脚，越大上/下影越长=拒接）
    volume_ratio Vol/MA(Vol,20)
  信号后（持仓前几根最大顺向偏移，ATR 单位）：
    max3_atr     入场后前 3 根最大有利偏移 / ATR
    max5_atr     入场后前 5 根最大有利偏移 / ATR
  最终：
    出场(反) / 盈亏% / 结果（信号反向直接止盈或止损）

用法：python _raw109b.py
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

from indicators import super_trend, ta_atr, ta_sma, ta_adx
from _live_cfg_backtest import fetch_candles, ts_fmt

SYMBOL = "BTC-USDT-SWAP"
ST_PERIODS, ST_MULT = 10, 3.0
START = int(datetime(2026, 3, 23, tzinfo=timezone.utc).timestamp() * 1000)
BARS_1H, BARS_4H = 5300, 1500


def main():
    t0 = time.time()
    raw1 = fetch_candles(SYMBOL, "1h", BARS_1H)
    print(f"fetch 1h={len(raw1)} ({time.time()-t0:.0f}s)", flush=True)

    candles = [c for c in raw1 if c["ts"] >= START]
    base = len(raw1) - len(candles)
    o = [c["o"] for c in raw1]; h = [c["h"] for c in raw1]
    l = [c["l"] for c in raw1]; c = [c["c"] for c in raw1]; v = [c["vol"] for c in raw1]
    n = len(raw1)

    st1 = super_trend(o, h, l, c, periods=ST_PERIODS, multiplier=ST_MULT, change_atr=True)
    atr = st1["atr"]
    flips_all = st1["flips"] or []
    fi = [f["i"] for f in flips_all]            # 全部翻转根索引（升序）
    range_flips = [f for f in flips_all if f["i"] >= base]
    print(f"区间 ST 翻转 {len(range_flips)} 个", flush=True)

    vol_ma20 = ta_sma(v, 20)
    adx = ta_adx(h, l, c, 14)

    rows = []
    N = len(range_flips)
    for k, f in enumerate(range_flips):
        i = f["i"]; typ = f["type"]; sd = 1 if typ == "buy" else -1
        ts = raw1[i]["ts"]; ci = c[i]; ai = atr[i] or 0.0
        entry = ci

        # ── 前 30 根 ST 状态 ──
        pos = bisect.bisect_left(fi, i)
        prev_age = (i - fi[pos - 1]) if pos > 0 else None
        # flip_count：前 30 根（i-30 .. i-1）内翻转次数
        lo = bisect.bisect_left(fi, i - 30)
        hi = bisect.bisect_left(fi, i)          # 不含当前根
        flip_count = hi - lo
        # ER20
        if i >= 20:
            er_num = abs(c[i] - c[i - 20])
            er_den = sum(abs(c[j] - c[j - 1]) for j in range(i - 19, i + 1))
            er20 = er_num / er_den if er_den > 0 else 0.0
        else:
            er20 = 0.0
        adx_i = adx[i] if (i < len(adx) and adx[i] is not None) else 0.0
        atr_pct = ai / ci * 100 if ci > 0 else 0.0

        # ── 信号K ──
        body = abs(ci - o[i])
        body_atr = body / ai if ai > 0 else 0.0
        rng = h[i] - l[i]
        wick_ratio = (rng - body) / body if body > 0 else 9.99
        vol_r = v[i] / vol_ma20[i] if (vol_ma20[i] is not None and vol_ma20[i] > 0) else 0.0

        # ── 信号后：前 3 / 5 根最大顺向偏移（ATR）──
        exit_i = range_flips[k + 1]["i"] if k + 1 < N else None

        def max_fav(end_bar):
            if end_bar < i + 1:
                return 0.0
            best = 0.0
            for j in range(i + 1, end_bar + 1):
                fav = (h[j] - ci) / ai if sd > 0 else (ci - l[j]) / ai
                if fav > best:
                    best = fav
            return best

        end3 = min(i + 3, exit_i) if exit_i else min(i + 3, n - 1)
        end5 = min(i + 5, exit_i) if exit_i else min(i + 5, n - 1)
        max3 = max_fav(end3)
        max5 = max_fav(end5)

        # ── 最终（信号反向出场）──
        if exit_i is not None:
            exit_px = raw1[exit_i]["c"]
            pnl = (exit_px - entry) / entry * 100 if sd > 0 else (entry - exit_px) / entry * 100
            result = "止盈(反)" if pnl > 0 else "止损(反)"
            bars = exit_i - i
        else:
            exit_px = None; pnl = None; result = "—"; bars = None

        rows.append({
            "idx": k + 1, "date": ts_fmt(ts), "type": typ,
            "flip_count": flip_count, "prev_st_age": prev_age, "er20": er20,
            "adx": adx_i, "atr_pct": atr_pct,
            "body_atr": body_atr, "wick_ratio": wick_ratio, "vol_r": vol_r,
            "max3_atr": max3, "max5_atr": max5,
            "exit": exit_px, "pnl_pct": pnl, "result": result, "bars": bars,
        })

    # ── 落盘 ──
    out_dir = os.path.dirname(__file__)
    txt = os.path.join(out_dir, "_raw109b.txt")
    csvp = os.path.join(out_dir, "_raw109b.csv")
    with open(csvp, "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.writer(fp)
        w.writerow(["idx", "date", "type", "flip_count", "prev_st_age", "er20", "adx",
                    "atr_pct", "body_atr", "wick_ratio", "vol_r", "max3_atr",
                    "max5_atr", "exit", "pnl_pct", "result", "bars"])
        for r in rows:
            w.writerow([r["idx"], r["date"], r["type"], r["flip_count"],
                        r["prev_st_age"] if r["prev_st_age"] is not None else "",
                        round(r["er20"], 3), round(r["adx"], 1), round(r["atr_pct"], 2),
                        round(r["body_atr"], 2), round(r["wick_ratio"], 2), round(r["vol_r"], 2),
                        round(r["max3_atr"], 2), round(r["max5_atr"], 2),
                        round(r["exit"], 1) if r["exit"] is not None else "",
                        round(r["pnl_pct"], 2) if r["pnl_pct"] is not None else "",
                        r["result"], r["bars"] if r["bars"] is not None else ""])

    with open(txt, "w", encoding="utf-8") as fp:
        fp.write(f"原始 SuperTrend(10x3.0) 信号 · 追加 ST状态/信号K/信号后 · {SYMBOL} 1h · "
                 f"{rows[0]['date']}~{rows[-1]['date']} · 共 {len(rows)} 个\n\n")
        hdr = (f"{'#':>3} {'日期':>10} {'方向':>4} {'flip30':>6} {'st_age':>6} {'ER20':>5} "
               f"{'ADX':>5} {'ATR%':>5} {'body':>5} {'wick':>5} {'volR':>5} "
               f"{'max3':>5} {'max5':>5} {'出场(反)':>10} {'盈亏%':>6} {'结果':>8}")
        fp.write(hdr + "\n" + "-" * len(hdr) + "\n")
        for r in rows:
            ex = f"{r['exit']:.1f}" if r["exit"] is not None else "—"
            pl = f"{r['pnl_pct']:+.2f}" if r["pnl_pct"] is not None else "—"
            pa = f"{r['prev_st_age']}" if r["prev_st_age"] is not None else "—"
            br = f"{r['bars']}" if r["bars"] is not None else "—"
            fp.write(f"{r['idx']:>3} {r['date']:>10} {r['type']:>4} {r['flip_count']:>6} "
                     f"{pa:>6} {r['er20']:>5.2f} {r['adx']:>5.1f} {r['atr_pct']:>5.2f} "
                     f"{r['body_atr']:>5.2f} {r['wick_ratio']:>5.2f} {r['vol_r']:>5.2f} "
                     f"{r['max3_atr']:>5.2f} {r['max5_atr']:>5.2f} {ex:>10} {pl:>6} {r['result']:>8}\n")

    # ── 汇总 ──
    done = [r for r in rows if r["pnl_pct"] is not None]
    win = [r for r in done if r["pnl_pct"] > 0]; lose = [r for r in done if r["pnl_pct"] <= 0]
    print(f"信号 {len(rows)} | 止盈 {len(win)} / 止损 {len(lose)} | 胜率 {len(win)/len(done)*100:.1f}%", flush=True)
    print(f"均值 flip30: 胜者 {sum(r['flip_count'] for r in win)/len(win):.2f} "
          f"负者 {sum(r['flip_count'] for r in lose)/len(lose):.2f}", flush=True)
    print(f"均值 ER20 : 胜者 {sum(r['er20'] for r in win)/len(win):.3f} "
          f"负者 {sum(r['er20'] for r in lose)/len(lose):.3f}", flush=True)
    print(f"均值 max3 : 胜者 {sum(r['max3_atr'] for r in win)/len(win):.2f} "
          f"负者 {sum(r['max3_atr'] for r in lose)/len(lose):.2f} (ATR)", flush=True)
    print(f"Wrote {txt}\nWrote {csvp}", flush=True)


if __name__ == "__main__":
    main()
