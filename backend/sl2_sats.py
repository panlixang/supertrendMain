# -*- coding: utf-8 -*-
"""
SATS（Self-Aware Trend System）完整移植 —— 独立研究脚本
================================================================================

对象
----
TradingView 上 WillyAlgoTrader 的「Self-Aware Trend System」，脚本 id `sXFWVpmg`。
**源码未公开**，本移植依据：作者官方说明 + ProRealCode 社区完整翻译版（含全部公式与默认参数）。
→ 结论可用于判断方向，但不等于 Pine 原实现的逐字节复刻。

六层机制
--------
1. effATR = rawATR × (0.5 + 0.5·ER)                      效率加权 ATR
2. TQI    = 四因子加权(ER .35 / 波动 .20 / 结构 .25 / 动量 .20)，各自 clamp[0,1]
3. tqiMult= 1 − q + q·(0.6 + 0.8·(1−tqi)^curve)         非线性调制带宽
4. active = sym·(1 − asym·tqi·0.3)，passive = sym·(1 + asym·tqi·0.4)，再 EMA(0.15)
5. character-flip：prevTQI>.55 且 tqi<.25 且 age≥5 且本 bar 未价格翻转 → 强制翻向
6. 交易计划：SL = 最近枢轴 ± 1.5·effATR 与 close ± 1.5·effATR 取更保守者；1R/2R/3R 各平 1/3

本脚本怎么跑
------------
**不修改任何现有文件。** 只在运行时把 `backtest_engine.super_trend` 换成本文件的 SATS 实现，
于是 SATS 的翻向序列直接进入生产那套 `position.py` 出场状态机（`backtest_engine.run_backtest`
内部就是调它）→ 口径与生产一致，结果可与既有 sl2 结论直接对照。

输出
----
A 组  SATS 入场 + 生产出场（equity / fixed 两种 sizing）
B 组  基线：普通 SuperTrend(10, 2.0) 与 (10, 3.0) + 生产出场  ← **决定性对照**
C 组  消融：分别关掉 effATR / TQI / 不对称 / character-flip，看哪层在起作用
D 组  SATS 自家交易计划（枢轴止损 + 1R/2R/3R 各 1/3）
E 组  character-flip 触发次数与冗余度
F 组  逐年拆解 + 买入持有基准

用法
----
    cd backend
    python sl2_sats.py
    python sl2_sats.py --symbols BTC-USDT,ETH-USDT --tfs 1h,4h
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import Counter, defaultdict

import numpy as np

import backtest_engine as BE
from backtest_engine import run_backtest
from sl2_prodexit import prod_rules, yearly
from sl2_tf_sweep import buy_hold, load_tf
from sl2_voltarget import build_trades

# ──────────────────────────────────────────────────────────────
# SATS 默认参数（与社区移植版逐项一致）
# ──────────────────────────────────────────────────────────────
SATS_DEFAULTS = dict(
    atr_len=14, base_mult=2.0,
    use_adaptive=True, er_len=20, adapt_strength=0.5, atr_baseline_len=100,
    use_efatr=True,
    use_tqi=True, quality_strength=0.4, quality_curve=1.5,
    mult_smooth=True, mult_smooth_alpha=0.15,
    use_asym=True, asym_strength=0.5,
    use_charflip=True, charflip_min_age=5, charflip_high=0.55, charflip_low=0.25,
    w_er=0.35, w_vol=0.20, w_struct=0.25, w_mom=0.20,
    struct_len=20, mom_len=10, vol_len=20,
    # 风险（自家交易计划用）
    sl_atr_mult=1.5, tp1_r=1.0, tp2_r=2.0, tp3_r=3.0,
    trade_max_age=100, pivot_len=3,
)


# ──────────────────────────────────────────────────────────────
# 滚动统计（NaN 对齐版，与 Pine 的 ta.sma / ta.stdev 语义一致）
# ──────────────────────────────────────────────────────────────
def _arr(v) -> np.ndarray:
    """list（含 None）→ float ndarray（None→NaN）。"""
    return np.array([np.nan if x is None else float(x) for x in v], float)


def _roll_sum(a: np.ndarray, length: int) -> np.ndarray:
    """**真·滚动和**（不是均值）。ER 的分母必须是路程之和，
    曾经这里误做成均值 → ER 被放大 length 倍 → 乘数变负 → 状态机每根翻转。"""
    n = len(a)
    out = np.full(n, np.nan)
    if length <= 0 or n < length:
        return out
    ok = np.isfinite(a)
    cs = np.concatenate([[0.0], np.cumsum(np.where(ok, a, 0.0))])
    ck = np.concatenate([[0], np.cumsum(ok.astype(int))])
    full = (ck[length:] - ck[:-length]) == length
    idx = np.nonzero(full)[0] + (length - 1)
    out[idx] = (cs[length:] - cs[:-length])[full]
    return out


def _roll_sma(a: np.ndarray, length: int) -> np.ndarray:
    return _roll_sum(a, length) / length


def _roll_std(a: np.ndarray, length: int) -> np.ndarray:
    """总体标准差（Pine ta.stdev 默认 population=True）。"""
    n = len(a)
    out = np.full(n, np.nan)
    if length <= 1 or n < length:
        return out
    m = _roll_sma(a, length)
    m2 = _roll_sma(a * a, length)
    var = m2 - m * m
    var = np.where(np.isfinite(var), np.maximum(var, 0.0), np.nan)
    out = np.sqrt(var)
    return out


def _roll_max(a: np.ndarray, length: int) -> np.ndarray:
    n = len(a)
    out = np.full(n, np.nan)
    for i in range(length - 1, n):
        out[i] = a[i - length + 1:i + 1].max()
    return out


def _roll_min(a: np.ndarray, length: int) -> np.ndarray:
    n = len(a)
    out = np.full(n, np.nan)
    for i in range(length - 1, n):
        out[i] = a[i - length + 1:i + 1].min()
    return out


# ──────────────────────────────────────────────────────────────
# SATS 核心：TQI + 调制 + 不对称 + 状态机
# ──────────────────────────────────────────────────────────────
def sats_series(candles: list[dict], cfg: dict) -> dict:
    """逐 bar 算出 SATS 全部中间量 + 自适应 SuperTrend 状态机。

    返回 dict：
      eff_atr / tqi / active_sm / passive_sm / st_line / trend / flips
      charflip_idx  character-flip 触发的 bar 序号
      warmup        信号生效的起始 bar
    """
    c = cfg
    n = len(candles)
    o = _arr([x["o"] for x in candles])
    h = _arr([x["h"] for x in candles])
    l = _arr([x["l"] for x in candles])
    cl = _arr([x["c"] for x in candles])
    vol = _arr([x.get("vol") or 0.0 for x in candles])

    empty = dict(eff_atr=np.full(n, np.nan), tqi=np.full(n, np.nan),
                 active_sm=np.full(n, np.nan), passive_sm=np.full(n, np.nan),
                 st_line=np.full(n, np.nan), trend=[None] * n, flips=[],
                 charflip_idx=[], warmup=n)
    if n < 200:
        return empty

    # ── 1. ATR / volRatio / ER / effATR ──
    tr = np.full(n, np.nan)
    tr[0] = h[0] - l[0]
    tr[1:] = np.maximum.reduce([h[1:] - l[1:],
                                np.abs(h[1:] - cl[:-1]),
                                np.abs(l[1:] - cl[:-1])])
    # Wilder RMA（= indicators.ta_atr）
    raw_atr = np.full(n, np.nan)
    L = c["atr_len"]
    if n > L:
        prev = np.nanmean(tr[:L])
        raw_atr[L - 1] = prev
        for i in range(L, n):
            prev = (prev * (L - 1) + tr[i]) / L
            raw_atr[i] = prev

    atr_base = _roll_sma(raw_atr, c["atr_baseline_len"])
    with np.errstate(invalid="ignore", divide="ignore"):
        vol_ratio = np.where((atr_base > 0), raw_atr / atr_base, 1.0)

    P = c["er_len"]
    er = np.full(n, np.nan)
    if n > P:
        chg = np.abs(cl[P:] - cl[:-P])
        path = _roll_sum(np.abs(np.diff(cl, prepend=cl[0])), P)
        with np.errstate(invalid="ignore", divide="ignore"):
            er[P:] = np.where(path[P:] > 0, chg / path[P:], 0.0)

    if c["use_efatr"]:
        eff_atr = raw_atr * (0.5 + 0.5 * np.where(np.isfinite(er), er, 0.0))
    else:
        eff_atr = raw_atr.copy()
    # 自检：ER 必须落在 [0,1]，否则整条调制链的符号都会翻
    _erv = er[np.isfinite(er)]
    if _erv.size and (_erv.min() < -1e-9 or _erv.max() > 1 + 1e-9):
        raise AssertionError(f"ER 越界 [{_erv.min():.4f},{_erv.max():.4f}]，检查 _roll_sum")

    # ── 2. TQI 四因子 ──
    f_er = np.clip(np.where(np.isfinite(er), er, 0.0), 0.0, 1.0)

    has_vol = bool(np.any(vol > 0))
    if has_vol:
        vmean = _roll_sma(vol, c["vol_len"])
        vstd = _roll_std(vol, c["vol_len"])
        with np.errstate(invalid="ignore", divide="ignore"):
            volz = np.where(vstd > 0, (vol - vmean) / vstd, 0.0)
            f_vol = np.clip((volz - (-1.0)) / 3.0, 0.0, 1.0)
    else:
        volz = np.zeros(n)
        f_vol = np.clip((vol_ratio - 0.6) / 1.2, 0.0, 1.0)

    s_hi = _roll_max(h, c["struct_len"])
    s_lo = _roll_min(l, c["struct_len"])
    with np.errstate(invalid="ignore", divide="ignore"):
        pos_in = np.where((s_hi - s_lo) > 0, (cl - s_lo) / (s_hi - s_lo), 0.5)
    f_struct = np.clip(np.abs(pos_in - 0.5) * 2.0, 0.0, 1.0)

    M = c["mom_len"]
    f_mom = np.full(n, np.nan)
    if n > M + 1:
        win_chg = cl[M:] - cl[:-M]                      # close - close[M]
        aligned = np.zeros(n - M)
        d1 = np.diff(cl)                                # d1[k] = close[k+1]-close[k]
        for k in range(M):
            bar_chg = cl[M - 1 - k:n - 1 - k] - cl[M - 2 - k:n - 2 - k] \
                if False else d1[M - 1 - k:n - 1 - k]
            up = (win_chg > 0) & (bar_chg > 0)
            dn = (win_chg < 0) & (bar_chg < 0)
            aligned += (up | dn).astype(float)
        f_mom[M:] = aligned / M

    w_sum = c["w_er"] + c["w_vol"] + c["w_struct"] + c["w_mom"] or 1.0
    if c["use_tqi"]:
        tqi = (f_er * c["w_er"] + f_vol * c["w_vol"]
               + f_struct * c["w_struct"] + np.nan_to_num(f_mom) * c["w_mom"]) / w_sum
    else:
        tqi = np.full(n, 0.5)
    tqi = np.clip(np.where(np.isfinite(tqi), tqi, 0.5), 0.0, 1.0)

    # ── 3. 自适应乘数 ──
    if c["use_adaptive"]:
        legacy = 1.0 + c["adapt_strength"] * (0.5 - np.where(np.isfinite(er), er, 0.0))
    else:
        legacy = np.ones(n)
    q = c["quality_strength"]
    qdev = np.clip(1.0 - tqi, 0.0, 1.0)
    tqi_mult = 1.0 - q + q * (0.6 + 0.8 * np.power(qdev, c["quality_curve"]))
    sym = c["base_mult"] * legacy * tqi_mult

    if c["use_tqi"] and c["use_asym"]:
        act_raw = sym * (1.0 - c["asym_strength"] * tqi * 0.3)
        pas_raw = sym * (1.0 + c["asym_strength"] * tqi * 0.4)
    else:
        act_raw = sym.copy()
        pas_raw = sym.copy()
    # 自检：带宽乘数必须为正，负值会让翻转带跑到价格错误的一侧 → 每根 K 线来回翻
    for _nm, _v in (("active", act_raw), ("passive", pas_raw)):
        _vv = _v[np.isfinite(_v)]
        if _vv.size and _vv.min() <= 0:
            raise AssertionError(f"{_nm} 乘数出现非正值 {_vv.min():.4f}")

    active_sm = np.full(n, np.nan)
    passive_sm = np.full(n, np.nan)
    a0 = c["atr_len"] - 1
    for i in range(a0, n):
        if not np.isfinite(active_sm[i - 1]) if i > a0 else True:
            active_sm[i], passive_sm[i] = act_raw[i], pas_raw[i]
        elif c["mult_smooth"]:
            active_sm[i] = active_sm[i - 1] * (1 - c["mult_smooth_alpha"]) \
                + act_raw[i] * c["mult_smooth_alpha"]
            passive_sm[i] = passive_sm[i - 1] * (1 - c["mult_smooth_alpha"]) \
                + pas_raw[i] * c["mult_smooth_alpha"]
        else:
            active_sm[i], passive_sm[i] = act_raw[i], pas_raw[i]

    # ── 4. 状态机（trail + flip + character-flip）──
    warmup = int(max(50, c["atr_len"] + c["atr_baseline_len"],
                     c["er_len"], c["struct_len"], c["mom_len"], c["vol_len"])) + 10
    warmup = min(warmup, n - 2)

    st_line = np.full(n, np.nan)
    trend = [None] * n
    flips: list[dict] = []
    charflip_idx: list[int] = []

    t_dir, t_line, start_bar = 1, cl[0], 0
    for i in range(1, n):
        prev = t_dir
        if i >= c["atr_len"] and np.isfinite(eff_atr[i]) \
                and np.isfinite(active_sm[i]) and np.isfinite(passive_sm[i]):
            if prev == 1:
                act_band = cl[i] - active_sm[i] * eff_atr[i]
                flip_band = cl[i] + passive_sm[i] * eff_atr[i]
            else:
                act_band = cl[i] + active_sm[i] * eff_atr[i]
                flip_band = cl[i] - passive_sm[i] * eff_atr[i]

            if i == c["atr_len"]:
                t_line, t_dir = act_band, 1
            else:
                if prev == 1:
                    if act_band > t_line:
                        t_line = act_band
                    if cl[i] < t_line:
                        t_dir, t_line = -1, flip_band
                    else:
                        t_dir = 1
                else:
                    if act_band < t_line:
                        t_line = act_band
                    if cl[i] > t_line:
                        t_dir, t_line = 1, flip_band
                    else:
                        t_dir = -1

            # character-flip：质量崩塌强制翻向（本 bar 未发生价格翻转才补）
            prev_tqi = tqi[i - 1] if i >= 2 else 0.5
            age = i - start_bar
            price_flipped = (t_dir != prev)
            if (c["use_charflip"] and c["use_tqi"] and prev_tqi > c["charflip_high"]
                    and tqi[i] < c["charflip_low"] and age >= c["charflip_min_age"]
                    and not price_flipped):
                if prev == 1:
                    t_dir, t_line = -1, cl[i] + passive_sm[i] * eff_atr[i]
                else:
                    t_dir, t_line = 1, cl[i] - passive_sm[i] * eff_atr[i]
                charflip_idx.append(i)

            st_line[i] = t_line
            if i >= warmup:
                trend[i] = t_dir

        if t_dir != prev:
            start_bar = i
            if i >= warmup:
                flips.append({"i": i, "type": "buy" if t_dir == 1 else "sell"})

    return dict(eff_atr=eff_atr, tqi=tqi, active_sm=active_sm, passive_sm=passive_sm,
                st_line=st_line, trend=trend, flips=flips,
                charflip_idx=charflip_idx, warmup=warmup, raw_atr=raw_atr,
                vol_ratio=vol_ratio, er=er)


def to_engine_dict(ser: dict) -> dict:
    """把 SATS 序列转成 backtest_engine 期望的 super_trend 返回结构。"""
    line = ser["st_line"]
    up = [None if not np.isfinite(v) else round(float(v), 6) for v in line]
    dn = list(up)                      # st_line(i) 按 trend[i] 取 up 或 dn，两处同值即可
    atr = [None if not np.isfinite(v) else round(float(v), 6) for v in ser["eff_atr"]]
    return {"up": up, "dn": dn, "trend": ser["trend"], "atr": atr,
            "flips": ser["flips"], "up_plot": up, "dn_plot": dn, "ohlc4": []}


_ORIG_SUPER_TREND = BE.super_trend
_BOX = {"st": None}


def _patched_super_trend(*_a, **_kw):
    return _BOX["st"]


# ──────────────────────────────────────────────────────────────
# SATS 自家交易计划（枢轴止损 + 1R/2R/3R 各 1/3）
# ──────────────────────────────────────────────────────────────
def sats_own_plan(candles, ser, cfg, fee_pct, sl_first=True, allow_overwrite=True):
    """SATS 指标自己的交易计划。

    allow_overwrite=True 复刻指标行为：**持仓中出现新翻向会直接覆盖旧单，
    被覆盖的那笔永不记账**（指标源码里 opening 块不检查 tradeClosed）。
    allow_overwrite=False 是修正版：持仓中忽略新信号。
    sl_first：同一根 K 线内止损与止盈同时满足时的处理顺序
              （引擎口径是 sl_first=True，指标源码是止盈优先）。
    """
    c = cfg
    n = len(candles)
    h = _arr([x["h"] for x in candles])
    l = _arr([x["l"] for x in candles])
    cl = _arr([x["c"] for x in candles])
    flip_at = {f["i"]: f["type"] for f in ser["flips"]}
    ef = ser["eff_atr"]

    last_ph, last_pl = 0.0, 0.0
    pl = c["pivot_len"]
    act = None
    trades, n_overwrite = [], 0

    for i in range(n):
        # 枢轴（因果，无未来函数）
        if i >= 2 * pl:
            if h[i - pl] == h[i - 2 * pl:i + 1].max():
                last_ph = float(h[i - pl])
            if l[i - pl] == l[i - 2 * pl:i + 1].min():
                last_pl = float(l[i - pl])

        # 管理已有仓位（开仓那根不管理，与指标一致）
        if act is not None and i > act["i"]:
            long = act["side"] == 1
            adv = l[i] if long else h[i]
            fav = h[i] if long else l[i]
            left = act["left"]

            def _fill_sl():
                px = min(act["sl"], candles[i]["o"]) if long else max(act["sl"], candles[i]["o"])
                act["legs"].append((act["left"], px))
                act["left"] = 0.0
                act["why"] = "止损"

            def _fill_tp(k):
                qfrac = min(1.0 / 3.0, act["left"])
                act["legs"].append((qfrac, act["tp"][k]))
                act["left"] -= qfrac
                act["hit"][k] = True
                act["why"] = f"止盈{k + 1}"

            if sl_first:
                if adv <= act["sl"] if long else adv >= act["sl"]:
                    _fill_sl()
                else:
                    for k in range(3):
                        if left <= 1e-9:
                            break
                        if act["hit"][k]:
                            continue
                        if fav >= act["tp"][k] if long else fav <= act["tp"][k]:
                            _fill_tp(k)
            else:
                for k in range(3):
                    if act["left"] <= 1e-9 and k == 2:
                        break
                    if act["hit"][k]:
                        continue
                    if fav >= act["tp"][k] if long else fav <= act["tp"][k]:
                        _fill_tp(k)
                if act["left"] > 1e-9 and (adv <= act["sl"] if long else adv >= act["sl"]):
                    _fill_sl()

            if act["left"] <= 1e-9:
                trades.append(_close_plan(act, candles, fee_pct, i, act["why"]))
                act = None
            elif i - act["i"] >= c["trade_max_age"]:
                act["legs"].append((act["left"], float(cl[i])))
                act["left"] = 0.0
                trades.append(_close_plan(act, candles, fee_pct, i, "超时"))
                act = None

        # 新信号
        typ = flip_at.get(i)
        if typ:
            if act is not None and allow_overwrite:
                n_overwrite += 1
                act = None
            if act is None:
                entry = float(cl[i])
                side = 1 if typ == "buy" else -1
                a = float(ef[i]) if np.isfinite(ef[i]) else 0.0
                if side == 1:
                    base = last_pl if last_pl > 0 else float(l[i])
                    raw_sl = base - c["sl_atr_mult"] * a
                    min_sl = entry - c["sl_atr_mult"] * a
                    sl = min(raw_sl, min_sl)
                    risk = entry - sl
                    tp = [entry + risk * c["tp1_r"], entry + risk * c["tp2_r"],
                          entry + risk * c["tp3_r"]]
                else:
                    base = last_ph if last_ph > 0 else float(h[i])
                    raw_sl = base + c["sl_atr_mult"] * a
                    min_sl = entry + c["sl_atr_mult"] * a
                    sl = max(raw_sl, min_sl)
                    risk = sl - entry
                    tp = [entry - risk * c["tp1_r"], entry - risk * c["tp2_r"],
                          entry - risk * c["tp3_r"]]
                act = dict(i=i, side=side, entry=entry, sl=sl, tp=tp, risk=risk,
                           left=1.0, hit=[False, False, False], legs=[], why="未平")

    if act is not None:
        act["legs"].append((act["left"], float(cl[-1])))
        act["left"] = 0.0
        trades.append(_close_plan(act, candles, fee_pct, n - 1, "未平"))
    return trades, n_overwrite


def _close_plan(act, candles, fee_pct, i, reason):
    entry, side = act["entry"], act["side"]
    gross = sum(q * side * (px - entry) / entry for q, px in act["legs"]) * 100.0
    return {"i": act["i"], "j": i, "side": side, "bars": i - act["i"],
            "ts": candles[act["i"]]["ts"], "gross": gross,
            "net": gross - 2 * fee_pct, "reason": reason,
            "r": gross / 100.0 * entry / (act["risk"] or 1e-9)}


# ──────────────────────────────────────────────────────────────
# 输出
# ──────────────────────────────────────────────────────────────
HDR = (f"{'方案':<40}{'笔数':>7}{'胜率%':>8}{'收益%':>11}{'买入持有%':>11}"
       f"{'超额pp':>10}{'回撤%':>9}{'均持仓':>8}{'TP1':>7}{'止损':>7}{'反向':>7}")


def show(tag, r, bh_total=None):
    if not r or r.get("error"):
        print(f"  {tag:<40}{(r.get('error') if r else '无结果')}")
        return None
    vs = f"{r['return_pct'] - bh_total:>10.1f}" if bh_total is not None else f"{'—':>10}"
    print(f"  {tag:<40}{r['trades']:>7}{r['win_rate']:>8.1f}{r['return_pct']:>11.2f}"
          f"{bh_total if bh_total is not None else 0:>11.1f}{vs}{r['max_dd_pct']:>9.1f}"
          f"{r['avg_bars']:>8.1f}{r['tp1_count']:>7}{r['stop_count']:>7}{r['reverse_count']:>7}")
    return r


def run_sats(candles, cfg, base, bh_total):
    ser = sats_series(candles, cfg)
    _BOX["st"] = to_engine_dict(ser)
    BE.super_trend = _patched_super_trend
    p = {"periods": cfg["atr_len"], "multiplier": cfg["base_mult"],
         "src": "hl2", "change_atr": True}
    r = run_backtest(candles, p, **base, exit_rules=prod_rules(), full_trades=True)
    BE.super_trend = _ORIG_SUPER_TREND
    return r, ser


def run_base(candles, mult, base, bh_total):
    BE.super_trend = _ORIG_SUPER_TREND
    p = {"periods": 10, "multiplier": mult, "src": "hl2", "change_atr": True}
    return run_backtest(candles, p, **base, exit_rules=prod_rules(), full_trades=True)


# ──────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(description="SATS 完整移植（含消融与基线对照）")
    ap.add_argument("--symbols", default="BTC-USDT,ETH-USDT")
    ap.add_argument("--tfs", default="1h,4h")
    ap.add_argument("--fee", type=float, default=0.0005)
    ap.add_argument("--margin", type=float, default=10.0)
    ap.add_argument("--leverage", type=int, default=3)
    a = ap.parse_args(argv)

    symbols = [s.strip() for s in a.symbols.split(",") if s.strip()]
    tfs = [s.strip() for s in a.tfs.split(",") if s.strip()]
    fee_pct = a.fee * 100

    cells = []
    for sym in symbols:
        for tf in tfs:
            cs = load_tf(sym, tf)
            if len(cs) < 400:
                print(f"[跳过] {sym} {tf} 只有 {len(cs)} 根")
                continue
            cells.append((sym, tf, cs))

    if not cells:
        print("[失败] 无可用数据")
        return 1

    print("=" * 128)
    print("SATS（Self-Aware Trend System）完整移植 · 信号源替换进生产出场状态机")
    print("  依据：TradingView / WillyAlgoTrader（id sXFWVpmg，源码未公开）+ ProRealCode 社区移植版")
    print("  口径：所有列的入场信号不同，出场统一为生产实际出场（TP1 1.5% 平 70% + 保本 + ST 跟踪）")
    print("=" * 128)

    summary = {}
    for sym, tf, cs in cells:
        bh = buy_hold(cs)
        print(f"\n{'━' * 128}")
        print(f"▌{sym}  {tf}  ·  {len(cs)} 根 ≈ {bh['years']:.2f} 年  ·  费率 {fee_pct:.2f}%/边")
        print(f"  买入持有基准：总收益 {bh['total']:+.1f}% · 年化 {bh['cagr']:.1f}% · 最大回撤 {bh['dd']:.1f}%")
        print(f"{'━' * 128}")
        base_eq = dict(init_cash=10000.0, fee_rate=a.fee, sizing="equity")
        base_fx = dict(init_cash=10000.0, fee_rate=a.fee, sizing="fixed",
                       margin_usdt=a.margin, leverage=a.leverage)

        print(f"\n  {HDR}")
        r_sats, ser = run_sats(cs, SATS_DEFAULTS, base_eq, bh["total"])
        show("A1 SATS 入场 · 生产出场 (equity)", r_sats, bh["total"])

        _BOX["st"] = to_engine_dict(ser)
        BE.super_trend = _patched_super_trend
        r_sats_fx = run_backtest(cs, {"periods": SATS_DEFAULTS["atr_len"]}, **base_fx,
                                 exit_rules=prod_rules())
        BE.super_trend = _ORIG_SUPER_TREND
        show(f"A2 SATS 入场 · 生产出场 ({a.margin:.0f}U×{a.leverage}x)", r_sats_fx, bh["total"])

        print(f"\n  ── B 组 决定性对照：普通 SuperTrend，同出场 ──")
        # B0：先量出 SATS 的**实际有效宽度**再对齐 —— 它的带宽是 乘数 × effATR，
        # 而 effATR = rawATR×(0.5+0.5ER)，震荡里只有一半宽 → 名义 baseMult 严重误导。
        _w = ser["warmup"]
        _m = (np.arange(len(cs)) >= _w) & np.isfinite(ser["raw_atr"]) & (ser["raw_atr"] > 0)
        _eff_mult = float(np.nanmedian((ser["active_sm"] * ser["eff_atr"]
                                        / ser["raw_atr"])[_m]))
        print(f"  SATS 实际有效宽度 = active乘数 × effATR / rawATR 中位数 = **{_eff_mult:.2f}×ATR**"
              f"（名义 baseMult = {SATS_DEFAULTS['base_mult']}）← 差 {SATS_DEFAULTS['base_mult'] - _eff_mult:.2f}")
        r_b0 = run_base(cs, round(_eff_mult, 2), base_eq, bh["total"])
        show(f"B0 普通 SuperTrend(10, {_eff_mult:.2f})  ← 宽度对齐", r_b0, bh["total"])
        r_b20 = run_base(cs, 2.0, base_eq, bh["total"])
        show("B1 普通 SuperTrend(10, 2.0)  ← SATS 的名义宽度", r_b20, bh["total"])
        r_b30 = run_base(cs, 3.0, base_eq, bh["total"])
        show("B2 普通 SuperTrend(10, 3.0)  ← 生产同类", r_b30, bh["total"])
        r_b50 = run_base(cs, 5.0, base_eq, bh["total"])
        show("B3 普通 SuperTrend(10, 5.0)", r_b50, bh["total"])

        print(f"\n  ── C 组 消融：逐层关掉，看哪层在起作用 ──")
        abl = {}
        for key, label in (("use_efatr", "C1 关 effATR（用原始 ATR）"),
                           ("use_tqi", "C2 关 TQI 调制（退化为对称固定倍数）"),
                           ("use_asym", "C3 关不对称通道"),
                           ("use_charflip", "C4 关 character-flip")):
            cfg = dict(SATS_DEFAULTS)
            cfg[key] = False
            r_ab, ser_ab = run_sats(cs, cfg, base_eq, bh["total"])
            _mm = float(np.nanmedian(
                (ser_ab["active_sm"] * ser_ab["eff_atr"] / ser_ab["raw_atr"])[_m]))
            show(label + f"  [有效宽度 {_mm:.2f}×]", r_ab, bh["total"])
            abl[key] = (r_ab, ser_ab)

        # C5 精炼版：只留「带宽随趋势质量时变」这一条，去掉 effATR / 不对称 / character-flip
        cfg_ref = dict(SATS_DEFAULTS)
        cfg_ref.update(use_efatr=False, use_asym=False, use_charflip=False)
        r_ref, ser_ref = run_sats(cs, cfg_ref, base_eq, bh["total"])
        _mm = float(np.nanmedian(
            (ser_ref["active_sm"] * ser_ref["eff_atr"] / ser_ref["raw_atr"])[_m]))
        show(f"C5 精炼版（仅 TQI 时变带宽）  [有效宽度 {_mm:.2f}×]", r_ref, bh["total"])
        # B4：C5 的平均宽度对齐 —— 用来判定 C5 的优势来自「时变」还是仅仅「更宽」
        r_b4 = run_base(cs, round(_mm, 2), base_eq, bh["total"])
        show(f"B4 普通 SuperTrend(10, {_mm:.2f})  ← C5 宽度对齐", r_b4, bh["total"])

        print(f"\n  ── E 组 character-flip 触发统计 ──")
        cf = ser["charflip_idx"]
        no_cf = abl["use_charflip"][1]["flips"]
        no_cf_idx = sorted(f["i"] for f in no_cf)
        import bisect
        redundant = 0
        for i in cf:
            k = bisect.bisect_left(no_cf_idx, i)
            near = min([abs(no_cf_idx[j] - i) for j in (k - 1, k)
                        if 0 <= j < len(no_cf_idx)] or [10 ** 9])
            if near <= 3:
                redundant += 1
        print(f"    character-flip 触发 {len(cf)} 次 / 全场 {len(cs)} 根"
              f"（SATS 本身翻向 {len(ser['flips'])} 次）")
        if cf:
            print(f"    其中 {redundant} 次在关掉该机制后 ±3 根内也会自然翻向 → "
                  f"冗余率 {redundant / len(cf) * 100:.0f}%")
            gaps = np.diff(cf)
            print(f"    首次触发在第 {cf[0]} 根 · 两次触发间隔中位数 "
                  f"{np.median(gaps) if len(gaps) else float('nan'):.0f} 根")

        # TQI 调制在默认参数下是不是「空转」——理论零点：tqiMult=1 ⟺ tqi=1−0.5^(1/curve)
        _q = SATS_DEFAULTS["quality_strength"]
        _cp = SATS_DEFAULTS["quality_curve"]
        _tt = ser["tqi"][_m]
        _tm = 1 - _q + _q * (0.6 + 0.8 * np.power(np.clip(1 - _tt, 0, 1), _cp))
        _zero = 1 - 0.5 ** (1 / _cp)
        print(f"    TQI 均值 {_tt.mean():.3f} · 分位 P10/P90 = "
              f"{np.percentile(_tt,10):.3f}/{np.percentile(_tt,90):.3f}")
        print(f"    tqiMult 均值 {_tm.mean():.3f} · 范围 [{_tm.min():.3f}, {_tm.max():.3f}]"
              f" → 相对 baseMult 最多改动 ±{max(abs(_tm.min()-1),abs(_tm.max()-1))*100:.0f}%")
        print(f"    理论空转点 tqi = {_zero:.3f}（tqiMult 恰为 1）"
              f" → 实测均值{'落在空转点' if abs(_tt.mean()-_zero) < 0.05 else '偏离空转点'}"
              f"（偏离 {abs(_tt.mean()-_zero):.3f}）")

        print(f"\n  ── D 组 SATS 自家交易计划（枢轴止损 + 1R/2R/3R 各 1/3）──")
        print(f"    {'方案':<40}{'笔数':>7}{'胜率%':>8}{'总收益%':>11}{'均每笔%':>10}"
              f"{'均持仓':>8}{'被覆盖':>8}")
        for ow, sf, label in ((True, True, "D1 复刻指标（止盈优先·覆盖旧单）"),
                              (True, False, "D2 复刻指标（止损优先·覆盖旧单）"),
                              (False, True, "D3 修正版（止损优先·忽略新信号）")):
            tr, n_ow = sats_own_plan(cs, ser, SATS_DEFAULTS, fee_pct,
                                     sl_first=sf, allow_overwrite=ow)
            if not tr:
                print(f"    {label:<40}{'无交易'}")
                continue
            g = np.asarray([t["net"] for t in tr], float)
            eq = np.cumprod(1 + g / 100.0)
            pk = np.maximum.accumulate(eq)
            dd = float(np.min(eq / pk - 1)) * 100
            tot = float((eq[-1] - 1) * 100)
            win = float((g > 0).mean() * 100)
            hold = float(np.mean([t["bars"] for t in tr]))
            print(f"    {label:<40}{len(tr):>7}{win:>8.1f}{tot:>11.1f}{g.mean():>10.3f}"
                  f"{hold:>8.1f}{n_ow:>8}")
            if ow:
                f_ = Counter(t["reason"] for t in tr)
                print(f"      离场分布：{' '.join(f'{k}×{v}' for k, v in f_.most_common())}"
                      f"   回撤 {dd:.1f}%")

        print(f"\n  ── F 组 逐年拆解（SATS 生产出场 vs 基线 ST(10,2.0)）──")
        if r_sats and r_sats.get("trades_list"):
            yearly(r_sats["trades_list"], fee_pct, "SATS 入场 · 生产出场")
        if r_b20 and r_b20.get("trades_list"):
            yearly(r_b20["trades_list"], fee_pct, "基线 SuperTrend(10,2.0) · 生产出场")

        summary[(sym, tf)] = dict(bh=bh["total"],
                                  sats=r_sats.get("return_pct") if r_sats else None,
                                  b0=r_b0.get("return_pct") if r_b0 else None,
                                  eff_mult=_eff_mult,
                                  b20=r_b20.get("return_pct") if r_b20 else None,
                                  b30=r_b30.get("return_pct") if r_b30 else None,
                                  c1=abl["use_efatr"][0].get("return_pct")
                                  if abl["use_efatr"][0] else None,
                                  c5=r_ref.get("return_pct") if r_ref else None,
                                  b4=r_b4.get("return_pct") if r_b4 else None,
                                  c5_eff=_mm,
                                  sats_dd=r_sats.get("max_dd_pct") if r_sats else None,
                                  sats_n=r_sats.get("trades") if r_sats else None,
                                  n_cf=len(cf))

    # ── 总判决 ──
    print(f"\n{'=' * 128}")
    print("总判决（全部用生产出场口径）")
    print("=" * 128)
    print(f"  {'标的/周期':<16}{'买入持有':>10}{'SATS六层':>11}{'同宽ST':>10}{'名义ST2.0':>11}"
          f"{'生产ST3.0':>11}{'关effATR':>10}{'C5精炼':>10}{'C5同宽ST':>11}")
    b0w = c5w = c1w = c5bw = 0
    for (sym, tf), s in summary.items():
        if s["sats"] is None:
            continue
        b0w += 1 if (s["b0"] is not None and s["sats"] > s["b0"]) else 0
        c1w += 1 if (s["c1"] is not None and s["c1"] > s["sats"]) else 0
        c5w += 1 if (s["c5"] is not None and s["c5"] > s["sats"]) else 0
        c5bw += 1 if (s["b4"] is not None and s["c5"] is not None
                      and s["c5"] > s["b4"]) else 0
        print(f"  {sym + ' ' + tf:<16}{s['bh']:>10.1f}{s['sats']:>11.2f}{s['b0']:>10.2f}"
              f"{s['b20']:>11.2f}{s['b30']:>11.2f}{s['c1']:>10.2f}{s['c5']:>10.2f}"
              f"{s['b4']:>11.2f}")
    n = len(summary)
    print(f"\n  SATS 打赢「同有效宽度普通 ST」：{b0w}/{n}")
    print(f"  关掉 effATR 后变好：{c1w}/{n}     只留 TQI 时变带宽的精炼版变好：{c5w}/{n}")
    print(f"  **C5 精炼版打赢「它自己平均宽度对齐的普通 ST」：{c5bw}/{n}**"
          f"  ← 这才是判定「带宽时变」有没有独立价值的唯一对照")
    print("  生产现役参数是 ST(10,3.0) —— 直接看「SATS六层」那一列与它比大小。")
    print("=" * 128)
    return 0


if __name__ == "__main__":
    sys.exit(main())
