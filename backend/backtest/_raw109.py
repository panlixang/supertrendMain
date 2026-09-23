"""原始 SuperTrend(10x3.0) 信号 · 无评分 / 无固定止盈止损 · 出场=信号反向。

对区间 2026-03-23~ 的每一个 SuperTrend 翻转信号（与 _score_signal_list 同 109 个），
输出原始信号画像，不含任何评分/过滤：

  持仓规则：入场=翻转根收盘；出场=下一根反向翻转根收盘（即「信号反向直接止盈/止损」）。
  因此 TP(信号反向) 与 SL(信号反向) 两列都是同一个「反向出场价」——本系统没有预设
  止盈/止损位，反向翻转既是止盈触发也是止损触发，两列相等是设计使然。

诊断字段（均在入场根、用截至该根的数据计算）：
  4H趋势     大周期方向（4H ST + 价 vs 4H EMA20）：多/多·回/空/空·反/—
  箱体位置   近 48 根高低箱体的位置 Pos：突破上沿/突破下沿/震荡中轴/箱体边缘
  突破幅度ATR 翻转根实体越破被突破轨的幅度 = |收盘-被破轨|/ATR；实体>1.5ATR 标「过度」
  Mom12 ATR  动量 = (收盘-12根前收盘)/ATR（带符号，方向中性原值，不截断）
  当K贡献     单根贡献 = (收盘-前根收盘)/(收盘-12根前收盘)，接近 1 表示行情几乎由单根K驱动
  量能变化   Vol/MA(Vol,20)：>1.3 带量 / <0.7 缩量 / 否则 平
  ADX        当前 ADX(14) 与近 5 根变化；>25 趋势、<20 无趋势
  距结构ATR  入场价到近 48 根内最近摆动高低点(±5)的距离/ATR：越小越贴结构、越大越追涨
  MAE        持仓区间最大不利偏移%（买=最低低/入场-1；卖=最高高/入场-1）
  MFE        持仓区间最大有利偏移%（买=最高高/入场-1；卖=最低低/入场-1）
  结果标签   反向出场相对入场的盈亏：止盈(反)/止损(反)/—

用法：python _raw109.py
"""
from __future__ import annotations

import bisect
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from indicators import super_trend, ta_atr, ta_ema, ta_sma, ta_adx
from _live_cfg_backtest import fetch_candles, ts_fmt

SYMBOL = "BTC-USDT-SWAP"
ST_PERIODS, ST_MULT = 10, 3.0
START = int(datetime(2026, 3, 23, tzinfo=timezone.utc).timestamp() * 1000)
BARS_1H, BARS_4H = 5300, 1500
BOX_N, PIV_N, PIV_K = 48, 48, 5


def pivots_in(highs, lows, lo, hi, k):
    """[lo,hi] 内、左右各 k 根的摆动高低点价格（不含端点外推）。"""
    ph, pl = [], []
    for p in range(lo + k, hi - k + 1):
        h = highs[p]
        if all(h >= highs[q] for q in range(p - k, p + k + 1) if q != p):
            ph.append(h)
        l = lows[p]
        if all(l <= lows[q] for q in range(p - k, p + k + 1) if q != p):
            pl.append(l)
    return ph, pl


def main():
    t0 = time.time()
    raw1 = fetch_candles(SYMBOL, "1h", BARS_1H)
    c4 = fetch_candles(SYMBOL, "4h", BARS_4H)
    print(f"fetch 1h={len(raw1)} 4h={len(c4)} ({time.time()-t0:.0f}s)", flush=True)

    candles = [c for c in raw1 if c["ts"] >= START]
    base = len(raw1) - len(candles)
    o = [c["o"] for c in raw1]; h = [c["h"] for c in raw1]
    l = [c["l"] for c in raw1]; c = [c["c"] for c in raw1]; v = [c["vol"] for c in raw1]
    n = len(raw1)

    st1 = super_trend(o, h, l, c, periods=ST_PERIODS, multiplier=ST_MULT, change_atr=True)
    atr = st1["atr"]; trend1 = st1["trend"]; dn = st1["dn"]; up = st1["up"]
    flips = [f for f in (st1.get("flips") or []) if f["i"] >= base]
    print(f"区间 ST 翻转 {len(flips)} 个", flush=True)

    # ── 1h 指标序列 ──
    vol_ma20 = ta_sma(v, 20)
    adx = ta_adx(h, l, c, 14)

    # ── 4H 趋势 ──
    c4o = [x["o"] for x in c4]; c4h = [x["h"] for x in c4]
    c4l = [x["l"] for x in c4]; c4c = [x["c"] for x in c4]; c4ts = [x["ts"] for x in c4]
    st4 = super_trend(c4o, c4h, c4l, c4c, periods=ST_PERIODS, multiplier=ST_MULT, change_atr=True)
    trend4 = st4["trend"]
    ema20_4 = ta_ema(c4c, 20)

    rows = []
    for k, f in enumerate(flips):
        i = f["i"]; typ = f["type"]; sd = 1 if typ == "buy" else -1
        ts = raw1[i]["ts"]; ci = c[i]; ai = atr[i] or 0.0
        entry = ci

        # 出场 = 下一根反向翻转收盘
        exit_px = raw1[flips[k + 1]["i"]]["c"] if k + 1 < len(flips) else None

        # ── 诊断字段（入场根）──
        # 4H 趋势
        j = bisect.bisect_right(c4ts, ts) - 1
        if 0 <= j < len(trend4) and trend4[j] is not None and ema20_4[j] is not None:
            sd4 = trend4[j]; e4 = ema20_4[j]
            if sd4 == 1:
                h4 = "多" if ci > e4 else "多·回"
            else:
                h4 = "空" if ci < e4 else "空·反"
        else:
            h4 = "—"

        # 箱体位置（近 BOX_N 根）
        lo_w = max(0, i - BOX_N + 1); lo48 = min(l[lo_w:i + 1]); hi48 = max(h[lo_w:i + 1])
        rng = hi48 - lo48
        pos = (ci - lo48) / rng if rng > 0 else 0.5
        if pos > 0.8:
            box = "突破上沿"
        elif pos < 0.2:
            box = "突破下沿"
        elif 0.3 <= pos <= 0.7:
            box = "震荡中轴"
        else:
            box = "箱体边缘"

        # 突破幅度 ATR
        ref = (dn[i - 1] if sd > 0 else up[i - 1]) if i > 0 else None
        dist_atr = abs(ci - ref) / ai if (ref is not None and ai > 0) else 0.0
        body_atr = abs(ci - o[i]) / ai if ai > 0 else 0.0
        brk_label = "过度" if body_atr > 1.5 else "正常"

        # Mom12 ATR（带符号原值）
        mom12 = (ci - c[i - 12]) / ai if (i >= 12 and ai > 0) else 0.0

        # 当根 K 贡献比例
        move12 = ci - c[i - 12] if i >= 12 else 0.0
        lastmove = ci - c[i - 1] if i >= 1 else 0.0
        kcontrib = lastmove / move12 if move12 != 0 else 0.0

        # 量能变化
        vol_r = v[i] / vol_ma20[i] if (vol_ma20[i] is not None and vol_ma20[i] > 0) else 0.0

        # ADX
        adx_i = adx[i] if (i < len(adx) and adx[i] is not None) else 0.0
        adx_chg = ((adx[i] - adx[i - 5]) if (i >= 5 and adx[i] is not None and adx[i - 5] is not None) else 0.0)

        # 距结构 ATR（近 PIV_N 根内最近摆动高低点）
        pw_lo = max(0, i - PIV_N); phs, pls = pivots_in(h, l, pw_lo, i, PIV_K)
        near = None
        for px in phs + pls:
            d = abs(px - ci)
            if near is None or d < near:
                near = d
        struct_atr = (near / ai) if (near is not None and ai > 0) else 0.0

        # MAE / MFE（持仓区间 [i, exit_i]）
        if exit_px is not None:
            ej = flips[k + 1]["i"]
            seg_h = h[i:ej + 1]; seg_l = l[i:ej + 1]
            if sd > 0:
                mae = (entry - min(seg_l)) / entry * 100
                mfe = (max(seg_h) - entry) / entry * 100
                pnl = (exit_px - entry) / entry * 100
            else:
                mae = (max(seg_h) - entry) / entry * 100
                mfe = (entry - min(seg_l)) / entry * 100
                pnl = (entry - exit_px) / entry * 100
            result = "止盈(反)" if pnl > 0 else "止损(反)"
            bars = ej - i
        else:
            mae = mfe = pnl = None; result = "—"; bars = None

        rows.append({
            "idx": k + 1, "date": ts_fmt(ts), "type": typ, "entry": entry,
            "tp_rev": exit_px, "sl_rev": exit_px, "pnl_pct": pnl, "result": result,
            "bars": bars, "h4": h4, "box": box, "brk_atr": dist_atr, "brk_label": brk_label,
            "body_atr": body_atr, "mom12": mom12, "kcontrib": kcontrib, "vol_r": vol_r,
            "adx": adx_i, "adx_chg": adx_chg, "struct_atr": struct_atr,
            "mae": mae, "mfe": mfe,
        })

    # ── 落盘 ──
    out_dir = os.path.dirname(__file__)
    txt = os.path.join(out_dir, "_raw109.txt")
    csvp = os.path.join(out_dir, "_raw109.csv")
    with open(csvp, "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.writer(fp)
        w.writerow(["idx", "date", "type", "entry", "tp_rev", "sl_rev", "pnl_pct", "result",
                    "bars", "h4", "box", "brk_atr", "brk_label", "body_atr", "mom12",
                    "kcontrib", "vol_r", "adx", "adx_chg", "struct_atr", "mae", "mfe"])
        for r in rows:
            w.writerow([r["idx"], r["date"], r["type"], round(r["entry"], 1),
                        round(r["tp_rev"], 1) if r["tp_rev"] is not None else "",
                        round(r["sl_rev"], 1) if r["sl_rev"] is not None else "",
                        round(r["pnl_pct"], 2) if r["pnl_pct"] is not None else "",
                        r["result"], r["bars"] if r["bars"] is not None else "",
                        r["h4"], r["box"], round(r["brk_atr"], 2), r["brk_label"],
                        round(r["body_atr"], 2), round(r["mom12"], 2), round(r["kcontrib"], 2),
                        round(r["vol_r"], 2), round(r["adx"], 1), round(r["adx_chg"], 1),
                        round(r["struct_atr"], 2),
                        round(r["mae"], 2) if r["mae"] is not None else "",
                        round(r["mfe"], 2) if r["mfe"] is not None else ""])

    # ── 文本表（UTF-8 直写，避免 shell 重定向乱码）──
    with open(txt, "w", encoding="utf-8") as fp:
        fp.write(f"原始 SuperTrend(10x3.0) 信号画像 · {SYMBOL} 1h · 区间 {rows[0]['date']}~{rows[-1]['date']} · 共 {len(rows)} 个\n")
        fp.write("规则：入场=翻转根收盘；出场=下一根反向翻转收盘（TP/SL 同价=信号反向）。下列字段均为入场根截至该根的计算。\n\n")
        hdr = (f"{'#':>3} {'日期':>10} {'方向':>4} {'入场':>10} {'出场(反)':>10} "
               f"{'盈亏%':>6} {'结果':>8} {'K':>4} {'4H':>5} {'箱体':>8} "
               f"{'突ATR':>6} {'Mom12':>6} {'当K':>5} {'量能':>5} {'ADX':>5} {'距结构':>6} "
               f"{'MAE%':>6} {'MFE%':>6}")
        fp.write(hdr + "\n" + "-" * len(hdr) + "\n")
        for r in rows:
            ex = f"{r['tp_rev']:.1f}" if r["tp_rev"] is not None else "—"
            pl = f"{r['pnl_pct']:+.2f}" if r["pnl_pct"] is not None else "—"
            br = f"{r['bars']}" if r["bars"] is not None else "—"
            ma = f"{r['mae']:+.1f}" if r["mae"] is not None else "—"
            mf = f"{r['mfe']:+.1f}" if r["mfe"] is not None else "—"
            fp.write(f"{r['idx']:>3} {r['date']:>10} {r['type']:>4} {r['entry']:>10.1f} "
                     f"{ex:>10} {pl:>6} {r['result']:>8} {br:>4} {r['h4']:>5} {r['box']:>8} "
                     f"{r['brk_atr']:>6.2f} {r['mom12']:>+6.2f} {r['kcontrib']:>5.2f} "
                     f"{r['vol_r']:>5.2f} {r['adx']:>5.1f} {r['struct_atr']:>6.2f} "
                     f"{ma:>6} {mf:>6}\n")

    # ── 汇总（stdout，纯数字避免乱码）──
    done = [r for r in rows if r["pnl_pct"] is not None]
    win = [r for r in done if r["pnl_pct"] > 0]; lose = [r for r in done if r["pnl_pct"] <= 0]
    print(f"信号 {len(rows)} 个 | 有出场 {len(done)} | 止盈(反) {len(win)} / 止损(反) {len(lose)}", flush=True)
    print(f"原始系统净盈亏% 合计 = {sum(r['pnl_pct'] for r in done):+.2f}%  "
          f"均值 = {sum(r['pnl_pct'] for r in done)/len(done):+.2f}%", flush=True)
    print(f"平均 MAE={sum(r['mae'] for r in done)/len(done):.2f}%  "
          f"平均 MFE={sum(r['mfe'] for r in done)/len(done):.2f}%", flush=True)
    print(f"Wrote {txt}\nWrote {csvp}", flush=True)


if __name__ == "__main__":
    main()
