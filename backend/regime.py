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
    price_offset: float = 0.05         # 限价单偏移 %：买单挂在触发价下方，卖单上方
    # ── 闸门 ──
    er_hide_below: float = 0.10        # ER < 此值时信号静默（仍存db但不弹窗、不提醒、面板灰显）
    er_weak_min:   float = 0.12        # 弱档下界：ER ∈ [er_weak_min, er_min) 走「快进快出」规则
    er_min:        float = 0.15        # 标准档下界：ER ≥ 此值走「吃波段」那套规则
    er_trend:      float = 0.30        # 趋势判定：ER ≥ 此值升级为趋势行情（较少触发快进快出）
    quick_enabled: bool  = False       # 是否启用弱档快进快出下单
    allow_grades:  list  = field(default_factory=lambda: ["A", "B"])
    min_score:     int   = 2            # 0~3
    allow_tfs:     list  = field(default_factory=lambda: ["15m", "30m", "1h", "4h", "1d"])
    # 同一周期两次下单的最小间隔（秒），防止参数被调小后连续触发
    cooldown_sec:  int   = 300


def evaluate(sig: dict, candles: list[dict], cfg: TradeConfig) -> dict:
    """决定这条信号该不该真下单。

    返回 {"trade": bool, "regime": {...}, "reasons": [...], "hidden": bool}，
    reasons 是所有未通过项，会原样显示在 UI 和推送里 —— 用户要看得到为什么没下单。
    hidden=True 表示 ER 过低，信号静默（不弹窗、不提醒、前端灰显）。
    """
    er = efficiency_ratio(candles)
    regime = classify(er, cfg.er_min, cfg.er_trend, cfg.er_weak_min, cfg.quick_enabled)

    # ER 太低的信号彻底静默（不弹窗、不推送、前端灰显）
    hidden = er is not None and er < cfg.er_hide_below
    if hidden:
        return {"trade": False, "regime": regime, "reasons": [], "hidden": True}

    reasons = []
    if not cfg.enabled:
        reasons.append("自动挂单未开启")
    if not regime["tradable"]:
        if er is None:
            reasons.append("K线不足，无法判定行情状态")
        elif regime["regime"] == "edge":
            # 够得着弱档、只是开关没开 —— 说清楚是哪种「没下单」，
            # 否则用户会以为是 ER 不够而去调阈值
            reasons.append(
                f"震荡边缘（ER {er}，在 {cfg.er_weak_min}~{cfg.er_min} 弱档区间）"
                f"—— 弱档自动下单未勾选"
            )
        else:
            reasons.append(
                f"{regime['label']}（ER {er} < {cfg.er_weak_min}）—— 实测震荡市信号亏损率约 80%"
            )
    if sig.get("grade") not in cfg.allow_grades:
        reasons.append(f"信号等级 {sig.get('grade')} 不在允许范围 {'/'.join(cfg.allow_grades)}")
    if (sig.get("score") or 0) < cfg.min_score:
        reasons.append(f"强度 {sig.get('score')}/3 低于阈值 {cfg.min_score}/3")
    if sig.get("tf") not in cfg.allow_tfs:
        reasons.append(f"周期 {sig.get('tf')} 不在允许范围")

    # profile 只在真要下单时才有意义 —— 被拦的信号不该带着档位往下传，
    # 免得 executor 拿到一个「该用弱档规则但其实不下单」的矛盾状态
    return {"trade": not reasons, "regime": regime, "reasons": reasons,
            "profile": regime.get("profile") if not reasons else None}


def limit_price(sig: dict, cfg: TradeConfig) -> float:
    """限价挂单价：买单挂低一点、卖单挂高一点，争取 maker 成交。

    偏移方向要对：买单往下挂才可能被动成交，往上挂就直接吃单了。
    """
    p = float(sig["price"])
    off = p * cfg.price_offset / 100
    return p - off if sig["type"] == "buy" else p + off
