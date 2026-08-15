"""
Pine → Python 移植：«Signal Engine Quantum Edge» (© Quantum Edge Capital LLC, MPL-2.0)

原脚本 = KivancOzbilgic 版 SuperTrend + MTF Bias 表。逐行对照如下。

    // ATR / SUPERTREND CORE
    atr2  = ta.sma(ta.tr, Periods)
    atr   = changeATR ? ta.atr(Periods) : atr2      → atr(): RMA 或 SMA

    up  = src - Multiplier * atr
    up1 = nz(up[1], up)
    up := close[1] > up1 ? math.max(up, up1) : up

    dn  = src + Multiplier * atr
    dn1 = nz(dn[1], dn)
    dn := close[1] < dn1 ? math.min(dn, dn1) : dn

    trend  = 1
    trend := nz(trend[1], trend)
    trend := trend == -1 and close > dn1 ?  1 :
             trend ==  1 and close < up1 ? -1 : trend

    buySignal  = trend ==  1 and trend[1] == -1
    sellSignal = trend == -1 and trend[1] ==  1

三处易错、这里严格照抄的细节：
  1. 棘轮和翻转判定用的都是 up1/dn1（**前一根**的最终轨），不是当根 up/dn；
  2. trend 的种子值是 1（Pine `trend = 1` + `nz(trend[1], trend)`），不是按价格猜方向；
  3. ta.tr 首根 = high-low（无 close[1]），ta.atr 用 RMA(Wilder) 而非 SMA。

作图侧同样照抄：
    upPlot = plot(trend ==  1 ? up : na)   → 多头段只画 up，空头段断开(na)
    dnPlot = plot(trend == -1 ? dn : na)
    plotshape(buySignal ? up : na, 'Buy')  → 买点标签打在 up 上，卖点打在 dn 上
    fill(ohlc4, upPlot/dnPlot)             → 高亮区在 ohlc4 与趋势线之间

    // MTF BIAS：fast MA >= slow MA → Bull，否则 Bear（用 MA，不是 ST）
    f_bias(tf) = ma(close, fastLen) >= ma(close, slowLen) ? 1 : -1
"""

from typing import Optional

Num = Optional[float]


# ─── Pine 基础函数 ───────────────────────────────────────────────

def ta_tr(highs: list[float], lows: list[float], closes: list[float]) -> list[float]:
    """ta.tr：首根无 close[1]，退化为 high-low。"""
    n = len(closes)
    tr = [0.0] * n
    if n:
        tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    return tr


def ta_rma(values: list[float], length: int) -> list[Num]:
    """ta.rma（Wilder 平滑）：第 length 根用 SMA 播种，之后递推。"""
    n = len(values)
    out: list[Num] = [None] * n
    if n < length or length <= 0:
        return out
    prev = sum(values[:length]) / length
    out[length - 1] = prev
    for i in range(length, n):
        prev = (prev * (length - 1) + values[i]) / length
        out[i] = prev
    return out


def ta_sma(values: list[float], length: int) -> list[Num]:
    n = len(values)
    out: list[Num] = [None] * n
    if n < length or length <= 0:
        return out
    s = sum(values[:length])
    out[length - 1] = s / length
    for i in range(length, n):
        s += values[i] - values[i - length]
        out[i] = s / length
    return out


def ta_ema(values: list[float], length: int) -> list[Num]:
    """ta.ema：Pine 同样用 SMA 播种。"""
    n = len(values)
    out: list[Num] = [None] * n
    if n < length or length <= 0:
        return out
    alpha = 2 / (length + 1)
    prev = sum(values[:length]) / length
    out[length - 1] = prev
    for i in range(length, n):
        prev = values[i] * alpha + prev * (1 - alpha)
        out[i] = prev
    return out


def ta_atr(highs, lows, closes, length: int) -> list[Num]:
    """ta.atr(length) = ta.rma(ta.tr, length)"""
    return ta_rma(ta_tr(highs, lows, closes), length)


def ta_adx(highs: list[float], lows: list[float], closes: list[float],
           length: int = 14) -> list[Num]:
    """ADX（平均趋向指数），0~100。

    ADX < 20 → 无趋势 / 震荡；ADX > 25 → 趋势确认；ADX > 40 → 强趋势。
    用 Wilder RMA 平滑（与 Pine ta.adx 一致）。
    """
    n = len(closes)
    out: list[Num] = [None] * n
    if n < length * 2 + 1 or length <= 0:
        return out

    tr   = ta_tr(highs, lows, closes)
    pdm  = [0.0] * n   # +DM
    ndm  = [0.0] * n   # -DM

    for i in range(1, n):
        up   = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        pdm[i] = up   if up > 0 and up > down   else 0.0
        ndm[i] = down if down > 0 and down > up else 0.0

    atr_s  = ta_rma(tr,  length)
    pdm_s  = ta_rma(pdm, length)
    ndm_s  = ta_rma(ndm, length)

    dx: list[Num] = [None] * n
    for i in range(n):
        if atr_s[i] is None or pdm_s[i] is None or ndm_s[i] is None:
            continue
        if atr_s[i] <= 0:
            continue
        pdi = 100 * pdm_s[i] / atr_s[i]
        ndi = 100 * ndm_s[i] / atr_s[i]
        denom = pdi + ndi
        dx[i] = 100 * abs(pdi - ndi) / denom if denom > 0 else 0.0

    adx = ta_rma([v if v is not None else 0.0 for v in dx], length)
    # 前 length*2 根 RMA 未成型，置 None
    for i in range(min(length * 2, n)):
        adx[i] = None
    return adx


def ma(values: list[float], length: int, ma_type: str = "EMA") -> list[Num]:
    """Pine 的 f_ma：maType == 'EMA' ? ta.ema : ta.sma"""
    return ta_ema(values, length) if ma_type.upper() == "EMA" else ta_sma(values, length)


def source(highs, lows, closes, opens=None, name: str = "hl2") -> list[float]:
    """input.source 支持的常用序列。脚本默认 hl2，高亮区用 ohlc4。"""
    n = len(closes)
    name = (name or "hl2").lower()
    if name == "close":
        return list(closes)
    if name == "open" and opens is not None:
        return list(opens)
    if name == "high":
        return list(highs)
    if name == "low":
        return list(lows)
    if name == "hlc3":
        return [(highs[i] + lows[i] + closes[i]) / 3 for i in range(n)]
    if name == "ohlc4" and opens is not None:
        return [(opens[i] + highs[i] + lows[i] + closes[i]) / 4 for i in range(n)]
    return [(highs[i] + lows[i]) / 2 for i in range(n)]   # hl2


# ─── SUPERTREND CORE（逐行对应 Pine） ────────────────────────────

def super_trend(
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    periods: int = 15,
    multiplier: float = 9.1,
    src: str = "hl2",
    change_atr: bool = True,
) -> dict:
    """
    返回（各数组与 K 线等长，ATR 未成型的前几根为 None）：
      up / dn    最终上升轨、下降轨（Pine 的 up、dn）
      trend      +1 / -1
      up_plot    trend == 1 ? up : None    ← 直接给前端画「多头段」
      dn_plot    trend == -1 ? dn : None   ← 「空头段」
      ohlc4      高亮区的另一条边界
      atr        ATR 序列
      flips      翻转点 [{i, type}]，type = buy / sell
    """
    n = len(closes)
    empty = {"up": [], "dn": [], "trend": [], "up_plot": [], "dn_plot": [],
             "ohlc4": [], "atr": [], "flips": []}
    if n == 0 or n < periods + 1:
        return empty

    tr = ta_tr(highs, lows, closes)
    atr = ta_rma(tr, periods) if change_atr else ta_sma(tr, periods)
    srcv = source(highs, lows, closes, opens, src)

    up: list[Num] = [None] * n
    dn: list[Num] = [None] * n
    trend: list[Optional[int]] = [None] * n
    flips: list[dict] = []

    start = next((i for i in range(n) if atr[i] is not None), None)
    if start is None:
        return empty

    # 首根有效 K：nz(up[1], up) → up1 == up，棘轮条件必不成立，取原始值；
    # nz(trend[1], trend) → 种子 1（与 Pine 的 `trend = 1` 一致）
    up[start] = srcv[start] - multiplier * atr[start]
    dn[start] = srcv[start] + multiplier * atr[start]
    trend[start] = 1

    for i in range(start + 1, n):
        up1 = up[i - 1]
        dn1 = dn[i - 1]

        raw_up = srcv[i] - multiplier * atr[i]
        raw_dn = srcv[i] + multiplier * atr[i]
        up[i] = max(raw_up, up1) if closes[i - 1] > up1 else raw_up
        dn[i] = min(raw_dn, dn1) if closes[i - 1] < dn1 else raw_dn

        prev = trend[i - 1]
        if prev == -1 and closes[i] > dn1:
            cur = 1
        elif prev == 1 and closes[i] < up1:
            cur = -1
        else:
            cur = prev
        trend[i] = cur
        if cur != prev:
            flips.append({"i": i, "type": "buy" if cur == 1 else "sell"})

    r = lambda v: round(v, 6) if v is not None else None
    ohlc4 = source(highs, lows, closes, opens, "ohlc4")
    return {
        "up":      [r(v) for v in up],
        "dn":      [r(v) for v in dn],
        "trend":   trend,
        # plot.style_linebr：非当前趋势的一侧填 None，前端画成断线
        "up_plot": [r(up[i]) if trend[i] == 1 else None for i in range(n)],
        "dn_plot": [r(dn[i]) if trend[i] == -1 else None for i in range(n)],
        "ohlc4":   [r(v) for v in ohlc4],
        "atr":     [r(v) for v in atr],
        "flips":   flips,
    }


# ─── MTF BIAS ENGINE ────────────────────────────────────────────

def bias(closes: list[float], fast_len: int = 20, slow_len: int = 50,
         ma_type: str = "EMA") -> Optional[int]:
    """f_bias：fast_val >= slow_val ? 1 : -1。数据不足返回 None（Pine 里是 na）。

    注：Pine 用 request.security(lookahead_off) 取高周期值；这里直接对高周期
    自己的 K 线序列算 MA，等价且天然无未来函数。
    """
    fast = ma(closes, fast_len, ma_type)
    slow = ma(closes, slow_len, ma_type)
    if not fast or not slow or fast[-1] is None or slow[-1] is None:
        return None
    return 1 if fast[-1] >= slow[-1] else -1


def bias_detail(candles: list[dict], fast_len: int, slow_len: int, ma_type: str) -> dict:
    """给 Bias 表一行用：方向 + 两条 MA 的值和乖离。"""
    if not candles:
        return {"bias": None}
    closes = [c["c"] for c in candles]
    fast = ma(closes, fast_len, ma_type)
    slow = ma(closes, slow_len, ma_type)
    fv = fast[-1] if fast else None
    sv = slow[-1] if slow else None
    if fv is None or sv is None:
        return {"bias": None}
    return {
        "bias":    1 if fv >= sv else -1,
        "fast":    round(fv, 4),
        "slow":    round(sv, 4),
        "spread":  round((fv - sv) / sv * 100, 2) if sv else 0.0,
        "close":   closes[-1],
    }


# ─── 由 SuperTrend 派生的盯盘信息 ────────────────────────────────

def st_signals(candles: list[dict], st: dict, tf: str) -> list[dict]:
    """把 flips 翻成买卖点。

    Pine 里 buySignal 的标签画在 up 上、sellSignal 画在 dn 上，
    所以这里的 `line` 就是标签落点，也正好是这笔单的初始跟踪止损位。

    强度 0-3（原脚本没有，属于本项目补充的过滤维度）：
      +1 翻转当根实体方向与信号一致
      +1 当根量能 > 前 20 根均量 ×1.2
      +1 收盘距被突破的轨道 > 0.3 ATR（突破干脆，不是贴着轨道磨）
    """
    out = []
    n = len(candles)
    vols = [c["vol"] for c in candles]

    for f in st["flips"]:
        i = f["i"]
        if i >= n:
            continue
        c = candles[i]
        a = st["atr"][i] or 0.0
        is_buy = f["type"] == "buy"
        line = st["up"][i] if is_buy else st["dn"][i]      # 标签落点 / 初始止损
        ref  = st["dn"][i - 1] if is_buy else st["up"][i - 1]   # 被突破的轨（dn1/up1）
        dist = abs(c["c"] - ref) if ref is not None else 0.0

        score = 0
        if (is_buy and c["c"] > c["o"]) or (not is_buy and c["c"] < c["o"]):
            score += 1
        base = vols[max(0, i - 20):i]
        if base and c["vol"] > (sum(base) / len(base)) * 1.2:
            score += 1
        if a > 0 and dist > 0.3 * a:
            score += 1

        chg = (candles[-1]["c"] - c["c"]) / c["c"] * 100
        out.append({
            "tf":         tf,
            "ts":         c["ts"],
            "type":       f["type"],
            "price":      round(c["c"], 6),
            "line":       line,
            "atr":        a,
            "score":      score,
            "bars_since": n - 1 - i,
            "pnl_pct":    round(chg if is_buy else -chg, 2),
        })
    return out


def st_state(candles: list[dict], st: dict) -> dict:
    """当前趋势状态摘要（面板顶部 / 图例用）。

    `seeded` 标记「这段趋势从未真正翻转过」：Pine 的 `trend = 1` 是个种子值，
    K 线不够长（或 ATR 倍数过大）时整段序列都保持初始方向，从没出现过 buySignal。
    这种「多头」只是初值，不能当信号看 —— 尤其 1w / 1M 这类根数少的周期。
    """
    if not candles or not st.get("trend"):
        return {}
    i = len(candles) - 1
    trend = st["trend"]
    if i >= len(trend) or trend[i] is None:
        return {}

    cur = trend[i]
    j = i
    while j > 0 and trend[j - 1] == cur:
        j -= 1

    last = candles[-1]
    line = st["up"][i] if cur == 1 else st["dn"][i]
    a = st["atr"][i] or 0.0
    gap = last["c"] - line if line is not None else 0.0
    entry = candles[j]["c"]
    return {
        "trend":    cur,
        "line":     line,                                  # 跟踪止损位
        "atr":      a,
        "bars":     i - j + 1,                             # 本轮趋势持续根数
        "since_ts": candles[j]["ts"],
        "entry":    round(entry, 6),
        "gap":      round(gap, 6),
        "gap_pct":  round(gap / line * 100, 2) if line else 0.0,
        "gap_atr":  round(gap / a, 2) if a else 0.0,       # 离轨几个 ATR
        "run_pct":  round((last["c"] - entry) / entry * 100 * (1 if cur == 1 else -1), 2),
        "price":    last["c"],
        # 全程没翻转过 → 当前方向只是 Pine 的初始种子值，不是信号
        "seeded":   not st.get("flips"),
    }


def bar_colors(st: dict) -> list[Optional[int]]:
    """Pine 的 barcolor：buy1[1] < sell1[1] → Bull，反之 Bear。
    等价于「上一根所处的趋势方向」，故直接取 trend 位移一根。"""
    trend = st.get("trend") or []
    return [None] + list(trend[:-1]) if trend else []


def compute(candles: list[dict], tf: str, p: dict) -> dict:
    """单周期全量计算，供 /api/indicators 与 WS 使用。"""
    if not candles:
        return {}
    opens  = [c["o"] for c in candles]
    highs  = [c["h"] for c in candles]
    lows   = [c["l"] for c in candles]
    closes = [c["c"] for c in candles]

    st = super_trend(
        opens, highs, lows, closes,
        periods=p.get("periods", 15), multiplier=p.get("multiplier", 9.1),
        src=p.get("src", "hl2"), change_atr=p.get("change_atr", True),
    )
    if not st.get("trend"):
        return {"tf": tf, "ts": [c["ts"] for c in candles], "st": {}, "signals": [], "state": {}}

    fast_len = p.get("fast_len", 20)
    slow_len = p.get("slow_len", 50)
    ma_type  = p.get("ma_type", "EMA")
    return {
        "tf": tf,
        "ts": [c["ts"] for c in candles],
        "st": {
            "up":      st["up"],
            "dn":      st["dn"],
            "trend":   st["trend"],
            "up_plot": st["up_plot"],
            "dn_plot": st["dn_plot"],
            "ohlc4":   st["ohlc4"],
            "atr":     st["atr"],
        },
        "bar_color": bar_colors(st),
        "signals":   st_signals(candles, st, tf),
        "state":     st_state(candles, st),
        "fast_ma":   [round(v, 4) if v is not None else None for v in ma(closes, fast_len, ma_type)],
        "slow_ma":   [round(v, 4) if v is not None else None for v in ma(closes, slow_len, ma_type)],
    }
