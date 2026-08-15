"""
MTF Bias 表 + 信号分级

Bias 完全按 Pine 的 f_bias 实现：每个周期取 fast MA vs slow MA，
    fast >= slow → 1 (Bull)，否则 -1 (Bear)
行序与原脚本一致：5m / 15m / 30m / 1H / 4H / 1D / 1W / 1M。

在原脚本之上补了两件盯盘时真正要用的东西（Pine 表里只有颜色）：
  - 加权 bias 汇总：高周期权重更大，给出一个 [-1, 1] 的整体偏向；
  - 信号分级：ST 翻转方向与 Bias 表整体偏向是否一致 → A / B / C。
    Pine 的 strategy.entry 是无条件进场，实盘用 Bias 过滤能砍掉大部分逆势单。
"""

from indicators import bias_detail, super_trend, st_state
from state import BIAS_TFS

# 高周期定方向，权重更大
TF_WEIGHT = {"5m": 1, "15m": 1, "30m": 2, "1h": 3, "4h": 4, "1d": 5, "1w": 6, "1M": 6}


def mtf_bias(candles_by_tf: dict[str, list[dict]], p: dict) -> dict:
    """返回 Bias 表的 8 行 + 加权汇总。"""
    fast_len = p.get("fast_len", 20)
    slow_len = p.get("slow_len", 50)
    ma_type  = p.get("ma_type", "EMA")

    rows, num, den = [], 0.0, 0.0
    for tf in BIAS_TFS:
        d = bias_detail(candles_by_tf.get(tf) or [], fast_len, slow_len, ma_type)
        b = d.get("bias")
        if b is not None:
            w = TF_WEIGHT.get(tf, 1)
            num += b * w
            den += w
        rows.append({"tf": tf, **d})

    score = round(num / den, 3) if den else 0.0
    if score >= 0.5:
        verdict, label = "bull", "多头共振"
    elif score <= -0.5:
        verdict, label = "bear", "空头共振"
    else:
        verdict, label = "mixed", "多空分歧"

    return {
        "rows":    rows,
        "score":   score,
        "verdict": verdict,
        "label":   label,
        "bulls":   sum(1 for r in rows if r.get("bias") == 1),
        "bears":   sum(1 for r in rows if r.get("bias") == -1),
        "fast_len": fast_len, "slow_len": slow_len, "ma_type": ma_type,
    }


def st_table(candles_by_tf: dict[str, list[dict]], p: dict) -> list[dict]:
    """各周期的 SuperTrend 方向表（Pine 表只有 MA Bias，这里补上 ST 本身）。"""
    out = []
    for tf, candles in candles_by_tf.items():
        if not candles:
            out.append({"tf": tf, "trend": None})
            continue
        st = super_trend(
            [c["o"] for c in candles], [c["h"] for c in candles],
            [c["l"] for c in candles], [c["c"] for c in candles],
            periods=p.get("periods", 15), multiplier=p.get("multiplier", 9.1),
            src=p.get("src", "hl2"), change_atr=p.get("change_atr", True),
        )
        s = st_state(candles, st) if st.get("trend") else {}
        out.append({"tf": tf, **s} if s else {"tf": tf, "trend": None})
    return out


def grade(sig: dict, verdict: str) -> str:
    """A 顺势且突破干脆 / B 可参与 / C 逆 Bias，仅提示。

    verdict == 'mixed' 时没有哪个方向算「顺势」，但也不该一律打成逆势 ——
    这时只按翻转自身质量给 B / C。
    """
    if verdict == "mixed":
        return "B" if sig.get("score", 0) >= 2 else "C"
    aligned = (sig["type"] == "buy" and verdict == "bull") or \
              (sig["type"] == "sell" and verdict == "bear")
    if not aligned:
        return "C"
    return "A" if sig.get("score", 0) >= 2 else "B"


def advice(verdict: str, st_rows: list[dict]) -> str:
    big = next((r for r in st_rows if r["tf"] == "1d" and r.get("trend")), None) or \
          next((r for r in st_rows if r["tf"] == "4h" and r.get("trend")), None)
    if verdict == "bull":
        s = "MTF 偏多，回踩超趋线不破可顺势做多"
    elif verdict == "bear":
        s = "MTF 偏空，反抽超趋线不破可顺势做空"
    else:
        s = "各周期方向分歧，震荡概率大，等高周期表态"
    if big and big.get("gap_atr") is not None and abs(big["gap_atr"]) > 3:
        s += "；大周期已离超趋线 3 ATR 以上，追单风险高"
    return s


def mtf_st_consistency(candles_by_tf: dict, p: dict, target_tf: str) -> dict:
    """多周期SuperTrend方向一致性

    震荡市特征：各周期ST方向混乱，频繁翻转
    趋势市特征：多数周期方向一致

    返回：
        consistency: float - 与目标周期一致的比例（0~1）
        aligned: bool - 是否满足一致性要求（由外部阈值判断）
        trends: dict - 各周期当前趋势方向
        flip_counts: dict - 各周期近期翻转次数
        big_tf_flips: int - 大周期（4h+1d）翻转次数总和
        reason: str - 判定原因
    """
    key_tfs = ["15m", "30m", "1h", "4h", "1d"]
    trends = {}
    flip_counts = {}

    for tf in key_tfs:
        candles = candles_by_tf.get(tf, [])
        if not candles:
            continue

        st = super_trend(
            [c["o"] for c in candles], [c["h"] for c in candles],
            [c["l"] for c in candles], [c["c"] for c in candles],
            periods=p.get("periods", 15),
            multiplier=p.get("multiplier", 9.1),
            src=p.get("src", "hl2"),
            change_atr=p.get("change_atr", True)
        )

        if st.get("trend") and st["trend"][-1] is not None:
            trends[tf] = st["trend"][-1]
            # 统计最近20根的翻转次数
            recent_trend = st["trend"][-20:] if len(st["trend"]) >= 20 else st["trend"]
            flips = sum(1 for i in range(1, len(recent_trend))
                       if recent_trend[i] != recent_trend[i-1]
                       and recent_trend[i] is not None
                       and recent_trend[i-1] is not None)
            flip_counts[tf] = flips

    if not trends:
        return {"consistency": 0, "aligned": False, "trends": {}, "flip_counts": {},
                "big_tf_flips": 0, "reason": "数据不足"}

    # 计算与目标周期一致的比例
    target_trend = trends.get(target_tf)
    if target_trend is None:
        return {"consistency": 0, "aligned": False, "trends": trends,
                "flip_counts": flip_counts, "big_tf_flips": 0, "reason": "目标周期无数据"}

    aligned_count = sum(1 for t in trends.values() if t == target_trend)
    consistency = aligned_count / len(trends)

    # 高周期翻转频繁 → 震荡
    big_tf_flips = flip_counts.get("4h", 0) + flip_counts.get("1d", 0)

    return {
        "consistency": round(consistency, 2),
        "trends": trends,
        "flip_counts": flip_counts,
        "big_tf_flips": big_tf_flips,
        "reason": f"大周期频繁翻转({big_tf_flips}次)" if big_tf_flips > 5 else
                  f"方向分歧({consistency:.0%})" if consistency < 0.6 else
                  f"共振({consistency:.0%})"
    }


def overview(candles_by_tf: dict[str, list[dict]], p: dict) -> dict:
    """盯盘总览：Bias 表 + ST 表 + 结论。"""
    b = mtf_bias(candles_by_tf, p)
    st_rows = st_table(candles_by_tf, p)
    return {"bias": b, "st": st_rows, "advice": advice(b["verdict"], st_rows)}


def evaluate(candles_by_tf: dict[str, list[dict]], p: dict, sig: dict) -> dict:
    """给刚产生的翻转信号补 Bias 上下文，供高亮与推送使用。"""
    b = mtf_bias(candles_by_tf, p)
    return {
        **sig,
        "grade":     grade(sig, b["verdict"]),
        "bias_score": b["score"],
        "verdict":   b["verdict"],
        "bias_label": b["label"],
        "bulls":     b["bulls"],
        "bears":     b["bears"],
    }
