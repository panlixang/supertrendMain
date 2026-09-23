"""原始 SuperTrend(10x3.0) 信号 · 2025-01-01 ~ 今 · BTC 1h 全量详细列表。

对区间内每一个 ST 翻转信号输出原始画像（无评分、无固定止盈止损；出场=下一根反向翻转收盘）：
  time                    信号根 UTC 时间(到小时)
  direction               buy / sell
  entry_price             翻转根收盘
  result                  win / loss（反向出场相对入场）
  profit_U                反向出场盈亏(U)，按 1000U 名义仓位(100U×10x)计，扣 1U 双边手续费
  bars_since_last_flip    当前根 − 上一根翻转根（上一轮趋势持续根数）
  range_position          近48根箱体内位置 Pos=(C−Lo)/(Hi−Lo)，0~1
  distance_to_range_high_ATR  (Hi48−C)/ATR
  distance_to_range_low_ATR   (C−Lo48)/ATR
  body_ATR                |C−O|/ATR
  upper_wick_ratio        (H−max(O,C))/|C−O|（实体为0时记9.99）
  lower_wick_ratio        (min(O,C)−L)/|C−O|（实体为0时记9.99）
  candle_range_ATR        (H−L)/ATR（信号K自身振幅）
  mom12_ATR               (C−C12)/ATR（带符号原值）
  current_bar_contribution (C−C1)/(C−C12)（当根贡献）
  volume_ratio            Vol/MA(Vol,20)
  ADX14                   ADX(14)
  ATR_percent             ATR/C×100
  future_max_profit       持仓区间最大有利偏移/ATR（MFE）
  future_max_drawdown     持仓区间最大不利偏移/ATR（MAE）
  bars_to_max_profit      达到 MFE 的 K 数（相对入场根）
  bars_to_max_drawdown    达到 MAE 的 K 数（相对入场根）

用法：python _raw_full.py
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
from _live_cfg_backtest import fetch_candles

SYMBOL = "BTC-USDT-SWAP"
ST_PERIODS, ST_MULT = 10, 3.0
START = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
BARS_1H = 15600          # 2025-01-01~今 ≈ 630天×24 ≈ 15120 根 + warmup
BOX_N = 48
NOTIONAL = 1000.0        # 100U × 10x
FEE_U = 0.0005 * NOTIONAL * 2   # 双边手续费 ≈ 1U


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
    fi = [f["i"] for f in flips_all]
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

        # bars_since_last_flip
        pos = bisect.bisect_left(fi, i)
        bslf = (i - fi[pos - 1]) if pos > 0 else None

        # 箱体（近48根）
        lo_w = max(0, i - BOX_N + 1)
        lo48 = min(l[lo_w:i + 1]); hi48 = max(h[lo_w:i + 1]); rng48 = hi48 - lo48
        range_pos = (ci - lo48) / rng48 if rng48 > 0 else 0.5
        dist_hi = (hi48 - ci) / ai if ai > 0 else 0.0
        dist_lo = (ci - lo48) / ai if ai > 0 else 0.0

        # 信号K 影线/实体
        body = abs(ci - o[i])
        up_w = h[i] - max(o[i], ci); dn_w = min(o[i], ci) - l[i]
        up_r = up_w / body if body > 1e-9 else 9.99
        dn_r = dn_w / body if body > 1e-9 else 9.99
        candle_rng = (h[i] - l[i]) / ai if ai > 0 else 0.0

        mom12 = (ci - c[i - 12]) / ai if (i >= 12 and ai > 0) else 0.0
        move12 = ci - c[i - 12] if i >= 12 else 0.0
        lastmove = ci - c[i - 1] if i >= 1 else 0.0
        contrib = lastmove / move12 if move12 != 0 else 0.0
        vol_r = v[i] / vol_ma20[i] if (vol_ma20[i] is not None and vol_ma20[i] > 0) else 0.0
        adx_i = adx[i] if (i < len(adx) and adx[i] is not None) else 0.0
        atr_pct = ai / ci * 100 if ci > 0 else 0.0

        exit_i = range_flips[k + 1]["i"] if k + 1 < N else None
        if exit_i is not None:
            exit_px = raw1[exit_i]["c"]
            pnl = (exit_px - entry) / entry * 100 if sd > 0 else (entry - exit_px) / entry * 100
            result = "win" if pnl > 0 else "loss"
            profit_u = pnl / 100 * NOTIONAL - FEE_U
            # MFE / MAE 在持仓区间 [i+1, exit_i]
            fmp = 0.0; fmd = 0.0; bmp = 0; bmd = 0
            for j in range(i + 1, exit_i + 1):
                if sd > 0:
                    fav = (h[j] - entry) / ai; adv = (entry - l[j]) / ai
                else:
                    fav = (entry - l[j]) / ai; adv = (h[j] - entry) / ai
                if fav > fmp:
                    fmp = fav; bmp = j - i
                if adv > fmd:
                    fmd = adv; bmd = j - i
            bars = exit_i - i
        else:
            exit_px = None; pnl = None; result = "—"; profit_u = None
            fmp = fmd = bmp = bmd = None; bars = None

        rows.append({
            "time": datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "direction": typ, "entry_price": round(entry, 1), "result": result,
            "profit_U": round(profit_u, 2) if profit_u is not None else "",
            "bars_since_last_flip": bslf if bslf is not None else "",
            "range_position": round(range_pos, 3),
            "distance_to_range_high_ATR": round(dist_hi, 2),
            "distance_to_range_low_ATR": round(dist_lo, 2),
            "body_ATR": round(body / ai, 2) if ai > 0 else 0.0,
            "upper_wick_ratio": round(up_r, 2), "lower_wick_ratio": round(dn_r, 2),
            "candle_range_ATR": round(candle_rng, 2),
            "mom12_ATR": round(mom12, 2), "current_bar_contribution": round(contrib, 2),
            "volume_ratio": round(vol_r, 2), "ADX14": round(adx_i, 1),
            "ATR_percent": round(atr_pct, 2),
            "future_max_profit": round(fmp, 2) if fmp is not None else "",
            "future_max_drawdown": round(fmd, 2) if fmd is not None else "",
            "bars_to_max_profit": bmp if bmp is not None else "",
            "bars_to_max_drawdown": bmd if bmd is not None else "",
        })

    # ── 落盘 ──
    out_dir = os.path.dirname(__file__)
    csvp = os.path.join(out_dir, "_raw_full.csv")
    cols = ["time", "direction", "entry_price", "result", "profit_U", "bars_since_last_flip",
            "range_position", "distance_to_range_high_ATR", "distance_to_range_low_ATR",
            "body_ATR", "upper_wick_ratio", "lower_wick_ratio", "candle_range_ATR",
            "mom12_ATR", "current_bar_contribution", "volume_ratio", "ADX14", "ATR_percent",
            "future_max_profit", "future_max_drawdown", "bars_to_max_profit", "bars_to_max_drawdown"]
    with open(csvp, "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.DictWriter(fp, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    # ── 汇总 ──
    done = [r for r in rows if r["result"] != "—"]
    win = [r for r in done if r["result"] == "win"]
    lose = [r for r in done if r["result"] == "loss"]
    net = sum(float(r["profit_U"]) for r in done)
    print(f"信号 {len(rows)} | win {len(win)} / loss {len(lose)} | 胜率 {len(win)/len(done)*100:.1f}%", flush=True)
    print(f"净盈亏 {net:+.2f}U  均值 {net/len(done):+.2f}U/笔", flush=True)
    print(f"Wrote {csvp}  ({len(rows)} rows)", flush=True)


if __name__ == "__main__":
    main()
