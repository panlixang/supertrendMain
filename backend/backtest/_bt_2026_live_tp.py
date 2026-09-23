"""2025+2026 BTC 1h · 实盘止盈止损（TP1 1.5% 平70% + 保本 + ST 跟踪 + 2%兜底）下的 4 条过滤规则对比。

出场逻辑复用 bt_pattern_page.backtest（与 position.ExitRules 默认档一致）：
    入场 = ST 翻转根收盘；TP1 触及 ±1.5% 平 70% 并把止损移到开仓价（保本）；
    剩余 30% 跟随 SuperTrend 轨道跟踪，或遇下一根反向翻转收盘平掉；轨道无效按 2% 兜底。
名义 1000U（与前面反向平仓回测同口径），单边 taker 0.05%。

对比档位同 _bt_2026_filters.py：baseline / all4 / only_flip / only_vol / only_pos / only_candle。

用法：python _bt_2026_live_tp.py
"""
from __future__ import annotations

import bisect
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bt_pattern_page as bp
from indicators import super_trend, ta_atr
from _live_cfg_backtest import fetch_candles

# 与前面反向平仓回测同口径：名义 1000U、单边费 0.05%
bp.NOTIONAL = 1000.0
bp.FEE = 0.05 / 100

SYMBOL = "BTC-USDT-SWAP"
ST_PERIODS, ST_MULT = 10, 3.0
START = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
BARS_1H = 15600
BOX_N = 48


def compute_feat(o, h, l, c, atr, fi, i, sd):
    pos = bisect.bisect_left(fi, i)
    bslf = (i - fi[pos - 1]) if pos > 0 else 9999
    lo_w = max(0, i - BOX_N + 1)
    lo48 = min(l[lo_w:i + 1]); hi48 = max(h[lo_w:i + 1]); rng48 = hi48 - lo48
    ci = c[i]; ai = atr[i] or 0.0
    range_pos = (ci - lo48) / rng48 if rng48 > 0 else 0.5
    candle_rng = (h[i] - l[i]) / ai if ai > 0 else 0.0
    atr_pct = ai / ci * 100.0 if ci > 0 else 0.0
    return {"bars_since_last_flip": bslf, "range_position": range_pos,
            "candle_range_ATR": candle_rng, "ATR_percent": atr_pct}


def passed(feat, sd, cfg):
    if "flip" in cfg and feat["bars_since_last_flip"] < 20:
        return False
    if "vol" in cfg and feat["ATR_percent"] > 0.8:
        return False
    if "position" in cfg:
        rp = feat["range_position"]
        if (sd > 0 and rp < 0.3) or (sd < 0 and rp > 0.7):
            return False
    if "candle" in cfg and feat["candle_range_ATR"] > 3:
        return False
    return True


def main():
    t0 = time.time()
    raw1 = fetch_candles(SYMBOL, "1h", BARS_1H)
    print(f"fetch 1h={len(raw1)} ({time.time()-t0:.0f}s)", flush=True)

    o = [c["o"] for c in raw1]; h = [c["h"] for c in raw1]
    l = [c["l"] for c in raw1]; c = [c["c"] for c in raw1]
    st = super_trend(o, h, l, c, periods=ST_PERIODS, multiplier=ST_MULT, change_atr=True)
    atr = st["atr"]
    flips = st["flips"] or []
    fi = [f["i"] for f in flips]
    flip_idx = set(fi)
    up_plot = st["up_plot"]; dn_plot = st["dn_plot"]
    print(f"ST 翻转 {len(flips)} 个", flush=True)

    # 仅统计 START 之后的信号
    sigs = []
    for f in flips:
        if f["i"] >= len(raw1):
            continue
        if raw1[f["i"]]["ts"] < START:
            continue
        i = f["i"]; sd = 1 if f["type"] == "buy" else -1
        feat = compute_feat(o, h, l, c, atr, fi, i, sd)
        sigs.append({"i": i, "dir": sd, "ts": raw1[i]["ts"], "feat": feat})

    print(f"窗口内信号 {len(sigs)} 个\n", flush=True)

    configs = {
        "baseline": set(),
        "all4": {"flip", "vol", "position", "candle"},
        "only_flip": {"flip"},
        "only_vol": {"vol"},
        "only_pos": {"position"},
        "only_candle": {"candle"},
    }

    print(f"{'档位':<12}{'成交':>6}{'胜':>6}{'负':>6}{'胜率%':>8}{'净收益U':>12}{'收益率%':>9}{'单笔U':>9}")
    print("-" * 64)
    for name, cfg in configs.items():
        kept = [s for s in sigs if passed(s["feat"], s["dir"], cfg)]
        tr = bp.backtest(kept, h, l, c, up_plot, dn_plot, flip_idx)
        n = len(tr)
        if n == 0:
            print(f"{name:<12}{0:>6}{0:>6}{0:>6}{'0.0':>8}{'0.00':>12}{'0.00':>9}{'0.00':>9}")
            continue
        wins = sum(1 for t in tr if t["pnl"] > 0)
        net = sum(t["pnl"] for t in tr)
        ret = net / bp.NOTIONAL * 100
        print(f"{name:<12}{n:>6}{wins:>6}{n-wins:>6}{wins/n*100:>7.1f}{net:>12.2f}{ret:>9.2f}{net/n:>9.2f}")


if __name__ == "__main__":
    main()
