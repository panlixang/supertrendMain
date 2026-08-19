"""
行情状态判定 + 下单闸门

上一轮实测（51 个窗口，BTC/ETH/SOL，1d+4h）：
    震荡 ER<0.15   → 信号亏损率 80%
    偏震 0.15~0.3  → 50%
    趋势 ER≥0.3    → 39%
所以「震荡时只提醒不下单」的闸门用效率比 ER 做主判据。

ER（Efficiency Ratio，Kaufman）= |收盘净位移| / 逐根路径长度总和
    单边直上直下 → 接近 1
    来回磨 → 接近 0
它不需要额外参数，且对「同样涨了 100 点，是一路涨上去还是折腾上去」有区分度，
正是 SuperTrend 最怕的那种区别。

闸门是「全部通过才下单」，任何一条不满足都降级为仅提醒：
    1. ER ≥ er_min                 不是震荡市
    2. 信号等级 ∈ allow_grades      默认只做 A/B，C 是逆 MTF Bias 的
    3. 强度 score ≥ min_score       翻转本身要干脆
    4. tf ∈ allow_tfs               小周期噪声大，默认只做 15m 以上

ER < er_hide_below 默认彻底静默（不弹窗、图表不画、也不下单）。例外：
交易周期上强度 ≥ min_score 的翻转视为「突破启动」——大趋势常从磨盘爆出，
60 根 ER / ATR / ADX 此时仍滞后。图上出箭头和自动下单用同一套判定，不拆开。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ER 的计算窗口（根数）。太短会被单根噪声主导，太长跟不上状态切换。
ER_WINDOW = 60


def efficiency_ratio(candles: list[dict], window: int = ER_WINDOW) -> float | None:
    """Kaufman 效率比，0~1。数据不足返回 None。"""
    if not candles or len(candles) < window + 1:
        return None
    seg = [c["c"] for c in candles[-window:]]
    net = abs(seg[-1] - seg[0])
    path = sum(abs(seg[i] - seg[i - 1]) for i in range(1, len(seg)))
    if path <= 0:
        return 0.0
    return round(net / path, 4)


def classify(er: float | None, er_min: float = 0.15, er_trend: float = 0.30,
             er_weak_min: float | None = None, quick_enabled: bool = False) -> dict:
    """把 ER 翻成人看的状态，并给出该用哪套出场规则。

    profile 决定持仓走哪档止盈止损：
        normal  标准档   吃波段（1.5% 平 70% → 抬保本 → 跟随超趋线）
        quick   快进快出 震荡边缘用（0.8% 全平 / 1% 固定止损 / 不跟随）

    ⚠️ regime="weak"（弱趋势，ER 在 er_min~er_trend）和 profile="quick"（弱档规则）
       是两回事，别混。震荡边缘那档的 regime key 是 "edge"。

    er_weak_min=None 时退化为原来的两档行为（低于 er_min 一律不下单）。
    """
    if er is None:
        return {"er": None, "regime": "unknown", "label": "数据不足",
                "tradable": False, "profile": None}
    if er >= er_trend:
        return {"er": er, "regime": "trend", "label": "趋势行情",
                "tradable": True, "profile": "normal"}
    if er >= er_min:
        return {"er": er, "regime": "weak", "label": "弱趋势",
                "tradable": True, "profile": "normal"}
    if er_weak_min is not None and er >= er_weak_min:
        # 这一档下不下单由 quick_enabled 决定，但 profile 照样报出来，
        # 好让面板显示「现在是弱档，只是你没开」
        return {"er": er, "regime": "edge", "label": "震荡边缘",
                "tradable": bool(quick_enabled), "profile": "quick"}
    return {"er": er, "regime": "range", "label": "震荡行情",
            "tradable": False, "profile": None}


@dataclass
class TradeConfig:
    """自动挂单配置，前端可改，运行时生效。"""
    enabled:      bool  = False        # 总开关，默认关 —— 必须显式打开才会下单
    paper:        bool  = True         # 模拟盘（对应 OKX x-simulated-trading:1）
    category:     str   = "SWAP"       # SWAP 永续合约。改 "SPOT" 则只能做多
    leverage:     int   = 3            # 杠杆倍数
    margin_mode:  str   = "cross"      # cross 全仓 / isolated 逐仓
    amount_usdt:  float = 10.0         # 每笔保证金，名义价值 = 它 × 杠杆
    price_offset: float = 0.05         # 开仓追价 %：买单略高于现价、卖单略低于现价，配合 IOC 立刻成交
    # ── 闸门 ──
    er_hide_below: float = 0.10        # ER < 此值时默认静默；交易周期+强度够的翻转视为突破启动（显示并下单）
    er_weak_min:   float = 0.12        # 弱档下界：ER ∈ [er_weak_min, er_min) 走「快进快出」规则
    er_min:        float = 0.15        # 标准档下界：ER ≥ 此值走「吃波段」那套规则
    er_trend:      float = 0.30        # 趋势判定：ER ≥ 此值升级为趋势行情（较少触发快进快出）
    quick_enabled: bool  = False       # 是否启用弱档快进快出下单
    allow_grades:  list  = field(default_factory=lambda: ["A", "B"])
    min_score:     int   = 2            # 0~3
    allow_tfs:     list  = field(default_factory=lambda: ["15m", "30m", "1h", "4h", "1d"])
    # 同一周期两次下单的最小间隔（秒），防止参数被调小后连续触发
    cooldown_sec:  int   = 300
    # ── 组合过滤器 ──
    atr_filter_enabled:    bool  = False   # 是否启用ATR波动率过滤
    atr_vol_min:           float = 0.7     # ATR波动率最小值
    range_filter_enabled:  bool  = False   # 是否启用区间震荡过滤
    range_size_max:        float = 0.15    # 区间大小上限
    range_touches_min:     int   = 3       # 触边次数下限
    mtf_filter_enabled:    bool  = False   # 是否启用MTF一致性过滤
    mtf_consistency_min:   float = 0.6     # MTF一致性最小值
    mtf_flip_max:          int   = 5       # 大周期翻转次数上限
    adx_filter_enabled:    bool  = False   # 是否启用ADX过滤
    adx_min:               float = 20.0    # ADX最小值（低于此值视为无趋势）
    adx_period:            int   = 14      # ADX计算周期


def evaluate(sig: dict, candles: list[dict], cfg: TradeConfig,
             candles_by_tf: dict = None, p: dict = None) -> dict:
    """决定这条信号该不该真下单。

    返回 {"trade": bool, "regime": {...}, "reasons": [...], "hidden": bool,
            "filters": {...}}，
    reasons 是所有未通过项，会原样显示在 UI 和推送里 —— 用户要看得到为什么没下单。
    hidden=True 表示 ER 过低且不够干脆，信号静默（不弹窗、不提醒、图表不画、不下单）。
    交易周期上强度够的翻转视为突破启动：显示和下单同步，ER/ATR/ADX 滞后不拦。
    filters 包含所有过滤器的详细检测结果，供调试和展示。
    """
    er = efficiency_ratio(candles)
    regime = classify(er, cfg.er_min, cfg.er_trend, cfg.er_weak_min, cfg.quick_enabled)

    # ER 太低默认静默。交易周期上足够干脆的翻转 = 突破启动：
    # 图上出箭头，自动下单也走同一条路径（ER/ATR/ADX 窗口此时还没跟上）。
    low_er = er is not None and er < cfg.er_hide_below
    quality_flip = (sig.get("score") or 0) >= cfg.min_score
    on_trade_tf = sig.get("tf") in cfg.allow_tfs
    breakout_start = bool(low_er and quality_flip and on_trade_tf)
    hidden = bool(low_er and not breakout_start)
    if hidden:
        return {"trade": False, "regime": regime, "reasons": [], "hidden": True,
                "filters": {"er": er}}

    reasons = []
    filters = {"er": er, "breakout_start": breakout_start}

    # 1. ER 基础检查（突破启动时 ER 滞后，不拦）
    if not cfg.enabled:
        reasons.append("自动挂单未开启")
    if not regime["tradable"] and not breakout_start:
        if er is None:
            reasons.append("K线不足，无法判定行情状态")
        elif regime["regime"] == "edge":
            reasons.append(
                f"震荡边缘（ER {er}，在 {cfg.er_weak_min}~{cfg.er_min} 弱档区间）"
                f"—— 弱档自动下单未勾选"
            )
        else:
            reasons.append(
                f"{regime['label']}（ER {er} < {cfg.er_weak_min}）—— 实测震荡市信号亏损率约 80%"
            )

    # 2. ATR 波动率过滤
    atr_vol = None
    if cfg.atr_filter_enabled:
        atr_vol = atr_volatility(candles)
        filters["atr_vol"] = atr_vol
        if (not breakout_start and atr_vol is not None
                and atr_vol < cfg.atr_vol_min):
            reasons.append(f"ATR萎缩（{atr_vol:.2f} < {cfg.atr_vol_min}），疑似震荡")

    # 3. 区间震荡过滤
    range_check = None
    if cfg.range_filter_enabled:
        range_check = range_bound(candles)
        filters["range_check"] = range_check
        # 区间小且触边次数多 → 震荡
        if (not breakout_start
                and range_check["range_size_pct"] < cfg.range_size_max * 100
                and range_check["touches"] >= cfg.range_touches_min):
            reasons.append(
                f"区间震荡（范围{range_check['range_size_pct']:.1f}%，"
                f"{range_check['touches']}次触边）"
            )

    # 4. MTF 一致性过滤
    mtf = None
    if cfg.mtf_filter_enabled and candles_by_tf and p:
        from strategy import mtf_st_consistency
        mtf = mtf_st_consistency(candles_by_tf, p, sig.get("tf"))
        filters["mtf"] = mtf
        # 一致性不足或大周期频繁翻转
        if mtf["consistency"] < cfg.mtf_consistency_min:
            reasons.append(f"MTF方向分歧（一致性{mtf['consistency']:.0%}）")
        if mtf["big_tf_flips"] > cfg.mtf_flip_max:
            reasons.append(f"大周期震荡（4h+1d翻转{mtf['big_tf_flips']}次）")

    # 5. ADX 过滤
    if cfg.adx_filter_enabled:
        adx_val = adx_latest(candles, cfg.adx_period)
        filters["adx"] = adx_val
        if (not breakout_start and adx_val is not None
                and adx_val < cfg.adx_min):
            reasons.append(f"ADX过低（{adx_val:.1f} < {cfg.adx_min}），无趋势")

    # 6. 原有 grade/score/tf 检查
    if sig.get("grade") not in cfg.allow_grades:
        reasons.append(f"信号等级 {sig.get('grade')} 不在允许范围 {'/'.join(cfg.allow_grades)}")
    if (sig.get("score") or 0) < cfg.min_score:
        reasons.append(f"强度 {sig.get('score')}/3 低于阈值 {cfg.min_score}/3")
    if sig.get("tf") not in cfg.allow_tfs:
        reasons.append(f"周期 {sig.get('tf')} 不在允许范围")

    # 突破启动按标准档吃波段；被拦的信号不带档位，避免执行器和闸门矛盾
    profile = "normal" if breakout_start else regime.get("profile")
    return {
        "trade": not reasons,
        "regime": regime,
        "reasons": reasons,
        "hidden": False,
        "profile": profile if not reasons else None,
        "filters": filters
    }


def adx_latest(candles: list[dict], period: int = 14) -> float | None:
    """取最新一根的 ADX 值，数据不足返回 None。"""
    if len(candles) < period * 2 + 1:
        return None
    from indicators import ta_adx
    highs  = [c["h"] for c in candles]
    lows   = [c["l"] for c in candles]
    closes = [c["c"] for c in candles]
    series = ta_adx(highs, lows, closes, period)
    val = series[-1]
    return round(val, 2) if val is not None else None


def atr_volatility(candles: list[dict], atr_window: int = 14, lookback: int = 20) -> float | None:
    """ATR波动程度：当前ATR vs 近期ATR均值的比值

    返回值 < 1.0 表示波动萎缩，> 1.0 表示波动扩张
    震荡市特征：ATR持续萎缩（< 0.8）
    """
    if len(candles) < atr_window + lookback:
        return None

    from indicators import ta_atr
    highs = [c["h"] for c in candles]
    lows = [c["l"] for c in candles]
    closes = [c["c"] for c in candles]

    atr_series = ta_atr(highs, lows, closes, atr_window)
    recent = [a for a in atr_series[-lookback:] if a is not None]
    if not recent or atr_series[-1] is None:
        return None

    avg = sum(recent) / len(recent)
    return round(atr_series[-1] / avg, 3) if avg > 0 else 1.0


def range_bound(candles: list[dict], window: int = 30) -> dict:
    """检测是否在区间震荡

    返回：
        in_range: bool - 是否满足震荡特征（由外部阈值判断）
        range_pct: float - 当前价格在区间中的位置比例
        touches: int - 近期触碰区间边界的次数
        range_size_pct: float - 区间大小（高低点差/低点 * 100）
    """
    if len(candles) < window:
        return {"in_range": False, "range_pct": 0, "touches": 0, "range_size_pct": 0}

    recent = candles[-window:]
    high = max(c["h"] for c in recent)
    low = min(c["l"] for c in recent)
    range_size = (high - low) / low if low > 0 else 0

    # 统计触碰上下轨的次数（最近10根）
    touches = 0
    touch_threshold = 0.02  # 2%以内算触碰
    for c in recent[-10:]:
        if abs(c["h"] - high) / high < touch_threshold:
            touches += 1
        if abs(c["l"] - low) / low < touch_threshold:
            touches += 1

    curr_price = candles[-1]["c"]
    range_pct = (curr_price - low) / (high - low) if high > low else 0.5

    return {
        "range_pct": round(range_pct, 2),
        "touches": touches,
        "range_size_pct": round(range_size * 100, 2)
    }


def limit_price(sig: dict, cfg: TradeConfig, last: float | None = None) -> float:
    """开仓限价：买单略高于现价、卖单略低于现价，让单立刻可成交。

    以前往不利方向挂（买单挂低/卖单挂高）是为了吃 maker。SuperTrend 翻转时价格
    已经朝信号方向走，那种挂法经常不成交；等回撤再成交等于买在反弹、卖在反抽，
    随后极易止损。不成交由 IOC / 超时撤单兜底，不要把未成交单记成持仓。
    """
    p = float(last or sig["price"])
    off = p * cfg.price_offset / 100
    return p + off if sig["type"] == "buy" else p - off
