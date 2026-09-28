# -*- coding: utf-8 -*-
"""
自定义标签口径（sl_v2 与 sl2_lab 共用，避免两处各写一份导致口径漂移）
====================================================================

`strategy_learning.build_dataset` 只提供 exit / fwd / tpsl 三种标签。
方向类标签在这份数据上被证明没有可学性（walk-forward AUC≈0.5），
但**波动率类标签有**（walk-forward R²≈0.18）。这里补上波动率类标签。

为什么用「未来 horizon 根的已实现波动 / 当前 ATR」：
  - 都是波动量纲，比值接近 1，且与价格水平无关，跨时段可比
  - 注意：ATR 是**价格单位**，已实现波动是**百分比**，必须先统一量纲再比，
    否则算出来的比率没有意义（早期版本踩过这个坑）
"""
from __future__ import annotations

import numpy as np

from indicators import ta_atr

# 这些标签走「自定义」，不用 build_dataset 自带的 pnl/win
CUSTOM_MODES = ("vol", "mfe")


def atr_series(candles, n: int = 14):
    return ta_atr([c["h"] for c in candles], [c["l"] for c in candles],
                  [c["c"] for c in candles], n)


def vol_ratio(candles, i: int, horizon: int, atr) -> float | None:
    """未来 horizon 根的已实现波动 / 当前 ATR（换算成百分比后比）。"""
    cl = [c["c"] for c in candles]
    jmax = min(i + horizon, len(cl) - 1)
    if jmax <= i + 2:
        return None
    a = (atr[i] if atr else None) or 0.0
    if a <= 0 or cl[i] <= 0:
        return None
    rets = [(cl[k] - cl[k - 1]) / cl[k - 1] * 100 for k in range(i + 1, jmax + 1)]
    atr_pct = a / cl[i] * 100
    return float(np.std(rets)) / atr_pct


def mfe_mae(candles, i: int, side: int, horizon: int) -> float | None:
    """最大有利偏移 / 最大不利偏移（衡量这笔进场时机好不好）。"""
    hh = [c["h"] for c in candles]; ll = [c["l"] for c in candles]
    cl = [c["c"] for c in candles]
    jmax = min(i + horizon, len(cl) - 1)
    if jmax <= i:
        return None
    e = cl[i]
    hi = max(hh[i + 1:jmax + 1]); lo = min(ll[i + 1:jmax + 1])
    if side > 0:
        mfe = (hi - e) / e * 100; mae = (e - lo) / e * 100
    else:
        mfe = (e - lo) / e * 100; mae = (hi - e) / e * 100
    return mfe / (mae + 0.25)      # +0.25 平滑，防止除零把比值放大到无意义


def compute_targets(candles, ts_list, mode: str, horizon: int,
                    sides) -> list[float | None]:
    """按 mode 算出与 ts_list 对齐的目标值（含 None 表示该样本不可用）。"""
    ts_idx = {c["ts"]: k for k, c in enumerate(candles)}
    atr = atr_series(candles) if mode == "vol" else None
    out: list[float | None] = []
    for ts, side in zip(ts_list, sides):
        i = ts_idx.get(ts)
        if i is None:
            out.append(None)
            continue
        v = (vol_ratio(candles, i, horizon, atr) if mode == "vol"
             else mfe_mae(candles, i, int(side), horizon))
        out.append(float(v) if v is not None and np.isfinite(v) else None)
    return out
