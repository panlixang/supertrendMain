"""1D 趋势 regime 过滤实验：
  - 从 1h 聚合日线 -> 算 1D SuperTrend(trend), 作为每个 1h 信号的"宏观方向"
  - 对比三档信号过滤（回测均用 趋势跟踪+硬止损 sl=8%）：
      BASE : V3+仅多（无 1D 约束）
      FLAT : 1D 多头才做多 / 1D 空头或震荡时空仓（对应"1D 向下空掉多单"）
      BOTH : 1D 顺势双向（1D多头做多 / 1D空头做空）
  - 看能否抹平 2022/2025 的亏损、把回撤从 82% 压下来。
"""
import sys, datetime as dt, bisect
sys.path.insert(0, 'backend')
import pandas as pd
import bt_pattern_page as bp
from indicators import super_trend as st_fn

def load_csv(fn):
    df = pd.read_csv(fn)
    return [{"ts": int(r.open_time), "o": float(r.open), "h": float(r.high),
            "l": float(r.low), "c": float(r.close), "vol": float(r.vol)}
            for r in df.itertuples()]

base = load_csv('btc_1h_2019_2026.csv')
h4   = load_csv('btc_4h_2019_2026.csv')
sigs, opens, highs, lows, closes, up_plot, dn_plot, flip_idx = bp.build_signals(base, h4)
print(f"loaded 1h={len(base)} flips={len(sigs)}")

# ── 1D 日线聚合 + ST ───────────────────────────────────────────────
def agg_daily(base):
    daily, cur, cur_date = [], None, None
    for c in base:
        d = dt.datetime.utcfromtimestamp(c["ts"] / 1000).date()
        if d != cur_date:
            if cur: daily.append(cur)
            day0 = dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc)
            cur = {"ts": int(day0.timestamp() * 1000), "o": c["o"], "h": c["h"],
                   "l": c["l"], "c": c["c"]}
            cur_date = d
        else:
            cur["h"], cur["l"], cur["c"] = max(cur["h"], c["h"]), min(cur["l"], c["l"]), c["c"]
    if cur: daily.append(cur)
    return daily

daily = agg_daily(base)
d_ts = [c["ts"] for c in daily]
d_st = st_fn([c["o"] for c in daily], [c["h"] for c in daily],
             [c["l"] for c in daily], [c["c"] for c in daily],
             periods=bp.ST_PERIODS, multiplier=bp.ST_MULT, change_atr=True)
d_trend = d_st["trend"]
print(f"daily bars={len(daily)} trend defined={sum(1 for t in d_trend if t is not None)}")

def reg_at(ts):
    idx = bisect.bisect_right(d_ts, ts) - 1
    if idx < 0: idx = 0
    t = d_trend[idx]
    if t is None:
        for k in range(idx, -1, -1):
            if d_trend[k] is not None:
                return d_trend[k]
        return 0
    return t

# ── 回测：趋势跟踪 + 硬止损 ────────────────────────────────────────
def backtest_rev_sl(sigs, highs, lows, closes, flip_idx, sl_pct):
    trades = []
    for s in sigs:
        if s["type"] != "buy":
            continue
        i = s["i"]; side = s["dir"]; long = (side > 0); entry = closes[i]
        coins = bp.NOTIONAL / entry
        fee = entry * coins * bp.FEE
        pnl = -fee
        reason, ex, ex_i = "末根平仓", closes[-1], len(closes) - 1
        closed = False
        use_sl = sl_pct > 0
        sl_px = entry * (1 - sl_pct / 100) if long else entry * (1 + sl_pct / 100)
        for j in range(i + 1, len(closes)):
            if closed:
                break
            if use_sl:
                if long and lows[j] <= sl_px:
                    px = sl_px
                    pnl += (px - entry) * coins - px * coins * bp.FEE
                    fee += px * coins * bp.FEE
                    reason, ex, ex_i, closed = "硬止损", px, j, True
                    break
                if (not long) and highs[j] >= sl_px:
                    px = sl_px
                    pnl += (entry - px) * coins - px * coins * bp.FEE
                    fee += px * coins * bp.FEE
                    reason, ex, ex_i, closed = "硬止损", px, j, True
                    break
            if j in flip_idx:
                px = closes[j]
                pnl += ((px - entry) if long else (entry - px)) * coins - px * coins * bp.FEE
                fee += px * coins * bp.FEE
                reason, ex, ex_i, closed = "反向翻转", px, j, True
                break
        if not closed:
            px = closes[-1]
            pnl += ((px - entry) if long else (entry - px)) * coins - px * coins * bp.FEE
            fee += px * coins * bp.FEE
        trades.append({"ts": s["ts"], "dir": side, "entry": entry, "exit": ex, "i": i,
                       "j": ex_i, "pnl": pnl, "fee": fee, "gross": pnl + fee,
                       "tp1": False, "reason": reason, "ret_pct": pnl / bp.NOTIONAL * 100})
    return trades

REGIMES = [
    ("BASE", "V3+long-only",  lambda s: s["v3_execute"] and s["dir"] > 0),
    ("FLAT", "1D>0 -> long only", lambda s: s["v3_execute"] and s["dir"] > 0 and reg_at(s["ts"]) > 0),
    ("BOTH", "1D-aligned both",   lambda s: s["v3_execute"] and (
        (s["dir"] > 0 and reg_at(s["ts"]) > 0) or (s["dir"] < 0 and reg_at(s["ts"]) < 0))),
]
SL = 8

def year_bounds(y):
    st = int(dt.datetime(y, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    ed = (int(dt.datetime(y + 1, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
          if y < 2026 else int(dt.datetime(2026, 10, 8, tzinfo=dt.timezone.utc).timestamp() * 1000))
    return st, ed

def matrix(siglist, label):
    print(f"\n===== {label}  (收益%/回撤%/笔数 @ SL={SL}%) =====")
    hdr = f"{'year/regime':<14}" + " | " + " | ".join(f"{r[1]:>20}" for r in REGIMES)
    print(hdr); print("-" * len(hdr))
    rows = [("ALL", siglist)] + [(str(y), [s for s in siglist if st_y <= s['ts'] < ed_y])
                                 for y in [2021, 2022, 2025]
                                 for st_y, ed_y in [year_bounds(y)]]
    for tag, sub in rows:
        cells = []
        for key, desc, ok in REGIMES:
            ss = [s for s in sub if ok(s)]
            bt = backtest_rev_sl(ss, highs, lows, closes, flip_idx, SL)
            m = bp.metrics(bt)
            cells.append(f"{m['ret']:+.0f}/{m['max_dd']:.0f}({m['n']})")
        print(f"{tag:<14}" + " | " + " | ".join(f"{c:>20}" for c in cells))

matrix(sigs, "全量 + 困难年 2019-2026")
