"""趋势形态识别（对应根目录 `趋势形态识别.md` 的设计）。

输入一段 K 线（本项目用于 4h），逐根输出趋势方向：
    dir = +1  上行（上行趋势 / 震荡上行）
    dir = -1  下行（下行趋势 / 震荡下行）
    dir =  0  无明显趋势（宽幅震荡 / 走平缠绕）

算法：Pivot(ZigZag) 极值 → 高低点斜率(ΔPH,ΔPL) → 通道重叠度(OR) → ADX/MA 辅助，
按文档决策树判定。与主图 SuperTrend 解耦，可对任意周期独立调用。
"""
from __future__ import annotations

from typing import Optional

from indicators import ta_adx, ma


def _pivots(highs: list[float], lows: list[float], k: int = 5) -> list[dict]:
    """局部极值：H_i 为窗口内最高 → 波峰；L_i 为窗口内最低 → 波谷。

    返回升序列表，元素 {i, price, type:'H'/'L'}。相邻极值交替出现由后续
    识别逻辑按时间取最近 N 个处理。
    """
    n = len(highs)
    out: list[dict] = []
    for i in range(k, n - k):
        h = highs[i]
        if all(highs[j] <= h for j in range(i - k, i + k + 1)):
            out.append({"i": i, "price": h, "type": "H"})
        l = lows[i]
        if all(lows[j] >= l for j in range(i - k, i + k + 1)):
            out.append({"i": i, "price": l, "type": "L"})
    return out


def recognize(
    candles: list[dict],
    k: int = 5,
    adx_len: int = 14,
    fast: int = 20,
    slow: int = 60,
    ma_type: str = "EMA",
) -> dict:
    """对 K 线序列做趋势形态识别。

    返回：
      pattern : 与 candles 等长的列表，每根 K 一项
                {ts, dir, label, dph, dpl, adx, ma20, ma60, or_up, or_down}
                dir 为 None 表示该根数据不足（序列早期）。
      pivots  : 极值点列表（前端画标记用）[{i, ts, price, type}]
    """
    n = len(candles)
    empty = {"pattern": [], "pivots": []}
    if n < max(k * 2 + 1, adx_len * 2 + 1, slow):
        return empty

    highs = [c["h"] for c in candles]
    lows = [c["l"] for c in candles]
    closes = [c["c"] for c in candles]
    tss = [c["ts"] for c in candles]

    piv = _pivots(highs, lows, k)
    ph = [p for p in piv if p["type"] == "H"]
    pl = [p for p in piv if p["type"] == "L"]

    adx = ta_adx(highs, lows, closes, adx_len)
    ma20 = ma(closes, fast, ma_type)
    ma60 = ma(closes, slow, ma_type)

    slope_win = slow  # MA 斜率参考窗口

    pattern: list[dict] = []
    for i in range(n):
        rec = {
            "ts": tss[i],
            "dir": None,
            "label": "数据不足",
            "dph": None,
            "dpl": None,
            "adx": round(adx[i], 2) if adx[i] is not None else None,
            "ma20": round(ma20[i], 4) if ma20[i] is not None else None,
            "ma60": round(ma60[i], 4) if ma60[i] is not None else None,
            "or_up": None,
            "or_down": None,
        }

        ph_i = [p for p in ph if p["i"] <= i]
        pl_i = [p for p in pl if p["i"] <= i]
        if (len(ph_i) < 2 or len(pl_i) < 2 or adx[i] is None
                or ma20[i] is None or ma60[i] is None):
            pattern.append(rec)
            continue

        PHm, PHm1 = ph_i[-1]["price"], ph_i[-2]["price"]
        PLm, PLm1 = pl_i[-1]["price"], pl_i[-2]["price"]
        dph = PHm - PHm1
        dpl = PLm - PLm1

        s0 = max(0, i - slope_win)
        slope20 = (ma20[i] - ma20[s0]) if (ma20[s0] is not None) else 0.0
        ma_up = ma20[i] > ma60[i]
        ma_down = ma20[i] < ma60[i]

        denom_up = PHm - PLm1
        denom_down = PHm1 - PLm
        or_up = max(0.0, PHm1 - PLm) / denom_up if denom_up > 0 else 0.0
        or_down = max(0.0, PHm - PLm1) / denom_down if denom_down > 0 else 0.0

        adx_v = adx[i]

        # ── 决策树（对应文档第四节）──
        ma_flat = abs(ma20[i] - ma60[i]) / ma60[i] < 0.005 if ma60[i] else True
        no_trend = (adx_v < 20) or ma_flat

        if no_trend:
            rec["dir"], rec["label"] = 0, "无明显趋势"
        elif dph > 0 and dpl > 0:
            if ma_up and slope20 > 0 and or_up < 0.25 and adx_v >= 25:
                rec["dir"], rec["label"] = 1, "上行趋势"
            else:
                rec["dir"], rec["label"] = 1, "震荡上行"
        elif dph < 0 and dpl < 0:
            if ma_down and slope20 < 0 and or_down < 0.25 and adx_v >= 25:
                rec["dir"], rec["label"] = -1, "下行趋势"
            else:
                rec["dir"], rec["label"] = -1, "震荡下行"
        else:
            rec["dir"], rec["label"] = 0, "无明显趋势"

        rec["dph"] = round(dph, 4)
        rec["dpl"] = round(dpl, 4)
        rec["or_up"] = round(or_up, 3)
        rec["or_down"] = round(or_down, 3)
        pattern.append(rec)

    pivots_out = [
        {"i": p["i"], "ts": tss[p["i"]], "price": round(p["price"], 6),
         "type": p["type"]}
        for p in piv
    ]
    return {"pattern": pattern, "pivots": pivots_out}
