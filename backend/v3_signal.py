# -*- coding: utf-8 -*-
"""V3 信号引擎 —— Event / Timing Layer（设计见仓库根目录 v3.md）。

V1/V2 回答的是"这个方向有没有 Alpha"，V3 在其之上新增一层回答
"现在是不是合适的入场时机"：

    SuperTrend V2 基础分（STV2）
        + Event 检测     大K线 EventATR = |Close-Open| / ATR14
        + Breakout       突破 20 根结构（不含当前K）+ Buffer 0.05ATR
        + Pullback       回踩深度 / 守位 / 质量 PQ
        + Relaunch       回踩后重新启动 = 真正的入场扳机
        + Chase Blocker  追高检测（距 EMA20 / 距 ST / EventATR / 3根涨幅 / 前方空间）
        + Historical     历史上相似事件的延续概率（只用当前K之前的样本，无前视）
        + Risk Penalty   独立扣分（chase / 波动异常 / 空间 / 结构 / 流动性）

最终分（v3.md 十三章的归一化形式）：
    Final = 0.65 * STV2 + 0.20 * Timing + 0.15 * Historical - RiskPenalty

生命周期（Signal Lifecycle）：
    WAIT → EVENT_DETECTED → BREAKOUT → PULLBACK_WAIT
         → PULLBACK_CONFIRMED → RELAUNCH → ENTRY_READY → CONFIRMED

关键行为：Chase Blocker 命中时**不直接下单**，信号降级为 CANDIDATE，
登记进 Lifecycle 等回踩；回踩合格且 Relaunch 触发时才重新判分入场。

⚠️ 依赖注入说明：生命周期状态保存在模块级单例 LIFECYCLE 里（仅内存）。
   回测/批量评估前请调用 reset() 隔离，实盘重启后候选会丢失（可接受：
   候选有效期只有 CANDIDATE_MAX_BARS 根）。
"""

from __future__ import annotations

import threading

# ────────────────────────── 阈值（默认取 v3.md 推荐值）──────────────────────────

ATR_WINDOW = 14          # ATR 周期
BREAKOUT_N = 20          # 突破结构窗口（不含当前K）
BUFFER_ATR = 0.05        # 突破/再启动缓冲 = 0.05 × ATR
EMA_N = 20               # Chase 用的 EMA 周期
VOL_SMA_N = 20           # 量比基准
RESIST_N = 60            # 前方阻力/支撑参考窗口

# 大K线等级（EventATR）
EVENT_NOTABLE = 1.5
EVENT_STRONG = 2.0
EVENT_EXTREME = 2.5

# Chase Blocker
CHASE_BLOCK = 20         # ChaseScore > 20 → 不直接 CONFIRMED，转 CANDIDATE

# Chase 命中后的处置方式：
#   "block" = 禁止入场，登记候选等回踩 → Relaunch 才判单（v3.md 原设计）
#   "half"  = 不禁止入场，允许进但降级半仓（追第一根在这些品种上并不亏，
#             只是该减仓；等回踩会错过 11/23 的趋势，见回测归因）
CHASE_MODE = "block"

# Pullback
PULLBACK_MIN = 0.2       # <0.2 ATR 过浅，不算有效回踩
PULLBACK_BEST = 1.0
PULLBACK_MAX = 1.5       # >1.5 低质量
PULLBACK_INVALID = 2.0   # >2.0 大概率失效

# 候选有效期
CANDIDATE_MAX_BARS = 20  # 超过这么多根还没 relaunch → EXPIRED

# 历史统计
HIST_MIN_SAMPLES = 30    # 样本不足就逐级放宽匹配条件
HIST_FULL_SAMPLES = 200  # SC = min(1, N/200)

# 组合权重（v3.md 十三章）
W_STV2 = 0.65
W_TIMING = 0.20
W_HIST = 0.15


# ────────────────────────── 基础指标 ──────────────────────────

def atr_series(candles: list[dict], n: int = ATR_WINDOW) -> list:
    """ta.atr系列（含前导 None）。"""
    from indicators import ta_atr
    try:
        return ta_atr([c["h"] for c in candles], [c["l"] for c in candles],
                      [c["c"] for c in candles], n)
    except Exception:
        return [None] * len(candles)


def ema_series(vals: list[float], n: int) -> list[float]:
    out, k, prev = [], 2.0 / (n + 1), None
    for v in vals:
        prev = v if prev is None else v * k + prev * (1 - k)
        out.append(prev)
    return out


def _sma(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _sat(x: float | None, anchors: list[tuple[float, float]]) -> float:
    """分段线性饱和映射（与 regime_scoring._sat 同款，避免跨模块耦合）。"""
    if x is None:
        return anchors[0][1]
    if x <= anchors[0][0]:
        return anchors[0][1]
    if x >= anchors[-1][0]:
        return anchors[-1][1]
    for (x1, y1), (x2, y2) in zip(anchors, anchors[1:]):
        if x1 <= x <= x2:
            return y1 + (y2 - y1) * (x - x1) / (x2 - x1)
    return anchors[-1][1]


# ────────────────────────── 一、Event + Breakout ──────────────────────────

def event_metrics(candles: list[dict], i: int, atr_i: float, side: str) -> dict:
    """大K线事件 + 突破结构 + 突破强度（v3.md 一~三章）。

    side: "buy" / "sell"。
    返回 event_atr / level / breakout / breakout_atr / breakout_score /
         vol_ratio / close_pos / grade
    """
    out = {"event_atr": None, "level": None, "breakout": False,
           "breakout_atr": None, "breakout_score": 0.0,
           "vol_ratio": None, "close_pos": None, "grade": "normal"}
    if i >= len(candles) or not atr_i or atr_i <= 0:
        return out
    c = candles[i]
    rng = (c["h"] - c["l"]) or 1e-12
    out["event_atr"] = abs(c["c"] - c["o"]) / atr_i
    out["close_pos"] = (c["c"] - c["l"]) / rng

    # 量比：当前量 / 前 VOL_SMA_N 根均量（不含当前K）
    base = [x["vol"] for x in candles[max(0, i - VOL_SMA_N):i] if x.get("vol")]
    if base:
        avg = _sma(base)
        out["vol_ratio"] = (c.get("vol") or 0.0) / avg if avg > 0 else None

    # 突破位：不含当前K
    lo = max(0, i - BREAKOUT_N)
    if i > lo:
        if side == "buy":
            out["level"] = max(x["h"] for x in candles[lo:i])
            out["breakout"] = c["c"] > out["level"] + BUFFER_ATR * atr_i
        else:
            out["level"] = min(x["l"] for x in candles[lo:i])
            out["breakout"] = c["c"] < out["level"] - BUFFER_ATR * atr_i
        out["breakout_atr"] = abs(c["c"] - out["level"]) / atr_i

    # 突破强度 B = 结构30 + 距离20 + 量能20 + 收盘位置30
    b_struct = 30.0 if out["breakout"] else 0.0
    if out["breakout_atr"] is None:
        b_dist = 0.0
    else:
        d = out["breakout_atr"]
        b_dist = 5.0 if d < 0.2 else (12.0 if d < 0.5 else (20.0 if d <= 1.0 else 15.0))
    vr = out["vol_ratio"]
    b_vol = 5.0 if vr is None or vr < 1.0 else (
        10.0 if vr < 1.3 else (17.0 if vr <= 2.0 else 20.0))
    cp = out["close_pos"] or 0.0
    b_close = 30.0 * (cp if side == "buy" else 1.0 - cp)
    out["breakout_score"] = round(b_struct + b_dist + b_vol + b_close, 1)

    ea = out["event_atr"] or 0.0
    out["grade"] = ("extreme" if ea >= EVENT_EXTREME else
                    "strong" if ea >= EVENT_STRONG else
                    "notable" if ea >= EVENT_NOTABLE else "normal")
    return out


# ────────────────────────── 八、Chase Blocker ──────────────────────────

def chase_score(candles: list[dict], i: int, atr_i: float, st_line: float | None,
                side: str, event_atr: float | None) -> tuple[float, dict]:
    """追高风险分（0~40+）。> CHASE_BLOCK(20) 表示"现在追进去大概率是接盘"。

    ① 距 EMA20（0~12） ② 距 SuperTrend 线（0~10） ③ 当前K异常度（0~10）
    ④ 3根涨幅 / ATR（0~8） ⑤ 前方空间不足（0~10）
    """
    d: dict = {}
    if i >= len(candles) or not atr_i or atr_i <= 0:
        return 0.0, d
    c = candles[i]
    px = c["c"]

    # ① 距 EMA20
    closes = [x["c"] for x in candles[: i + 1]]
    ema = ema_series(closes, EMA_N)[-1] if len(closes) >= EMA_N else None
    if ema:
        dema = abs(px - ema) / atr_i
        d["d_ema"] = round(dema, 2)
        c1 = 0.0 if dema < 1 else (2.0 if dema < 1.5 else (5.0 if dema < 2 else
                                                           (8.0 if dema <= 2.5 else 12.0)))
    else:
        c1 = 0.0

    # ② 距 SuperTrend 线
    if st_line:
        dst = abs(px - st_line) / atr_i
        d["d_st"] = round(dst, 2)
        c2 = min(10.0, max(0.0, dst - 0.5) * 5.0)
    else:
        c2 = 0.0

    # ③ 当前K异常度
    ea = event_atr or 0.0
    c3 = 0.0 if ea < EVENT_NOTABLE else (2.0 if ea < EVENT_STRONG else
                                         (5.0 if ea < EVENT_EXTREME else 10.0))

    # ④ 3根涨幅
    if i >= 3:
        r3 = abs(px - candles[i - 3]["c"]) / atr_i
        d["r3_atr"] = round(r3, 2)
        c4 = min(8.0, max(0.0, r3 - 1.0) * 4.0)
    else:
        c4 = 0.0

    # ⑤ 前方空间：到近端阻力/支撑 < 1ATR 说明快撞墙
    lo = max(0, i - RESIST_N)
    if i > lo + 1:
        if side == "buy":
            res = max(x["h"] for x in candles[lo:i])
            space = (res - px) / atr_i if res > px else 99.0
        else:
            sup = min(x["l"] for x in candles[lo:i])
            space = (px - sup) / atr_i if sup < px else 99.0
        d["space_atr"] = round(space, 2)
        c5 = 10.0 if space < 1.0 else (5.0 if space < 2.0 else 0.0)
    else:
        c5 = 0.0

    d.update({"c_ema": round(c1, 1), "c_st": round(c2, 1), "c_event": c3,
              "c_r3": round(c4, 1), "c_space": c5})
    total = c1 + c2 + c3 + c4 + c5
    d["total"] = round(total, 1)
    return total, d


# ────────────────────────── 四~六、Pullback ──────────────────────────

def pullback_state(candles: list[dict], i0: int, i: int, side: str,
                   level: float | None, atr_b: float) -> dict:
    """回踩状态（v3.md 四~五章）。i0 = 突破K索引，i = 当前K索引。

    返回 extreme / pb_low / pb_high / depth(ATR) / level_hold / structure_hold
    """
    out = {"bars": i - i0, "extreme": None, "pb_low": None, "pb_high": None,
           "depth": None, "level_hold": True, "structure_hold": True,
           "invalidated": False}
    # 至少要有一根回踩K（i0+1）才有深度可言；否则 depth=None（还没回踩数据）
    if i - i0 < 2 or not atr_b or atr_b <= 0:
        return out

    seg = candles[i0 + 1:i]                                # 回踩段：不含当前K
    if side == "buy":
        jl = min(range(len(seg)), key=lambda k: seg[k]["l"])   # 回踩低点所在位置
        out["pb_low"] = seg[jl]["l"]
        # 极值只统计到"回踩低点"为止：否则再启动创新高会把 depth 越算越大，
        # 明明成功的回踩会被误判成 INVALIDATED（Relaunch 永远不触发）
        out["extreme"] = max([candles[i0]["c"]] + [seg[k]["c"] for k in range(jl + 1)])
        out["pb_high"] = max(x["h"] for x in seg)
        out["depth"] = (out["extreme"] - out["pb_low"]) / atr_b
        if level is not None:
            out["level_hold"] = out["pb_low"] >= level
        out["structure_hold"] = out["pb_low"] > candles[i0]["l"]
    else:
        jh = max(range(len(seg)), key=lambda k: seg[k]["h"])   # 回抽高点所在位置
        out["pb_high"] = seg[jh]["h"]
        out["extreme"] = min([candles[i0]["c"]] + [seg[k]["c"] for k in range(jh + 1)])
        out["pb_low"] = min(x["l"] for x in seg)
        out["depth"] = (out["pb_high"] - out["extreme"]) / atr_b
        if level is not None:
            out["level_hold"] = out["pb_high"] <= level
        out["structure_hold"] = out["pb_high"] < candles[i0]["h"]

    d = out["depth"] or 0.0
    out["invalidated"] = (d > PULLBACK_INVALID) or (not out["level_hold"] and d > PULLBACK_MAX)
    return out


def pullback_quality(ps: dict, st_dir_ok: bool, vol_ratio_pb: float | None,
                     vol_ratio_break: float | None, atr_pb: float | None,
                     atr_break: float | None, side: str,
                     candles: list[dict], i: int) -> tuple[float, dict]:
    """回踩质量 PQ ∈ [0,1]（v3.md 六章）：
    PQ = 0.30L + 0.25S + 0.15ST + 0.15V + 0.15Vol
    """
    L = 1.0 if ps.get("level_hold") else 0.0
    S = 1.0 if ps.get("structure_hold") else 0.0
    ST = 1.0 if st_dir_ok else 0.0
    if vol_ratio_pb is not None and vol_ratio_break:
        V = 1.0 - min(vol_ratio_pb / vol_ratio_break, 1.0)
    else:
        V = 0.5
    if atr_pb and atr_break:
        Vol = 1.0 - min(atr_pb / atr_break, 1.0)
    else:
        Vol = 0.5
    # 深度在最佳区(0.5~1.0)额外标注，不额外改权重（保持 v3.md 公式）
    pq = 0.30 * L + 0.25 * S + 0.15 * ST + 0.15 * V + 0.15 * Vol
    return round(pq, 3), {"L": L, "S": S, "ST": ST,
                          "V": round(V, 2), "Vol": round(Vol, 2),
                          "depth": ps.get("depth")}


# ────────────────────────── 七、Relaunch ──────────────────────────

def relaunch_check(ps: dict, candles: list[dict], i: int, side: str,
                   atr_i: float) -> bool:
    """再启动扳机：Close > PullbackHigh + 0.05ATR（空头镜像）。"""
    d = ps.get("depth")
    if d is None or d < PULLBACK_MIN:
        return False
    if not atr_i or i >= len(candles):
        return False
    px = candles[i]["c"]
    if side == "buy":
        return ps.get("pb_high") is not None and px > ps["pb_high"] + BUFFER_ATR * atr_i
    return ps.get("pb_low") is not None and px < ps["pb_low"] - BUFFER_ATR * atr_i


def relaunch_score(breakout_score: float, vol_ratio: float | None,
                   event_atr: float | None, st_dir_ok: bool) -> float:
    """RS = 0.35Break + 0.25Volume + 0.20Candle + 0.20ST（0~100）。"""
    brk = max(0.0, min(100.0, breakout_score))
    vol = _sat(vol_ratio, [(0.8, 20.0), (1.2, 50.0), (2.0, 85.0), (3.0, 100.0)])
    candle = _sat(event_atr, [(0.2, 10.0), (0.8, 45.0), (1.5, 80.0), (2.5, 100.0)])
    st = 100.0 if st_dir_ok else 0.0
    return round(0.35 * brk + 0.25 * vol + 0.20 * candle + 0.20 * st, 1)


# ────────────────────────── 九~十、历史相似事件统计 ──────────────────────────

class EventHistory:
    """历史大K线事件库（只用当前K之前的样本，回测无前视）。

    事件定义：EventATR ≥ 1.5 的K线，记录 vol_ratio / ST方向 / 是否突破，
    以及之后 1/3/5/10 根的方向化收益与 MFE/MAE。
    """

    def __init__(self, events: list[dict]):
        self.events = events

    @classmethod
    def build(cls, candles: list[dict], p: dict | None = None) -> "EventHistory":
        p = p or {}
        n = len(candles)
        if n < BREAKOUT_N + ATR_WINDOW + 15:
            return cls([])
        atr = atr_series(candles, ATR_WINDOW)
        trend: list = [None] * n
        try:
            from indicators import super_trend
            st = super_trend([c["o"] for c in candles], [c["h"] for c in candles],
                             [c["l"] for c in candles], [c["c"] for c in candles],
                             periods=p.get("periods", 15),
                             multiplier=p.get("multiplier", 9.1),
                             src=p.get("src", "hl2"),
                             change_atr=p.get("change_atr", True))
            trend = st.get("trend") or trend
        except Exception:
            trend = [None] * n
        vols = [c.get("vol") or 0.0 for c in candles]

        events: list[dict] = []
        for j in range(BREAKOUT_N + ATR_WINDOW, n - 10):
            a = atr[j]
            if not a or a <= 0:
                continue
            ea = abs(candles[j]["c"] - candles[j]["o"]) / a
            if ea < EVENT_NOTABLE:
                continue
            d = trend[j] or 1
            lo = j - BREAKOUT_N
            if d == 1:
                lvl = max(x["h"] for x in candles[lo:j])
                broke = candles[j]["c"] > lvl + BUFFER_ATR * a
            else:
                lvl = min(x["l"] for x in candles[lo:j])
                broke = candles[j]["c"] < lvl - BUFFER_ATR * a
            base = vols[max(0, j - VOL_SMA_N):j]
            vr = (vols[j] / _sma(base)) if base and _sma(base) > 0 else None
            px0 = candles[j]["c"]
            rec = {"idx": j, "dir": d, "ea": ea, "vr": vr, "broke": broke}
            for k in (1, 3, 5, 10):
                seg_h = [x["h"] for x in candles[j + 1: j + 1 + k]]
                seg_l = [x["l"] for x in candles[j + 1: j + 1 + k]]
                if len(seg_h) < k:
                    continue
                ret = (candles[j + k]["c"] / px0 - 1.0) * d
                if d == 1:
                    mfe = max(seg_h) / px0 - 1.0
                    mae = min(seg_l) / px0 - 1.0
                else:
                    mfe = 1.0 - min(seg_l) / px0
                    mae = 1.0 - max(seg_h) / px0
                rec[f"ret{k}"] = ret
                if k == 3:
                    rec["mfe3"], rec["mae3"] = mfe, mae
            events.append(rec)
        return cls(events)

    def query(self, i: int, side: str, event_atr: float | None,
              vol_ratio: float | None, broke: bool) -> dict:
        """查相似事件：先严后宽（event档 → vol档 → breakout → 方向）。"""
        d = 1 if side == "buy" else -1
        ea = event_atr or 0.0
        ea_band = (2 if ea >= EVENT_EXTREME else
                   1 if ea >= EVENT_STRONG else 0)
        vr = vol_ratio
        vr_band = (None if vr is None else
                   (0 if vr < 1.0 else (1 if vr < 1.5 else (2 if vr < 3.0 else 3))))

        for relax in range(4):
            sel = []
            for e in self.events:
                if e["idx"] >= i:                       # 只用历史，杜绝前视
                    continue
                if e["dir"] != d:
                    continue
                if relax < 3:
                    eb = (2 if e["ea"] >= EVENT_EXTREME else
                          1 if e["ea"] >= EVENT_STRONG else 0)
                    if eb != ea_band:
                        continue
                if relax < 2 and vr_band is not None:
                    evb = (None if e["vr"] is None else
                           (0 if e["vr"] < 1.0 else (1 if e["vr"] < 1.5 else
                                                     (2 if e["vr"] < 3.0 else 3))))
                    if evb is not None and evb != vr_band:
                        continue
                if relax < 1 and bool(e["broke"]) != bool(broke):
                    continue
                sel.append(e)
            if len(sel) >= HIST_MIN_SAMPLES or relax == 3:
                break

        n = len(sel)
        out = {"n": n, "p1": None, "p3": None, "p5": None, "p10": None,
               "mfe3": None, "mae3": None, "rev": None, "hcs": 50.0, "sc": 0.0,
               "relax": relax}
        if not n:
            return out
        for k in (1, 3, 5, 10):
            vals = [e[f"ret{k}"] for e in sel if e.get(f"ret{k}") is not None]
            if vals:
                out[f"p{k}"] = round(sum(1 for v in vals if v > 0) / len(vals), 3)
        mfes = [e["mfe3"] for e in sel if e.get("mfe3") is not None]
        maes = [e["mae3"] for e in sel if e.get("mae3") is not None]
        if mfes:
            out["mfe3"] = round(sum(mfes) / len(mfes), 4)
        if maes:
            out["mae3"] = round(sum(maes) / len(maes), 4)
        r1 = [e["ret1"] for e in sel if e.get("ret1") is not None]
        if r1:
            out["rev"] = round(sum(1 for v in r1 if v < 0) / len(r1), 3)

        p3 = out["p3"]
        base = 100.0 * p3 if p3 is not None else 50.0
        sc = min(1.0, n / HIST_FULL_SAMPLES)
        # 样本越少越向中性 50 收缩，防止"10个样本100%胜率"的假优势
        out["sc"] = round(sc, 2)
        out["hcs"] = round(50.0 + (base - 50.0) * sc, 1)
        return out


# ────────────────────────── 十二、Risk Adjustment ──────────────────────────

def risk_penalty(chase: float, atr_ratio: float | None, ps: dict | None,
                 vol_ratio: float | None, space_atr: float | None) -> tuple[float, dict]:
    """RP = P_chase + P_volatility + P_space + P_structure + P_liquidity（0~55）。"""
    d: dict = {}
    # Chase：chase 分越高扣越多（封顶 15）
    p_chase = min(15.0, max(0.0, chase) * 0.6)
    # 波动异常：ATR 比 >1.8 视为过热
    if atr_ratio is None:
        p_vol = 0.0
    elif atr_ratio >= 2.0:
        p_vol = 10.0
    elif atr_ratio >= 1.8:
        p_vol = 6.0
    elif atr_ratio >= 1.5:
        p_vol = 3.0
    else:
        p_vol = 0.0
    # 空间：前方 <1ATR 扣 10（与 Chase⑤ 同源但看更远端，这里只补差额）
    if space_atr is None:
        p_space = 0.0
    elif space_atr < 1.0:
        p_space = 6.0
    elif space_atr < 2.0:
        p_space = 3.0
    else:
        p_space = 0.0
    # 结构：回踩过深/破位
    if not ps or ps.get("depth") is None:
        p_struct = 0.0
    else:
        dep = ps["depth"]
        p_struct = (10.0 if dep > PULLBACK_INVALID else
                    6.0 if dep > PULLBACK_MAX else
                    2.0 if dep > PULLBACK_BEST else 0.0)
        if not ps.get("level_hold"):
            p_struct = min(10.0, p_struct + 4.0)
    # 流动性：爆量或极度缩量
    if vol_ratio is None:
        p_liq = 0.0
    elif vol_ratio >= 4.0 or vol_ratio < 0.5:
        p_liq = 10.0
    elif vol_ratio >= 3.0 or vol_ratio < 0.7:
        p_liq = 5.0
    else:
        p_liq = 0.0
    d.update({"chase": round(p_chase, 1), "volatility": p_vol, "space": p_space,
              "structure": round(p_struct, 1), "liquidity": p_liq})
    total = p_chase + p_vol + p_space + p_struct + p_liq
    d["total"] = round(total, 1)
    return total, d


# ────────────────────────── 十一、Signal Lifecycle ──────────────────────────

STAGE_CANDIDATE = "CANDIDATE"              # Chase 命中，等回踩（不下单）
STAGE_PULLBACK_WAIT = "PULLBACK_WAIT"      # 回踩过浅/未成形
STAGE_PULLBACK_CONFIRMED = "PULLBACK_CONFIRMED"
STAGE_RELAUNCH = "RELAUNCH"                # 再启动触发 → 可入场
STAGE_EXPIRED = "EXPIRED"
STAGE_INVALIDATED = "INVALIDATED"


class _Candidate(dict):
    """用 dict 存候选（方便序列化/日志），字段见 Lifecycle.register。"""


class Lifecycle:
    """V3 候选信号的生命周期状态机（线程安全，惰性重算）。

    惰性：不依赖"每根K都被调用"，每次 advance 都用当前 candles 从 i0 重算，
    实盘漏调用一根最多延迟一根触发。
    """

    def __init__(self):
        self._c: dict[str, _Candidate] = {}
        self._lock = threading.RLock()

    def reset(self):
        with self._lock:
            self._c.clear()

    def register(self, key: str, **kw) -> _Candidate:
        with self._lock:
            cur = self._c.get(key)
            # 幂等：同一事件（同一起点）被重复评估（UI 重扫 / shadow 双跑）时
            # 只刷新元数据，绝不重置 stage / relaunch_done，否则会重复触发入场。
            if cur and kw.get("i0") is not None and cur.get("i0") == kw["i0"]:
                for k, v in kw.items():
                    cur[k] = v
                return cur
            cand = _Candidate(key=key, stage=STAGE_CANDIDATE,
                              relaunch_done=False, **kw)
            self._c[key] = cand
            return cand

    def get(self, key: str) -> _Candidate | None:
        with self._lock:
            return self._c.get(key)

    def drop(self, key: str):
        with self._lock:
            self._c.pop(key, None)

    def pending(self, symbol: str | None = None, tf: str | None = None) -> list:
        with self._lock:
            out = []
            for c in self._c.values():
                if c.get("stage") in (STAGE_RELAUNCH, STAGE_EXPIRED, STAGE_INVALIDATED):
                    continue
                if symbol and c.get("symbol") != symbol:
                    continue
                if tf and c.get("tf") != tf:
                    continue
                out.append(c)
            return out

    def advance(self, key: str, candles: list[dict], atr: list | None = None,
                st_dir_ok: bool = True) -> dict:
        """推进候选状态，返回 {stage, trigger, ps, relaunch, atr_i}。

        trigger=True 表示"本根K应该入场"（同一候选只触发一次）。
        """
        cand = self.get(key)
        if not cand:
            return {"stage": None, "trigger": False}
        i = len(candles) - 1
        i0 = cand["i0"]
        if i <= i0:
            return {"stage": cand["stage"], "trigger": False, "ps": None,
                    "atr_i": None}
        atr = atr if atr is not None else atr_series(candles, ATR_WINDOW)
        atr_i = atr[i] if i < len(atr) else None

        bars = i - i0
        if bars > CANDIDATE_MAX_BARS:
            cand["stage"] = STAGE_EXPIRED
            return {"stage": STAGE_EXPIRED, "trigger": False, "atr_i": atr_i}

        ps = pullback_state(candles, i0, i, cand["side"], cand.get("level"),
                            cand.get("atr_b") or atr_i or 0.0)
        if ps.get("invalidated"):
            cand["stage"] = STAGE_INVALIDATED
            return {"stage": STAGE_INVALIDATED, "trigger": False, "ps": ps,
                    "atr_i": atr_i}

        fired = relaunch_check(ps, candles, i, cand["side"], atr_i or 0.0)
        if fired:
            dep = ps.get("depth") or 0.0
            stage = STAGE_RELAUNCH
            cand["stage"] = stage
            if not cand.get("relaunch_done"):
                cand["relaunch_done"] = True
                cand["relaunch_ts"] = candles[i]["ts"]
                cand["relaunch_i"] = i
                return {"stage": stage, "trigger": True, "ps": ps, "atr_i": atr_i}
            return {"stage": stage, "trigger": False, "ps": ps, "atr_i": atr_i}

        dep = ps.get("depth") or 0.0
        stage = (STAGE_PULLBACK_CONFIRMED
                 if (PULLBACK_MIN <= dep <= PULLBACK_MAX and ps.get("level_hold"))
                 else STAGE_PULLBACK_WAIT)
        cand["stage"] = stage
        return {"stage": stage, "trigger": False, "ps": ps, "atr_i": atr_i}


LIFECYCLE = Lifecycle()   # 模块级单例（回测/批量评估前记得 reset()）
