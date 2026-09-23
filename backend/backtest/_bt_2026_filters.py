"""2026 BTC 1h 原始 SuperTrend(10x3.0) 信号 · 4 条过滤规则回测收益对比。

信号：翻转根收盘开仓，下一根反向翻转收盘平仓（出场=信号反向，无预设 TP/SL）。
名义 1000U（100U×10x），双边手续费 1U/笔（与 backtest/_raw_full.py 一致）。

对比档位：
    baseline   : 不过滤（全部信号）
    all4       : 4 条规则全开
    only_flip  : 仅 ① 连续翻转过滤
    only_vol   : 仅 ② 波动异常过滤
    only_pos   : 仅 ③ 箱体错误位置过滤
    only_candle: 仅 ④ 极端K过滤

用法：python _bt_2026_filters.py
"""
from __future__ import annotations

import bisect
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from indicators import super_trend, ta_atr, ta_sma
from _live_cfg_backtest import fetch_candles

SYMBOL = "BTC-USDT-SWAP"
ST_PERIODS, ST_MULT = 10, 3.0
START = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
BARS_1H = 15600         # 2025+2026 全量 ≈ 630天×24 ≈ 15120 根 + warmup
BOX_N = 48
NOTIONAL = 1000.0
FEE_U = 0.0005 * NOTIONAL * 2   # 双边手续费 ≈ 1U


def compute_feat(o, h, l, c, atr, fi, i, sd):
    """与 backtest/_raw_full.py / pattern_trade.signal_features 完全一致。"""
    pos = bisect.bisect_left(fi, i)
    bslf = (i - fi[pos - 1]) if pos > 0 else 9999
    lo_w = max(0, i - BOX_N + 1)
    lo48 = min(l[lo_w:i + 1]); hi48 = max(h[lo_w:i + 1]); rng48 = hi48 - lo48
    ci = c[i]; ai = atr[i] or 0.0
    range_pos = (ci - lo48) / rng48 if rng48 > 0 else 0.5
    candle_rng = (h[i] - l[i]) / ai if ai > 0 else 0.0
    atr_pct = ai / ci * 100.0 if ci > 0 else 0.0
    return {
        "bars_since_last_flip": bslf,
        "range_position": range_pos,
        "candle_range_ATR": candle_rng,
        "ATR_percent": atr_pct,
    }


def passed(feat, sd, cfg):
    """cfg = set of enabled rule names。返回 (是否放行, 命中的拒绝原因)。"""
    if "flip" in cfg and feat["bars_since_last_flip"] < 20:
        return False, "flip"
    if "vol" in cfg and feat["ATR_percent"] > 0.8:
        return False, "vol"
    if "position" in cfg:
        rp = feat["range_position"]
        if (sd > 0 and rp < 0.3) or (sd < 0 and rp > 0.7):
            return False, "position"
    if "candle" in cfg and feat["candle_range_ATR"] > 3:
        return False, "candle"
    return True, ""


def main():
    t0 = time.time()
    raw1 = fetch_candles(SYMBOL, "1h", BARS_1H)
    print(f"fetch 1h={len(raw1)} ({time.time()-t0:.0f}s)", flush=True)

    candles = [c for c in raw1 if c["ts"] >= START]
    base = len(raw1) - len(candles)
    o = [c["o"] for c in raw1]; h = [c["h"] for c in raw1]
    l = [c["l"] for c in raw1]; c = [c["c"] for c in raw1]; v = [c["vol"] for c in raw1]
    n = len(raw1)

    st = super_trend(o, h, l, c, periods=ST_PERIODS, multiplier=ST_MULT, change_atr=True)
    atr = st["atr"]
    flips_all = st["flips"] or []
    fi = [f["i"] for f in flips_all]
    range_flips = [f for f in flips_all if f["i"] >= base]
    N = len(range_flips)
    print(f"2026 区间 ST 翻转 {N} 个", flush=True)

    # 预计算每个信号的画像 + 反向出场盈亏
    sigs = []
    for k, f in enumerate(range_flips):
        i = f["i"]; typ = f["type"]; sd = 1 if typ == "buy" else -1
        entry = c[i]
        exit_i = range_flips[k + 1]["i"] if k + 1 < N else None
        if exit_i is None:
            continue
        exit_px = c[exit_i]
        pnl = (exit_px - entry) / entry * 100 if sd > 0 else (entry - exit_px) / entry * 100
        profit_u = pnl / 100 * NOTIONAL - FEE_U
        feat = compute_feat(o, h, l, c, atr, fi, i, sd)
        sigs.append({"sd": sd, "feat": feat, "profit_u": profit_u, "pnl": pnl})

    print(f"有效信号（含出场）{len(sigs)} 个\n", flush=True)

    configs = {
        "baseline": set(),
        "all4": {"flip", "vol", "position", "candle"},
        "only_flip": {"flip"},
        "only_vol": {"vol"},
        "only_pos": {"position"},
        "only_candle": {"candle"},
    }

    print(f"{'档位':<12}{'成交':>6}{'胜':>6}{'负':>6}{'胜率%':>8}{'净收益U':>12}{'单笔U':>9}")
    print("-" * 60)
    for name, cfg in configs.items():
        kept = []
        for s in sigs:
            ok, _ = passed(s["feat"], s["sd"], cfg)
            if ok:
                kept.append(s)
        nk = len(kept)
        if nk == 0:
            print(f"{name:<12}{0:>6}{0:>6}{0:>6}{'0.0':>8}{'0.00':>12}{'0.00':>9}")
            continue
        wins = [s for s in kept if s["profit_u"] > 0]
        net = sum(s["profit_u"] for s in kept)
        wr = len(wins) / nk * 100
        print(f"{name:<12}{nk:>6}{len(wins):>6}{nk-len(wins):>6}{wr:>7.1f}{net:>12.2f}{net/nk:>9.2f}")

    # 单条规则各自的拦截量（在全部信号上）
    print("\n各规则单独拦截量（占全部信号）：")
    for rule in ("flip", "vol", "position", "candle"):
        cnt = 0
        for s in sigs:
            ok, _ = passed(s["feat"], s["sd"], {rule})
            if not ok:
                cnt += 1
        print(f"  {rule:<10}: 拦截 {cnt}/{len(sigs)} ({cnt/len(sigs)*100:.1f}%)")


if __name__ == "__main__":
    main()
