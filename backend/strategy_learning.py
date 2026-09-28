"""
策略学习模块
============
页面结构（前端 StrategyLearningPage 与之一致）：

    交易数据（SuperTrend 原始信号，分品种研究 / 先 BTC，预留其他品种）
        |
        +----------------------+
        |                      |
    LightGBM                聚类
    (预测/找影响因素)       (找市场类型)
        |
        |
      SHAP
    (解释 LightGBM 为什么这么判断)

数据来源：复用 /api/pattern 的 ST 翻转信号（行情状态诊断），
并对每笔信号现算：
  - 原始 ST 信号特征（bodyAtrRatio / flipDensity / wickRatio / trendAge / gapRatio）
  - V3 打分特征 + v3_decide（判断是否属于「V3 过滤后的可交易数据」）
训练标签（出场方式）：tp1 1.5% 平 70% + 剩余 30% 反向信号（下一根 SuperTrend 翻转）平仓。
  入场以 1h K 线收盘确认（close[i]）；pnl = 0.7*1.5% + 0.3*反向段收益率（触及 tp1 时），
  否则整笔 = 反向段收益率；win = (pnl > 0)。不使用 v4-exit 的 SL/TP 逻辑。
ML 目标：预测该笔信号触发交易是否盈利（分类 win）或盈利幅度（回归 pnl）。

LightGBM / SHAP 若未安装（pip install lightgbm shap）会自动降级到 sklearn：
  - LightGBM → HistGradientBoosting
  - SHAP     → permutation importance
"""
from __future__ import annotations

import asyncio
import bisect
import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import (
    accuracy_score, confusion_matrix, f1_score, mean_absolute_error,
    precision_score, r2_score, recall_score, roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

try:
    import lightgbm as lgb
    LGBM_AVAILABLE = True
except Exception:
    LGBM_AVAILABLE = False

try:
    import shap
    SHAP_AVAILABLE = True
except Exception:
    SHAP_AVAILABLE = False

logger = logging.getLogger(__name__)

# ── 预测用特征（全部由 ST 原始信号及其行情上下文派生）──
FEATURE_COLS = [
    "dir", "confidence",
    "bodyAtrRatio", "flipDensity", "wickRatio", "trendAge", "gapRatio",
    "er", "momentum", "atr_trend", "adx", "adx_slope", "flip_count", "volatility_spike",
    "mom5", "atr_contract50", "st_dist_change", "close_ma30_atr", "flip50", "er20",
    "dist_base_ma", "adx_chg20", "er_chg20", "vol100", "slope_htf", "dist_htf_ma",
    "v3_score", "v3_pass",
]

# 聚类只用「行情上下文」特征（市场类型，不把决策/V3 当市场状态）
CLUSTER_COLS = [
    "dir", "er", "momentum", "atr_trend", "adx", "adx_slope", "flip_count", "volatility_spike",
    "mom5", "atr_contract50", "st_dist_change", "close_ma30_atr", "flip50", "er20",
    "dist_base_ma", "adx_chg20", "er_chg20", "vol100", "slope_htf", "dist_htf_ma",
    # ── 新增"锐"因子：波动率状态 / 摆幅回调 / 放量突变 / HTF背离 / 翻转密度 ──
    "atr_ratio50", "realized_vol20", "range_pos20", "pullback20", "body_frac20",
    "vol_ratio20", "vol_ratio50", "flipDensity20", "htf_div", "mom1h_norm",
    # ── 特征组合（交互项）──
    "mom_x_vol", "er_x_adx", "trend_vol", "div_x_dir",
]

FEATURE_CN = {
    "dir": "方向", "confidence": "置信度",
    "bodyAtrRatio": "实体/ATR", "flipDensity": "翻转密度", "wickRatio": "影线比",
    "trendAge": "趋势年龄", "gapRatio": "跳空/ATR",
    "er": "效率比ER", "momentum": "动量%", "atr_trend": "ATR趋势%", "adx": "ADX",
    "adx_slope": "ADX斜率", "flip_count": "翻转次数", "volatility_spike": "波动尖峰",
    "mom5": "5根动量", "atr_contract50": "波动收缩", "st_dist_change": "ST距离变化",
    "close_ma30_atr": "偏离MA30", "flip50": "50根翻转", "er20": "ER20",
    "dist_base_ma": "基础MA距离", "adx_chg20": "ADX变化20", "er_chg20": "ER变化20",
    "vol100": "百根波动", "slope_htf": "4h斜率", "dist_htf_ma": "4hMA距离",
    "v3_score": "V3评分", "v3_pass": "V3通过",
    "atr_ratio50": "波动率状态", "realized_vol20": "近20波动", "range_pos20": "区间位置",
    "pullback20": "方向回撤", "body_frac20": "实体占比", "vol_ratio20": "量比20",
    "vol_ratio50": "量比50", "flipDensity20": "翻转密度20", "htf_div": "HTF背离",
    "mom1h_norm": "1h动量ATR",
    "mom_x_vol": "动量×量比", "er_x_adx": "ER×ADX", "trend_vol": "趋势×波动",
    "div_x_dir": "背离×方向",
}

# 训练结果缓存（让 SHAP 复用同一模型）
_CACHE: Dict[str, dict] = {}


# ──────────────────────────────────────────────────────────────
# 1. 构建交易数据集（SuperTrend 原始信号）
# ──────────────────────────────────────────────────────────────
def _raw_st_features(candles: List[dict], i: int, atr, flips_sorted: List[int]) -> dict:
    c = candles[i]
    o, h, l, cl = c["o"], c["h"], c["l"], c["c"]
    a = atr[i] if (atr and atr[i]) else 0.0
    body = abs(cl - o)
    bodyAtrRatio = body / a if a else 0.0
    flipDensity = sum(1 for f in flips_sorted if i - 50 < f <= i) / 50.0
    rng = (h - l)
    wickRatio = (rng - body) / body if body > 1e-12 else 0.0
    prev = [f for f in flips_sorted if f < i]
    trendAge = (i - prev[-1]) if prev else i
    prev_c = candles[i - 1]["c"] if i > 0 else cl
    gapRatio = abs(o - prev_c) / a if a else 0.0
    return {
        "bodyAtrRatio": round(bodyAtrRatio, 4),
        "flipDensity": round(flipDensity, 4),
        "wickRatio": round(wickRatio, 4),
        "trendAge": int(trendAge),
        "gapRatio": round(gapRatio, 4),
    }


def _sharp_features(candles, i, atr, flips_sorted, htf_ts, htf_cl, side) -> dict:
    """更"锐"的因子：波动率状态、近期摆幅/回调深度、放量突变、HTF 背离。"""
    cl = [c["c"] for c in candles]
    hi = [c["h"] for c in candles]
    lo = [c["l"] for c in candles]
    vo = [c["vol"] for c in candles]
    avg = lambda xs: (lambda ys: (sum(ys) / len(ys)) if ys else 0.0)([x for x in xs if x is not None])
    out = {}

    # 1) 波动率状态：当前 ATR 相对近 50 根均值（扩张/收缩）
    if i >= 50 and atr:
        a50 = avg(atr[i - 50:i])
        out["atr_ratio50"] = round(atr[i] / a50, 4) if a50 else 0.0
    else:
        out["atr_ratio50"] = 0.0
    # 近 20 根已实现波动率（收盘收益 std）相对 ATR
    if i >= 20:
        rets = [abs(cl[t] - cl[t - 1]) for t in range(i - 19, i + 1)]
        rv = (sum(r * r for r in rets) / len(rets)) ** 0.5
        a_ref = avg(atr[i - 50:i]) if i >= 50 else (atr[i] if atr else 0.0)
        out["realized_vol20"] = round(rv / (a_ref + 1e-12), 4)
    else:
        out["realized_vol20"] = 0.0

    # 2) 近期摆幅 / 回调深度（20 根区间位置 + 方向化回撤 + 实体占比）
    if i >= 20:
        hh = max(hi[i - 20:i + 1]); ll = min(lo[i - 20:i + 1])
        rng = (hh - ll) or 1e-12
        out["range_pos20"] = round((cl[i] - ll) / rng, 4)
        out["pullback20"] = round(((hh - cl[i]) / rng) if side > 0
                                  else ((cl[i] - ll) / rng), 4)
        bodies = [abs(candles[t]["c"] - candles[t]["o"]) for t in range(i - 19, i + 1)]
        out["body_frac20"] = round(avg(bodies) / (avg(atr[i - 20:i + 1]) + 1e-12), 4)
    else:
        out["range_pos20"] = 0.0; out["pullback20"] = 0.0; out["body_frac20"] = 0.0

    # 3) 成交放量突变
    if i >= 20:
        v20 = avg(vo[i - 20:i]); v50 = avg(vo[i - 50:i]) if i >= 50 else v20
        out["vol_ratio20"] = round(vo[i] / (v20 + 1e-12), 4)
        out["vol_ratio50"] = round(vo[i] / (v50 + 1e-12), 4)
    else:
        out["vol_ratio20"] = 0.0; out["vol_ratio50"] = 0.0

    # 4) 翻转密度（近 20 根）—— 直接刻画"震荡无序期"
    fd = sum(1 for f in flips_sorted if i - 20 < f <= i) / 20.0
    out["flipDensity20"] = round(fd, 4)

    # 5) HTF 背离：1h 近 10 根动量 与 4h 近 6 根(≈24h)动量 是否同向
    if htf_cl and i >= 10:
        k = bisect.bisect_right(htf_ts, candles[i]["ts"]) - 1
        if k >= 6:
            mom1h = cl[i] - cl[max(0, i - 10)]
            mom4h = htf_cl[k] - htf_cl[k - 6]
            out["htf_div"] = round(-1.0 if (mom1h * mom4h < 0) else 1.0, 4)
            out["mom1h_norm"] = round(mom1h / (atr[i] + 1e-12), 4)
        else:
            out["htf_div"] = 0.0; out["mom1h_norm"] = 0.0
    else:
        out["htf_div"] = 0.0; out["mom1h_norm"] = 0.0
    return out


def _classify_regime6(m: dict, sharp: dict, feats: dict, side: int):
    """为 ML 过滤重新设计的 regime 分类（6 类 + 震荡无序剔除标记）。

    判别轴：趋势强度(ADX/ER) × 阶段(ADX斜率/动量) × 无序度(20根翻转密度)。
    直接服务于「哪些 ST 翻转可学」：震荡无序期（反复跳信号、无趋势无波动）被标
    is_disorder=True 剔除；其余按用户要求的 6 类细分。
    阈值基于 1h BTC 的 ADX(0-100)/动量(%)尺度经验设定，后续可按分布微调。
    """
    adx = m.get("adx") or 0.0
    adx_slope = m.get("adx_slope") or 0.0
    er = m.get("er") or 0.0
    mom = m.get("momentum") or 0.0
    flip20 = sharp.get("flipDensity20") or 0.0
    atr_ratio = sharp.get("atr_ratio50") or 0.0
    htf_div = sharp.get("htf_div") or 0.0

    # 震荡无序期：20 根内反复跳 ST 信号（>=2 次翻转）、且无明确趋势 → 直接剔除
    # （按真实分布 flip20 中位数 0.05、p75 0.10、最大 0.20，>=0.10 即"反复跳"）
    if flip20 >= 0.10 and adx < 23:
        return ("choppy_disorder", "震荡无序期", True)

    trending = (adx >= 23) and (er > 0.22)
    if trending:
        # 趋势末期：ADX 见顶回落，或多头价格却与 4h 动量背离
        if adx_slope < -5 or (htf_div < 0 and adx >= 25):
            return ("trend_end", "趋势末期", False)
        # 趋势启动：ADX 中等且仍在爬升
        if adx < 28 and adx_slope > 3:
            return ("trend_init", "趋势启动", False)
        return ("trend_run", "趋势", False)

    # 非趋势 → 震荡系
    # 震荡开启：刚从趋势转弱、翻转密度尚低（有序进入盘整）
    if adx_slope < -5 and flip20 < 0.10:
        return ("range_start", "震荡开启", False)
    # 震荡末期：波动收缩、ADX 极低、翻转稀疏（压缩蓄势，临突破）
    if atr_ratio < 1.0 and adx < 18 and flip20 < 0.05:
        return ("range_end", "震荡末期", False)
    return ("range_mid", "震荡", False)


# 各周期一根 K 线对应的小时数（用于把「年」换算成 K 线根数）
_TF_HOURS = {"1m": 1/60, "3m": 0.05, "5m": 5/60, "15m": 0.25, "30m": 0.5,
             "1h": 1, "2h": 2, "4h": 4, "6h": 6, "12h": 12, "1d": 24, "1w": 168, "1M": 720}

# 已拉取的长历史 K 线缓存，避免训练/聚类/SHAP 反复向 OKX 翻页
_CANDLE_CACHE: Dict[str, list] = {}
# 已构建的交易数据集缓存（按 品种+周期+窗口 命中）
_DS_CACHE: Dict[str, dict] = {}


def compute_limit(base_tf: str, years: int = 5) -> int:
    """把「N 年」换算成该周期的 K 线根数（上限 8 万根，防止 15m 过度翻页）。"""
    h = _TF_HOURS.get(base_tf, 1)
    return min(80000, int(round(years * 365 * 24 / h)))


async def build_dataset(symbol: str, base_tf: str, limit: int = None, years: int = 5,
                        label_mode: str = "exit", horizon: int = 20,
                        tp_pct: float = 2.5, sl_pct: float = 2.0) -> Optional[dict]:
    """取 SuperTrend 原始信号数据 + 现算原始/V3 特征，拼成 ML 数据集。

    label_mode:
      - "exit": 出场标签 = tp1 1.5% 平 70% + 剩余 30% 反向信号平仓（与实盘一致）。
      - "fwd" : 标签 = 未来 horizon 根（按信号方向）收益率；分类看是否盈利、回归看幅度。

    直接走 history.fetch_candles 向 OKX 翻页拉取（不受本地库 maxlen 上限约束），
    因此能拿到 5 年这种超长历史；拉到的 K 线缓存复用。
    """
    from history import fetch_candles
    from indicators import super_trend
    from router import _pattern_candles, _pattern_impl
    from signal_v3 import features_from_candles, v3_decide

    symbol = (symbol or "BTC-USDT").strip().upper()
    if limit is None:
        limit = compute_limit(base_tf, years)

    ds_key = f"{symbol}|{base_tf}|{limit}|{label_mode}|{horizon}|{tp_pct}|{sl_pct}"
    candle_key = f"{symbol}|{base_tf}|{limit}"  # K 线与标签模式无关，单独缓存
    if ds_key in _DS_CACHE:
        return _DS_CACHE[ds_key]

    # 1) 拉取长历史 K 线（带缓存）
    bcandles = _CANDLE_CACHE.get(candle_key)
    if bcandles is None:
        loop = asyncio.get_event_loop()
        try:
            raw = await loop.run_in_executor(None, fetch_candles, base_tf, limit, symbol)
        except Exception as e:
            logger.warning(f"策略学习 拉取K线失败 [{symbol} {base_tf} {limit}]: {e}")
            raw = None
        if not raw:
            return None
        bcandles = [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol}
                    for c in raw]
        _CANDLE_CACHE[candle_key] = bcandles

    # 2) 复用 /api/pattern 的信号 + 行情状态诊断（直接喂入 K 线，跳过本地库上限）；
    #    训练标签在下方按「tp1 1.5% 平 70% + 剩余 30% 反向信号平仓」现算（不用 v4-exit）。
    resp = await _pattern_impl(symbol=symbol, base_tf=base_tf, limit=limit, candles=bcandles)
    if not resp or resp.get("error") or not resp.get("base"):
        return None

    base = resp["base"]
    candles = base.get("candles") or []
    signals = base.get("signals") or []
    if not candles or not signals:
        return None

    # 本地重算 ST（与 /api/pattern 同参数 10/3.0），用于原始信号特征
    o = [c["o"] for c in candles]; h = [c["h"] for c in candles]
    l = [c["l"] for c in candles]; cl = [c["c"] for c in candles]
    st = super_trend(o, h, l, cl, periods=10, multiplier=3.0, change_atr=True)
    atr = st["atr"]
    flips_sorted = sorted(f["i"] for f in st["flips"])

    ts_to_idx = {c["ts"]: i for i, c in enumerate(candles)}
    candles_htf_raw = await _pattern_candles(symbol, "4h", min(limit // 4, 12000)) or []
    candles_htf = [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol}
                   for c in candles_htf_raw]
    htf_ts = [c["ts"] for c in candles_htf]
    htf_cl = [c["c"] for c in candles_htf]

    rows = []
    for sig in signals:
        i = ts_to_idx.get(sig.get("ts"))
        if i is None:
            continue
        side = int(sig.get("dir") or 1)
        entry = cl[i]
        # ── 标签：按 label_mode 计算 ──
        if label_mode == "fwd":
            # 未来 horizon 根（按信号方向）收益率：进场(close[i])持 horizon 根后平仓的盈亏
            j = i + horizon
            if j >= len(cl):
                continue
            pnl = ((cl[j] - entry) / entry * 100 if side > 0
                   else (entry - cl[j]) / entry * 100)
            exit_type = f"fwd{horizon}"
        elif label_mode == "exit":
            # exit 模式：tp1 1.5% 平 70% + 剩余 30% 反向信号（下一根 ST 翻转）平仓。
            #   入场以 1h K 线收盘确认（close[i]）；若 tp1 未触及则整笔反向平仓。
            #   若信号之后没有反向信号（末笔），无法判定出场则跳过。
            p = bisect.bisect_right(flips_sorted, i)
            if p >= len(flips_sorted):
                continue
            nf = flips_sorted[p]
            tp_pct = 1.5 / 100.0
            tp_price = entry * (1 + tp_pct) if side > 0 else entry * (1 - tp_pct)
            # 自下一根起检查是否触及 tp1（多:最高价≥tp_price；空:最低价≤tp_price）
            hit = False
            for k in range(i + 1, nf + 1):
                if side > 0:
                    if h[k] >= tp_price:
                        hit = True
                        break
                else:
                    if l[k] <= tp_price:
                        hit = True
                        break
            reverse_pnl = ((cl[nf] - entry) / entry * 100 if side > 0
                           else (entry - cl[nf]) / entry * 100)
            pnl = (0.7 * (tp_pct * 100) + 0.3 * reverse_pnl) if hit else reverse_pnl
            exit_type = "tp1_reverse"
        elif label_mode == "tpsl":
            # 结构化标签：未来 horizon 根内，先触 +tp_pct(TP) 还是先触 -sl_pct(SL)。
            #   多: TP=high>=entry*(1+tp), SL=low<=entry*(1-sl); 空反之。
            #   先触 TP → win=1（动量延续）；先触 SL → win=0；都未触 → 以末根净收益符号判定。
            #   回归目标 = 首次触及时了结的收益率（TP→+tp*100，SL→-sl*100，未触→末根净收益）。
            tp = tp_pct / 100.0
            sl = sl_pct / 100.0
            tp_price = entry * (1 + tp) if side > 0 else entry * (1 - tp)
            sl_price = entry * (1 - sl) if side > 0 else entry * (1 + sl)
            jmax = min(i + horizon, len(cl) - 1)
            touch = 0  # 1=TP先, -1=SL先, 0=都未触
            for k in range(i + 1, jmax + 1):
                if side > 0:
                    if h[k] >= tp_price:
                        touch = 1; break
                    if l[k] <= sl_price:
                        touch = -1; break
                else:
                    if l[k] <= tp_price:
                        touch = 1; break
                    if h[k] >= sl_price:
                        touch = -1; break
            if touch == 1:
                win = 1; pnl = tp * 100
            elif touch == -1:
                win = 0; pnl = -sl * 100
            else:
                net = ((cl[jmax] - entry) / entry * 100 if side > 0
                       else (entry - cl[jmax]) / entry * 100)
                win = 1 if net > 0 else 0
                pnl = net
            exit_type = f"tpsl{tp_pct}_{sl_pct}_h{horizon}"
        raw = _raw_st_features(candles, i, atr, flips_sorted)
        sharp = _sharp_features(candles, i, atr, flips_sorted, htf_ts, htf_cl, side)
        feats = features_from_candles(candles, i, side, candles_htf=candles_htf) or {}
        dec = v3_decide(side, feats) if feats else {
            "score": 0, "path": "未通过", "execute": False, "fused": False}
        m = sig.get("metrics") or {}
        # 特征组合（交互项，便于树模型更易切分）
        combo = {
            "mom_x_vol": round((m.get("momentum") or 0) * sharp["vol_ratio20"], 4),
            "er_x_adx": round((m.get("er") or 0) * (m.get("adx") or 0), 4),
            "trend_vol": round(sharp["atr_ratio50"] * (m.get("adx") or 0), 4),
            "div_x_dir": round(sharp["htf_div"] * side, 4),
        }
        reg6 = _classify_regime6(m, sharp, feats, side)
        rows.append({
            "ts": sig.get("ts"), "type": sig.get("type"), "dir": side,
            "pnl": round(pnl, 3), "win": 1 if pnl > 0 else 0,
            "exit_type": exit_type,
            "regime": sig.get("regime"), "regime_cn": sig.get("regime_cn"),
            "confidence": sig.get("confidence"), "tradeable": sig.get("tradeable"),
            # 原始 ST 信号特征
            **raw,
            # 行情状态诊断
            "er": m.get("er"), "momentum": m.get("momentum"),
            "atr_trend": m.get("atr_trend"), "adx": m.get("adx"),
            "adx_slope": m.get("adx_slope"), "flip_count": m.get("flip_count"),
            "volatility_spike": m.get("volatility_spike"),
            # V3 k 线派生特征
            "mom5": feats.get("mom5"), "atr_contract50": feats.get("atr_contract50"),
            "st_dist_change": feats.get("st_dist_change"),
            "close_ma30_atr": feats.get("close_ma30_atr"),
            "flip50": feats.get("flip50"), "er20": feats.get("er20"),
            "dist_base_ma": feats.get("dist_base_ma"),
            "adx_chg20": feats.get("adx_chg20"), "er_chg20": feats.get("er_chg20"),
            "vol100": feats.get("vol100"), "slope_htf": feats.get("slope_htf"),
            "dist_htf_ma": feats.get("dist_htf_ma"),
            # V3 决策
            "v3_score": dec.get("score"), "v3_path": dec.get("path"),
            "v3_pass": bool(dec.get("execute")), "v3_fused": bool(dec.get("fused")),
            # 新锐因子
            **sharp,
            # 特征组合
            **combo,
            # 新 regime 分类（6 类 + 震荡无序剔除标记）
            "regime6": reg6[0], "regime6_cn": reg6[1], "is_disorder": reg6[2],
        })

    n = len(rows)
    n_win = sum(r["win"] for r in rows)
    n_v3 = sum(1 for r in rows if r["v3_pass"])
    result = {
        "symbol": symbol, "base_tf": base_tf, "candles_n": len(candles),
        "n_total": n, "n_win": n_win,
        "win_rate": round(n_win / n * 100, 1) if n else 0.0,
        "avg_pnl": round(float(np.mean([r["pnl"] for r in rows if r["pnl"] is not None])), 3) if n else 0.0,
        "n_v3_pass": n_v3,
        "rows": rows, "features": FEATURE_COLS, "regime_stats": resp.get("regime_stats"),
    }
    _DS_CACHE[ds_key] = result
    return result


def _df_from(rows: List[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for c in FEATURE_COLS:
        if c not in df.columns:
            df[c] = 0.0
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    return df


# ──────────────────────────────────────────────────────────────
# 2. LightGBM 训练（预测 / 找影响因素）
# ──────────────────────────────────────────────────────────────
def train_lightgbm(rows: List[dict], task: str = "cls", test_size: float = 0.3, seed: int = 42,
                   time_split: bool = False):
    if len(rows) < 20:
        raise ValueError(f"样本不足（{len(rows)} 笔），至少需要 20 笔才能训练")
    if time_split:
        rows = sorted(rows, key=lambda r: r.get("ts", 0) or 0)
    df = _df_from(rows)
    X = df[FEATURE_COLS].values.astype(float)
    y = (df["win"].values.astype(int) if task == "cls"
         else df["pnl"].values.astype(float))
    if time_split:
        n = int(len(df) * (1 - test_size))
        idx = np.arange(len(df))
        itr, ite = idx[:n], idx[n:]
        Xtr, Xte = X[itr], X[ite]
        ytr, yte = y[itr], y[ite]
    else:
        strat = y if task == "cls" else None
        Xtr, Xte, ytr, yte, itr, ite = train_test_split(
            X, y, np.arange(len(df)), test_size=test_size, random_state=seed, stratify=strat)

    if LGBM_AVAILABLE:
        if task == "cls":
            model = lgb.LGBMClassifier(n_estimators=200, learning_rate=0.05,
                                       num_leaves=31, random_state=seed, verbose=-1)
        else:
            model = lgb.LGBMRegressor(n_estimators=200, learning_rate=0.05,
                                      num_leaves=31, random_state=seed, verbose=-1)
        model.fit(Xtr, ytr)
        try:
            imp = np.asarray(model.booster_.feature_importance(importance_type="gain"), dtype=float)
        except Exception:
            imp = np.asarray(model.feature_importances_, dtype=float)
        backend = "lightgbm"
    else:
        from sklearn.ensemble import (HistGradientBoostingClassifier,
                                      HistGradientBoostingRegressor)
        from sklearn.inspection import permutation_importance
        if task == "cls":
            model = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05,
                                                  max_leaf_nodes=31, random_state=seed)
        else:
            model = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05,
                                                 max_leaf_nodes=31, random_state=seed)
        model.fit(Xtr, ytr)
        pi = permutation_importance(model, Xte, yte, n_repeats=10, random_state=seed)
        imp = np.asarray(pi.importances_mean, dtype=float)
        backend = "sklearn(HistGradientBoosting)"

    imp = np.asarray(imp, dtype=float)
    imp_sum = imp.sum() or 1.0
    importance = [{"feature": f, "cn": FEATURE_CN.get(f, f),
                   "importance": float(v), "ratio": float(v / imp_sum)}
                  for f, v in sorted(zip(FEATURE_COLS, imp), key=lambda x: -x[1])]

    if task == "cls":
        yp = model.predict(Xte)
        try:
            proba = model.predict_proba(Xte)[:, 1]
            auc = float(roc_auc_score(yte, proba))
        except Exception:
            auc = None
        cm = confusion_matrix(yte, yp, labels=[0, 1])
        metrics = {
            "task": "cls", "backend": backend,
            "n_train": int(len(ytr)), "n_test": int(len(yte)),
            "accuracy": float(accuracy_score(yte, yp)),
            "precision": float(precision_score(yte, yp, zero_division=0)),
            "recall": float(recall_score(yte, yp, zero_division=0)),
            "f1": float(f1_score(yte, yp, zero_division=0)),
            "auc": auc,
            "confusion_matrix": {"tn": int(cm[0, 0]), "fp": int(cm[0, 1]),
                                 "fn": int(cm[1, 0]), "tp": int(cm[1, 1])},
        }
    else:
        yp = model.predict(Xte)
        metrics = {
            "task": "reg", "backend": backend,
            "n_train": int(len(ytr)), "n_test": int(len(yte)),
            "mae": float(mean_absolute_error(yte, yp)),
            "rmse": float(np.sqrt(np.mean((yte - yp) ** 2))),
            "r2": float(r2_score(yte, yp)),
        }

    return {"metrics": metrics, "importance": importance, "model": model,
            "X": X, "y": y, "feature_names": FEATURE_COLS, "rows": rows}


# ──────────────────────────────────────────────────────────────
# 3. 聚类（找市场类型）
# ──────────────────────────────────────────────────────────────
def run_cluster(rows: List[dict], k: int = 4, seed: int = 42):
    if len(rows) < k + 2:
        raise ValueError(f"样本不足（{len(rows)} 笔），聚类至少需要 {k + 2} 笔")
    df = _df_from(rows)
    Xc = df[CLUSTER_COLS].values.astype(float)
    scaler = StandardScaler()
    Xs = scaler.fit_transform(Xc)

    km = KMeans(n_clusters=k, random_state=seed, n_init=10)
    km.fit(Xs)
    labels = km.labels_
    centers = scaler.inverse_transform(km.cluster_centers_)

    gmean = Xc.mean(0); gstd = Xc.std(0)
    gstd = np.where(gstd == 0, 1.0, gstd)

    clusters = []
    for cid in range(k):
        idx = np.where(labels == cid)[0]
        sub = df.iloc[idx]
        regime = sub["regime_cn"].mode()
        regime_cn = regime.iloc[0] if len(regime) else "-"
        z = (centers[cid] - gmean) / gstd
        order = np.argsort(-np.abs(z))[:4]
        top_feats = [{"feature": CLUSTER_COLS[j],
                      "cn": FEATURE_CN.get(CLUSTER_COLS[j], CLUSTER_COLS[j]),
                      "centroid": float(centers[cid][j]), "z": float(z[j])}
                     for j in order]
        clusters.append({
            "id": int(cid), "size": int(len(idx)),
            "avg_pnl": float(sub["pnl"].mean()) if len(sub) else 0.0,
            "win_rate": float((sub["win"] > 0).mean()) if len(sub) else 0.0,
            "v3_pass_rate": float(sub["v3_pass"].mean()) if len(sub) else 0.0,
            "dominant_regime": regime_cn, "top_features": top_feats,
            "sample_indices": [int(x) for x in idx.tolist()],
        })

    silhouette = None
    try:
        if len(set(labels)) > 1 and len(rows) > k + 1:
            from sklearn.metrics import silhouette_score
            silhouette = float(silhouette_score(Xs, labels))
    except Exception:
        silhouette = None

    return {"k": int(k), "n": int(len(rows)), "silhouette": silhouette,
            "clusters": clusters, "assignments": [int(x) for x in labels.tolist()]}


# ──────────────────────────────────────────────────────────────
# 4. SHAP（解释 LightGBM）
# ──────────────────────────────────────────────────────────────
def compute_shap(model, X, feature_names, task, rows, top_n: int = 6):
    if not SHAP_AVAILABLE:
        # 降级：permutation importance（仅全局，无单样本分解）
        from sklearn.inspection import permutation_importance
        y = np.array([r["win"] if task == "cls" else r["pnl"] for r in rows], dtype=float)
        pi = permutation_importance(model, X, y, n_repeats=10, random_state=42)
        imp = pi.importances_mean
        order = np.argsort(-np.abs(imp))[:top_n]
        return {
            "backend": "sklearn-permutation(未装shap)",
            "note": "未安装 shap，仅提供全局近似特征贡献；pip install shap 后可看单样本分解",
            "base_value": None,
            "global": [{"feature": feature_names[j], "cn": FEATURE_CN.get(feature_names[j], feature_names[j]),
                        "mean_abs_shap": float(imp[j])} for j in order],
            "samples": [],
        }

    explainer = shap.TreeExplainer(model)
    sv = explainer.shap_values(X)
    base_value = explainer.expected_value
    if isinstance(sv, list):                      # 多分类
        sv_arr = np.asarray(sv[1] if len(sv) > 1 else sv[0], dtype=float)
    elif getattr(sv, "ndim", 0) == 3:             # (n, cls, f)
        sv_arr = np.asarray(sv[:, 1, :] if sv.shape[1] > 1 else sv[:, 0, :], dtype=float)
    else:
        sv_arr = np.asarray(sv, dtype=float)
    if np.ndim(base_value) > 0:
        base_value = float(np.asarray(base_value).flatten()[0])
    else:
        base_value = float(base_value) if base_value is not None else None

    mean_abs = np.abs(sv_arr).mean(0)
    gorder = np.argsort(-np.abs(mean_abs))[:top_n]
    global_imp = [{"feature": feature_names[j], "cn": FEATURE_CN.get(feature_names[j], feature_names[j]),
                  "mean_abs_shap": float(mean_abs[j])} for j in gorder]

    # 选几笔有代表性的样本：预测最高/最低 + 错判样本
    if task == "cls":
        proba = model.predict_proba(X)[:, 1]
        pred = (proba >= 0.5).astype(int)
    else:
        pred = model.predict(X)
    y = np.array([r["win"] if task == "cls" else r["pnl"] for r in rows], dtype=float)
    orders = [np.argmax(proba), np.argmin(proba)]
    wrong = np.where(pred != y)[0]
    if len(wrong):
        orders.append(int(wrong[0]))
    chosen = []
    seen = set()
    for idx in orders:
        if idx in seen or idx >= len(rows):
            continue
        seen.add(idx)
        contrib = sv_arr[idx]
        corder = np.argsort(-np.abs(contrib))[:8]
        chosen.append({
            "index": int(idx), "ts": rows[idx].get("ts"),
            "actual": (int(y[idx]) if task == "cls" else float(y[idx])),
            "predicted": (float(proba[idx]) if task == "cls" else float(pred[idx])),
            "dir": rows[idx].get("dir"), "pnl": rows[idx].get("pnl"),
            "regime_cn": rows[idx].get("regime_cn"),
            "contrib": [{"feature": feature_names[j],
                         "cn": FEATURE_CN.get(feature_names[j], feature_names[j]),
                         "value": _round(rows[idx].get(feature_names[j])),
                         "shap": float(contrib[j])} for j in corder],
        })

    return {"backend": "shap(TreeExplainer)", "base_value": base_value,
            "global": global_imp, "samples": chosen}


# ──────────────────────────────────────────────────────────────
# API 路由
# ──────────────────────────────────────────────────────────────
from fastapi import APIRouter
from pydantic import BaseModel

sl_router = APIRouter(prefix="/api/sl", tags=["strategy-learning"])


class TrainReq(BaseModel):
    symbol: str = "BTC-USDT"
    base_tf: str = "1h"
    years: int = 5                # 取最近 N 年 K 线
    task: str = "cls"             # cls | reg
    test_size: float = 0.3


class ClusterReq(BaseModel):
    symbol: str = "BTC-USDT"
    base_tf: str = "1h"
    years: int = 5
    k: int = 4


class ShapReq(BaseModel):
    symbol: str = "BTC-USDT"
    base_tf: str = "1h"
    years: int = 5
    task: str = "cls"
    top_n: int = 6


def _cache_key(symbol, base_tf, limit, task):
    return f"{symbol}|{base_tf}|{limit}|{task}"


@sl_router.get("/dataset")
async def get_dataset(symbol: str = "BTC-USDT", base_tf: str = "1h", years: int = 5):
    ds = await build_dataset(symbol, base_tf, years=years)
    if not ds:
        return {"ok": False, "error": f"无 {symbol} / {base_tf} 数据（后端未启动、无网或该周期历史不足）"}
    return {"ok": True, **ds}


@sl_router.post("/train")
async def train(req: TrainReq):
    ds = await build_dataset(req.symbol, req.base_tf, years=req.years)
    if not ds:
        return {"ok": False, "error": "无数据"}
    res = train_lightgbm(ds["rows"], task=req.task, test_size=req.test_size)
    _CACHE[_cache_key(req.symbol, req.base_tf, compute_limit(req.base_tf, req.years), req.task)] = res
    return {"ok": True, "metrics": res["metrics"], "importance": res["importance"],
            "lgbm_available": LGBM_AVAILABLE}


@sl_router.post("/cluster")
async def cluster(req: ClusterReq):
    ds = await build_dataset(req.symbol, req.base_tf, years=req.years)
    if not ds:
        return {"ok": False, "error": "无数据"}
    try:
        res = run_cluster(ds["rows"], k=req.k)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, **res}


@sl_router.post("/shap")
async def shap_endpoint(req: ShapReq):
    ds = await build_dataset(req.symbol, req.base_tf, years=req.years)
    if not ds:
        return {"ok": False, "error": "无数据"}
    key = _cache_key(req.symbol, req.base_tf, compute_limit(req.base_tf, req.years), req.task)
    cached = _CACHE.get(key)
    if cached and cached.get("model") is not None:
        res = cached
    else:
        res = train_lightgbm(ds["rows"], task=req.task)
        _CACHE[key] = res
    try:
        out = compute_shap(res["model"], res["X"], res["feature_names"],
                           req.task, res["rows"], top_n=req.top_n)
    except Exception as e:
        return {"ok": False, "error": f"SHAP 计算失败: {e}"}
    out["shap_available"] = SHAP_AVAILABLE
    return {"ok": True, **out}


def _round(v, n=4):
    try:
        return round(float(v), n)
    except Exception:
        return v
