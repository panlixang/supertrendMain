"""
Market Regime Engine V2 —— 从 frontend/src/utils/regimeModel.js 精确 port（Python）。
用于离线回测：输入 K线 [{ts,o,h,l,c,vol}]，输出逐根市场状态 + 买卖信号序列。

与 JS 版本保持一致（V3 升级：震荡细分为三类）：
  模块1 Regime Detection -> trend/range/transition 三概率
  模块2 S/R Zone (Pivot+ATR聚类，强度含假突破拒绝)
  模块3 Exhaustion (边界/ATR/量/区间/MA30/ER提升)
  模块4 State Machine (明确切换阈值；突破确认用 ST方向；range 分支按 drift/MA30 细分)

6 状态市场分类：
  trend            -> SuperTrend 趋势跟随
  uptrend_consol   -> 上涨整理：回踩低吸（禁止高抛）
  neutral_range    -> 真正横盘：高抛低吸（均值回归）
  downtrend_consol -> 下跌整理：反弹做空（禁止低吸）
  transition       -> 压缩末端：监听突破
  breakout         -> 突破：放量+ATR扩张+ST方向一致，顺势介入

回测信号生成（JS 是“仅展示”，这里补全为可交易策略）：
  trend            -> 跟随 regime 内部 SuperTrend(14,3) 方向（翻转即换仓）
  neutral_range    -> S/R 均值回归（下沿买/上沿卖，带过滤）
  uptrend_consol   -> 只下沿买；支撑失守平多
  downtrend_consol -> 只上沿卖；压力失守平空
  transition/breakout -> 放量+ATR扩张+ST方向一致的突破信号
"""
import math

ER_N = 20
MA_P = 30
MA_LOOK = 10
ADX_P = 14
ATR_P = 14
PIVOT_LR = 3
ST_FACTOR = 3
WARMUP = MA_P + ADX_P + 10
DRIFT_K = 20          # 震荡子分类的漂移窗口（bar 数）

# 震荡子分类阈值（在 range 概率高、trend 概率低的分支内进一步判定方向）
#   上涨整理: MA30 斜率向上 或 价格净漂移为正
#   下跌整理: MA30 斜率向下 或 价格净漂移为负
#   真正横盘: 二者都不显著（Drift≈0，MA30 水平）
RANGE_UP_SLOPE = 0.12
RANGE_DOWN_SLOPE = -0.12
RANGE_UP_DRIFT = 1.2
RANGE_DOWN_DRIFT = -1.2


def avg(arr):
    return sum(arr) / len(arr) if arr else 0.0


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def sma(values, p):
    n = len(values)
    out = [None] * n
    s = 0.0
    for i in range(n):
        s += values[i]
        if i >= p:
            s -= values[i - p]
        if i >= p - 1:
            out[i] = s / p
    return out


def wilderATR(candles, p=ATR_P):
    n = len(candles)
    out = [None] * n
    prev = 0.0
    for i in range(1, n):
        h = candles[i]["h"]; l = candles[i]["l"]; c0 = candles[i - 1]["c"]
        tr = max(h - l, abs(h - c0), abs(l - c0))
        prev = tr if i == 1 else prev - prev / p + tr / p
        if i >= p:
            out[i] = prev
    return out


def efficiencyRatio(candles, n=ER_N):
    ln = len(candles)
    out = [None] * ln
    c = [x["c"] for x in candles]
    for i in range(n, ln):
        net = abs(c[i] - c[i - n])
        s = sum(abs(c[j] - c[j - 1]) for j in range(i - n + 1, i + 1))
        out[i] = net / s if s > 0 else 0.0
    return out


def computeADX(candles, p=ADX_P):
    n = len(candles)
    tr = [0.0] * n; pDM = [0.0] * n; mDM = [0.0] * n
    for i in range(1, n):
        h = candles[i]["h"]; l = candles[i]["l"]; ph = candles[i - 1]["h"]; pl = candles[i - 1]["l"]
        up = h - ph; down = pl - l
        pDM[i] = up if (up > down and up > 0) else 0.0
        mDM[i] = down if (down > up and down > 0) else 0.0
        tr[i] = max(h - l, abs(h - candles[i - 1]["c"]), abs(l - candles[i - 1]["c"]))
    sTR = [0.0] * n; sPD = [0.0] * n; sMD = [0.0] * n
    for i in range(1, n):
        if i == 1:
            sTR[i] = tr[i]; sPD[i] = pDM[i]; sMD[i] = mDM[i]
        else:
            sTR[i] = sTR[i - 1] - sTR[i - 1] / p + tr[i] / p
            sPD[i] = sPD[i - 1] - sPD[i - 1] / p + pDM[i] / p
            sMD[i] = sMD[i - 1] - sMD[i - 1] / p + mDM[i] / p
    pDI = [0.0] * n; mDI = [0.0] * n; dx = [0.0] * n
    for i in range(p, n):
        a = sTR[i]
        pDI[i] = (sPD[i] / a) * 100 if a else 0.0
        mDI[i] = (sMD[i] / a) * 100 if a else 0.0
        sm = pDI[i] + mDI[i]
        dx[i] = (abs(pDI[i] - mDI[i]) / sm) * 100 if sm else 0.0
    out = [None] * n
    prev = 0.0
    for i in range(1, n):
        if i == 2 * p - 1:
            s = sum(dx[j] for j in range(p, i + 1))
            out[i] = s / p; prev = s / p
        elif i > 2 * p - 1:
            prev = prev - prev / p + dx[i] / p
            out[i] = prev
    return out


def computeSuperTrend(candles, factor=ST_FACTOR, atrArr=None, p=ATR_P):
    n = len(candles)
    trend = [0] * n; flip = [False] * n; distance = [None] * n
    if n <= p or atrArr is None:
        return {"trend": trend, "flip": flip, "distance": distance}
    hl2 = [(c["h"] + c["l"]) / 2 for c in candles]
    prevUp = hl2[p] + factor * (atrArr[p] or 1)
    prevLo = hl2[p] - factor * (atrArr[p] or 1)
    st = prevLo; dirn = 1
    trend[p] = 1
    distance[p] = (candles[p]["c"] - st) / (atrArr[p] or 1)
    for i in range(p + 1, n):
        a = atrArr[i] or atrArr[i - 1] or 1
        wasLower = st == prevLo
        u = hl2[i] + factor * a
        l = hl2[i] - factor * a
        u = min(u, prevUp) if candles[i - 1]["c"] <= prevUp else u
        l = max(l, prevLo) if candles[i - 1]["c"] >= prevLo else l
        prevUp = u; prevLo = l
        if wasLower:
            if candles[i]["c"] < l:
                st = u; dirn = -1; flip[i] = True
            else:
                st = l; dirn = 1
        else:
            if candles[i]["c"] > u:
                st = l; dirn = 1; flip[i] = True
            else:
                st = u; dirn = -1
        trend[i] = dirn
        distance[i] = (candles[i]["c"] - st) / a
    return {"trend": trend, "flip": flip, "distance": distance}


def rangeOf(candles, i, k):
    mn = math.inf; mx = -math.inf
    for j in range(max(0, i - k + 1), i + 1):
        mn = min(mn, candles[j]["l"]); mx = max(mx, candles[j]["h"])
    return mx - mn


def findPivots(candles, lr=PIVOT_LR):
    n = len(candles)
    highs = []; lows = []
    for i in range(lr, n - lr):
        isH = True; isL = True
        for k in range(1, lr + 1):
            if candles[i]["h"] < candles[i - k]["h"] or candles[i]["h"] < candles[i + k]["h"]:
                isH = False
            if candles[i]["l"] > candles[i - k]["l"] or candles[i]["l"] > candles[i + k]["l"]:
                isL = False
        if isH:
            highs.append({"i": i, "price": candles[i]["h"], "vol": candles[i]["vol"], "ts": candles[i]["ts"]})
        if isL:
            lows.append({"i": i, "price": candles[i]["l"], "vol": candles[i]["vol"], "ts": candles[i]["ts"]})
    return {"highs": highs, "lows": lows}


def clusterZones(pivots, atrArr):
    clusters = []
    for p in pivots:
        thr = 0.5 * (atrArr[p["i"]] or 0)
        if thr <= 0:
            clusters.append({"members": [p], "center": p["price"]})
            continue
        best = None; bd = math.inf
        for cl in clusters:
            d = abs(p["price"] - cl["center"])
            if d < thr and d < bd:
                best = cl; bd = d
        if best:
            best["members"].append(p)
            best["center"] = avg([m["price"] for m in best["members"]])
        else:
            clusters.append({"members": [p], "center": p["price"]})
    merged = True
    while merged:
        merged = False
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                ta = 0.5 * (atrArr[clusters[a]["members"][0]["i"]] or 0)
                tb = 0.5 * (atrArr[clusters[b]["members"][0]["i"]] or 0)
                if abs(clusters[a]["center"] - clusters[b]["center"]) < max(ta, tb) + 1e-9:
                    clusters[a]["members"] = clusters[a]["members"] + clusters[b]["members"]
                    clusters[a]["center"] = avg([m["price"] for m in clusters[a]["members"]])
                    clusters.pop(b)
                    merged = True
                    break
            if merged:
                break
    return clusters


def scoreZone(cluster, type_, candles, atrArr):
    members = cluster["members"]
    nn = len(candles)
    prices = [m["price"] for m in members]
    touches = len(members)
    bounceSum = 0.0
    for m in members:
        a = atrArr[m["i"]] or 1
        end = min(nn - 1, m["i"] + 3)
        if end > m["i"]:
            if type_ == "resistance":
                mn = min(candles[k]["l"] for k in range(m["i"], end + 1))
                bounceSum += max(0, (m["price"] - mn) / a / 2)
            else:
                mx = max(candles[k]["h"] for k in range(m["i"], end + 1))
                bounceSum += max(0, (mx - m["price"]) / a / 2)
    avgBounce = bounceSum / touches if touches else 0.0
    avgRecency = avg([m["i"] for m in members]) / nn
    baseVol = avg([c["vol"] for c in candles])
    avgVol = avg([m["vol"] for m in members])
    volRatio = avgVol / baseVol if baseVol > 0 else 1.0
    high = max(prices); low = min(prices)
    rejections = 0
    frm = max(0, nn - 60)
    for k in range(frm, nn):
        if type_ == "resistance":
            if candles[k]["h"] > high and candles[k]["c"] <= high:
                rejections += 1
        elif candles[k]["l"] < low and candles[k]["c"] >= low:
            rejections += 1
    strength = 100 * (
        0.30 * clamp(touches / 5, 0, 1) +
        0.25 * clamp(avgBounce, 0, 1) +
        0.15 * clamp(avgRecency, 0, 1) +
        0.10 * clamp(volRatio, 0, 1) +
        0.20 * clamp(rejections / 3, 0, 1)
    )
    return {
        "type": type_, "center": cluster["center"], "high": high, "low": low,
        "strength": round(strength * 10) / 10, "touches": touches,
        "rejections": rejections, "lastTs": members[-1]["ts"],
    }


def zonesAt(candles, i, atrArr, win=120):
    sub = candles[max(0, i - win + 1): i + 1]
    if len(sub) < 2 * PIVOT_LR + 1:
        return None, None
    pv = findPivots(sub)
    res = [scoreZone(cl, "resistance", sub, atrArr) for cl in clusterZones(pv["highs"], atrArr)]
    sup = [scoreZone(cl, "support", sub, atrArr) for cl in clusterZones(pv["lows"], atrArr)]
    res.sort(key=lambda z: z["strength"], reverse=True)
    sup.sort(key=lambda z: z["strength"], reverse=True)
    return (res[0] if res else None), (sup[0] if sup else None)


def regimeScoresAt(i, ctx):
    erPrevIdx = max(MA_LOOK, i - MA_LOOK)
    er = ctx["er"][i] if ctx["er"][i] is not None else 0.5
    erPrev = ctx["er"][erPrevIdx] if ctx["er"][erPrevIdx] is not None else er
    adx = ctx["adx"][i] if ctx["adx"][i] is not None else 25
    adxPrev = ctx["adx"][erPrevIdx] if ctx["adx"][erPrevIdx] is not None else adx
    slope = ctx["ma30Slope"][i] if ctx["ma30Slope"][i] is not None else 0
    slopePrev = ctx["ma30Slope"][erPrevIdx] if ctx["ma30Slope"][erPrevIdx] is not None else slope
    r20 = ctx["range20"][i] or 0
    r50 = ctx["range50"][i] or 1
    comp = r20 / r50 if r50 > 0 else 1

    ER_score = clamp(er / 0.4, 0, 1)
    ADX_score = clamp(adx / 25, 0, 1)
    MA_score = clamp(abs(slope) / 0.2, 0, 1)
    ADX_low = clamp((25 - adx) / 25, 0, 1)
    compression = clamp(1 - comp, 0, 1)

    # 趋势 = 方向效率 + 趋势强度（关键：不依赖 MA 斜率，否则“慢涨震荡”会被误判为趋势）
    trend = 0.5 * ER_score + 0.5 * ADX_score
    rng = 0.5 * (1 - ER_score) + 0.25 * ADX_low + 0.25 * compression
    decay = clamp(
        0.5 * (ER_score - clamp(erPrev / 0.4, 0, 1)) +
        0.5 * (ADX_score - clamp(adxPrev / 25, 0, 1)), 0, 1)
    ambiguity = clamp(1 - max(trend, rng), 0, 1)
    transition = clamp(max(decay, ambiguity * 0.8), 0, 1)
    return {"trend": trend, "range": rng, "transition": transition}


def exhaustionAt(i, ctx):
    candles = ctx["candles"]
    start = max(0, i - 30)
    win = candles[start: i + 1]
    hi = max(c["h"] for c in win); lo = min(c["l"] for c in win)
    tests = 0
    for k in range(start, i + 1):
        if candles[k]["h"] >= hi * 0.997 or candles[k]["l"] <= lo * 1.003:
            tests += 1
    sTests = 25 * clamp(tests / 5, 0, 1)
    ar = ctx["atrRatio"][i] or 1
    sATR = 20 * clamp((ar - 1) / 0.4, 0, 1)
    vr = (ctx["volRatio"][i] or 1) - 1
    sVol = 15 * clamp(vr / 0.5, 0, 1)
    r20 = ctx["range20"][i] or 0
    r50 = ctx["range50"][i] or 1
    rr = r20 / r50 if r50 > 0 else 1
    sRange = 10 * clamp((rr - 1) / 0.3, 0, 1)
    s = ctx["ma30Slope"][i] or 0
    sMA = 15 * clamp((abs(s) - 0.2) / 0.4, 0, 1)
    erNow = ctx["er"][i] or 0.3
    erPrev = ctx["er"][max(MA_LOOK, i - MA_LOOK)] or erNow
    erAccel = clamp((erNow - erPrev) / 0.2, 0, 1)
    sER = 15 * erAccel
    return sTests + sATR + sVol + sRange + sMA + sER


def breakoutUp(i, ctx, resistance, st):
    return bool(resistance) and ctx["candles"][i]["c"] > resistance["high"] and \
        (ctx["volRatio"][i] or 0) > 1.5 and (ctx["atrRatio"][i] or 0) > 1.2 and \
        st["trend"][i] == 1


def breakoutDown(i, ctx, support, st):
    return bool(support) and ctx["candles"][i]["c"] < support["low"] and \
        (ctx["volRatio"][i] or 0) > 1.5 and (ctx["atrRatio"][i] or 0) > 1.2 and \
        st["trend"][i] == -1


def classifyRangeState(i, ctx):
    """在 range 分支内，按方向把震荡细分为：
       uptrend_consol    上涨整理（回踩低吸，禁止高抛）
       downtrend_consol  下跌整理（反弹做空，禁止低吸）
       neutral_range     真正横盘（高抛低吸）
    """
    slope = ctx["ma30Slope"][i] if (ctx.get("ma30Slope") and ctx["ma30Slope"][i] is not None) else 0.0
    drift = ctx["drift"][i] if (ctx.get("drift") and ctx["drift"][i] is not None) else 0.0
    if slope > RANGE_UP_SLOPE or drift > RANGE_UP_DRIFT:
        return "uptrend_consol"
    if slope < RANGE_DOWN_SLOPE or drift < RANGE_DOWN_DRIFT:
        return "downtrend_consol"
    return "neutral_range"


def stateAt(i, env):
    sc = env["scores"][i] or {"trend": 0, "range": 0}
    tp = sc["trend"]; rp = sc["range"]
    ex = env["exhaustion"][i] or 0
    a = env["adx"][i] or 0
    if ex >= 70 and (breakoutUp(i, env, env["resistance"], env["st"]) or
                     breakoutDown(i, env, env["support"], env["st"])):
        return "breakout"
    if tp >= 0.5 and a >= 20:
        return "trend"
    if ex > 50:
        return "transition"
    # 非趋势、非过渡/突破 -> 一律按“震荡/整理”处理，由 slope/drift 细分为三类
    return classifyRangeState(i, env)


# ─────────────────────────────────────────────────────────────
#  Market Permission Layer (V3: 取代原 No Trade Zone)
#  核心思想：不是“禁止交易”，而是“决定哪些策略可运行、权重如何、入场门槛多高”。
#  三 Agent 权限矩阵（按 4h 桶的 A/B/C/D 条件组合）：
#    条件        Trend Agent        Range Agent          Breakout Agent
#    A概率接近    降权(weight↓)      允许                允许
#    B弱ADX      禁止               允许                允许(低ADX反是优势)
#    C低波动      禁止(只禁趋势)     允许                等待(不新开, 等扩张)
#    D老化       降权               禁止追趋势(只让离场) 允许突破
#  另加两个结构性风险过滤（不依赖桶，依赖价格结构）：
#    Consolidation Break Risk (CBR): 整理态逆势单前检查高低点结构, 防轧空/轧多
#    Trend Noise Filter (TNF): 趋势态但近 1h 实际位移不足 -> 视为噪音, 不追
#  Market Permission Score: Trend Score<60 关闭 Trend Agent; Breakout 单独计分
# ─────────────────────────────────────────────────────────────
NT_AMBIG_GAP   = 0.15   # A: 最大概率 - 第二概率 < 此值
NT_ADX_MIN     = 20      # B: ADX < 此值(趋势强度不足)
NT_ATR_PCT_MIN = 0.008   # C: 4h ATR/Close < 此值(波动太低)
NT_AGING_BARS  = 30      # D: 连续 trend 4h 桶数阈值
NT_AGING_LOOK  = 10      # D: ADX 回落对比回看桶数
TREND_MIN_HOLD      = 2  # 趋势微动过滤: 持仓不足此根 1h 忽略 flip
TREND_MIN_MOVE_ATR  = 0.8  # 趋势微动过滤(基础): 净位移 < 此值*ATR 视为噪音
TREND_DOWN_MOVE_ATR = 1.4  # 趋势降权(A/D)时更严: 净位移需 > 此值*ATR 才追
CBR_LOOK      = 20       # 整理突破风险回看 4h 桶数
CBR_HL_MIN    = 2        # 低点抬升 >= 此数 -> 禁止逆势空(轧空风险)
CBR_LH_MIN    = 2        # 高点下移 >= 此数 -> 禁止逆势多(轧多风险)
TNF_LOOK_1H   = 10       # 趋势噪声回看 1h 根数
TREND_FLIP_CLUSTER = 3     # 近 TNF_LOOK_1H 根内 ST 翻转 >= 此数 -> 锯齿(噪音); 2 次=正常反转对(如 #83→#84)不误杀
TREND_SCORE_MIN = 60      # Trend Score 参考阈值(满分100)
TREND_SCORE_GATE = False   # v1: 评分为参考, 不做硬关(只过滤C + CBR + TNF, 见设计); 置True则<阈值关Trend
STRUCTURE_BIAS = True     # 下跌整理结构偏置(旧, 保留供对比; 现并入 CBR)
MARKET_PERMISSION_ENABLED = True  # 总开关: False 时所有 Agent 全允许(用于还原原始信号做对比)


def marketConditionsAt(j, ctx, candles4, consec_trend):
    """返回该 4h 桶触发的 A/B/C/D 条件（True/False）。"""
    sc = ctx["scores"][j] or {"trend": 0, "range": 0, "transition": 0}
    probs = sorted([sc["trend"], sc["range"], sc["transition"]], reverse=True)
    cond = {"A": False, "B": False, "C": False, "D": False}
    if probs[0] - probs[1] < NT_AMBIG_GAP:
        cond["A"] = True
    adx = ctx["adx"][j] or 0
    atr = ctx["atr"][j] or 0
    close = candles4[j]["c"] or 1
    if adx < NT_ADX_MIN:
        cond["B"] = True
    if close > 0 and atr / close < NT_ATR_PCT_MIN:
        cond["C"] = True
    if consec_trend >= NT_AGING_BARS and j >= NT_AGING_LOOK and \
            (ctx["adx"][j] or 0) < (ctx["adx"][j - NT_AGING_LOOK] or 0):
        cond["D"] = True
    return cond


def marketPermissionScoreTrend(j, ctx, candles4, cond, consec_trend):
    """Trend Permission Score (满分100)：ADX强度30 + ER25 + ATR环境20 + 趋势年龄15 + 概率差10。
    < TREND_SCORE_MIN 时关闭 Trend Agent（即使无 B/C 硬禁）。"""
    adx = ctx["adx"][j] or 0
    er = ctx["er"][j] if ctx["er"][j] is not None else 0.5
    atr = ctx["atr"][j] or 0
    close = candles4[j]["c"] or 1
    atr_pct = atr / close if close > 0 else 0.02
    sc = ctx["scores"][j] or {"trend": 0, "range": 0, "transition": 0}
    probs = sorted([sc["trend"], sc["range"], sc["transition"]], reverse=True)
    prob_gap = probs[0] - probs[1]
    s_adx = clamp(adx / 40, 0, 1) * 30
    s_er = clamp(er / 0.4, 0, 1) * 25
    s_atr = clamp(atr_pct / 0.02, 0, 1) * 20
    # 趋势年龄：越老分数越低（D 触发=0；否则随连续桶数线性衰减，年轻=满分）
    if cond["D"]:
        s_age = 0.0
    else:
        s_age = 15 * (1 - clamp((consec_trend - NT_AGING_BARS / 2) / NT_AGING_BARS, 0, 1))
    s_prob = clamp(prob_gap / 0.15, 0, 1) * 10
    return round(s_adx + s_er + s_atr + s_age + s_prob, 1)


def marketPermissionScoreBreakout(j, ctx, cond):
    """Breakout Permission Score (满分100)：低ADX压缩末端反是优势。
    ATR扩张40 + 量扩张30 + (1-ADX)压缩30。该分独立，不与 Trend 互斥。"""
    atrRatio = ctx.get("atrRatio")
    volRatio = ctx.get("volRatio")
    adx = ctx["adx"][j] or 0
    ar = (atrRatio[j] if atrRatio and atrRatio[j] is not None else 1) or 1
    vr = (volRatio[j] if volRatio and volRatio[j] is not None else 1) or 1
    s_atr = clamp((ar - 1) / 0.4, 0, 1) * 40
    s_vol = clamp((vr - 1) / 0.5, 0, 1) * 30
    s_comp = clamp((25 - adx) / 25, 0, 1) * 30
    return round(s_atr + s_vol + s_comp, 1)


def permissionMatrixAt(j, ctx, candles4, consec_trend, state=None):
    """返回该 4h 桶的三 Agent 权限矩阵 + 评分。
      trend/range: {allow, weight, down, no_chase}
      breakout:    {allow, wait, weight}
    语义：allow=False=该 Agent 不新开仓(已持仓顺势顺延); down=降权(抬高入场门槛);
          weight 用于下游缩放(如仓位/确认强度); wait=突破暂不新开(等波动扩张)。
    """
    if not MARKET_PERMISSION_ENABLED:
        # 总开关关闭：所有 Agent 全允许(用于还原原始信号做对比);
        # Trend Agent 内对应跳过 move/TNF 过滤, Range 跳过 CBR
        return {"trend": {"allow": True, "weight": 1.0, "down": False},
                "range": {"allow": True, "weight": 1.0, "down": False, "no_chase": False},
                "breakout": {"allow": True, "wait": False, "weight": 1.0},
                "cond": {"A": False, "B": False, "C": False, "D": False},
                "reason": "disabled", "trend_score": 0.0, "breakout_score": 0.0}
    cond = marketConditionsAt(j, ctx, candles4, consec_trend)
    trend = {"allow": True, "weight": 1.0, "down": False}
    rng = {"allow": True, "weight": 1.0, "down": False, "no_chase": False}
    brk = {"allow": True, "wait": False, "weight": 1.0}
    reasons = []
    # A 概率接近 -> 趋势降权(不禁止); 区间/突破允许
    if cond["A"]:
        trend["down"] = True
        trend["weight"] = 0.6
        reasons.append("A概率接近→Trend降权")
    # B 弱ADX -> 禁止趋势跟随; 区间/突破允许(低ADX对突破反是优势)
    if cond["B"]:
        trend["allow"] = False
        reasons.append("B弱ADX→Trend禁止")
    # C 低波动 -> 关键: 不直接硬禁"趋势态已确认"的趋势(否则会误杀压缩末端的突破, 如 #84 +8.06%)。
    #   非趋势态(横盘/整理/过渡)下无量无方向 -> 硬禁开趋势;
    #   趋势态已确认(ST 已翻出方向) -> 仅降权(抬高入场门槛, 让 Breakout Agent 接管扩张)。
    #   Range 仍允许(用户: 只禁趋势策略); Breakout 等待(等波动扩张再介入)。
    if cond["C"]:
        brk["wait"] = True
        if state != "trend":
            trend["allow"] = False
            reasons.append("C低波动→Trend禁止(非趋势态)")
        else:
            trend["down"] = True
            trend["weight"] = min(trend["weight"], 0.6)
            reasons.append("C低波动→Trend降权(已确认趋势)")
    # D 老化 -> 趋势降权; 区间禁止追趋势(只让离场); 突破允许
    if cond["D"]:
        trend["down"] = True
        trend["weight"] = min(trend["weight"], 0.6)
        rng["no_chase"] = True
        reasons.append("D老化→Trend降权/Range禁追趋势")
    tscore = marketPermissionScoreTrend(j, ctx, candles4, cond, consec_trend)
    bscore = marketPermissionScoreBreakout(j, ctx, cond)
    if TREND_SCORE_GATE and tscore < TREND_SCORE_MIN:
        # 评分硬关(默认关闭): v1 只依赖 C + CBR + TNF 做硬过滤, A/D 仅降权不关
        trend["allow"] = False
        reasons.append(f"TrendScore={tscore}<{TREND_SCORE_MIN}→关闭Trend")
    return {
        "trend": trend, "range": rng, "breakout": brk,
        "cond": cond, "reason": (";".join(reasons) if reasons else "ok"),
        "trend_score": tscore, "breakout_score": bscore,
    }


def consolidationBreakRisk(state, j, candles4, st):
    """整理态逆势单前的结构性风险 (Consolidation Break Risk)。
    下跌整理做空前: 近 CBR_LOOK 根 4h 低点抬升(更高低) >= CBR_HL_MIN -> 禁空(轧空)
    上涨整理低吸前: 近 CBR_LOOK 根 4h 高点下移(更低高) >= CBR_LH_MIN -> 禁多(轧多)
    返回 (被禁方向 'buy'/'sell'/None, 说明)。用于治 #27/#42/#59。
    """
    if state not in ("downtrend_consol", "uptrend_consol"):
        return (None, "")
    lo = max(0, j - CBR_LOOK + 1)
    seg = candles4[lo:j + 1]
    if len(seg) < 3:
        return (None, "")
    if state == "downtrend_consol":
        hl = sum(1 for k in range(1, len(seg)) if seg[k]["l"] > seg[k - 1]["l"])
        if hl >= CBR_HL_MIN:
            return ("sell", f"下跌整理近{CBR_LOOK}根低点抬升{hl}次→禁空(轧空风险)")
    else:  # uptrend_consol
        lh = sum(1 for k in range(1, len(seg)) if seg[k]["h"] < seg[k - 1]["h"])
        if lh >= CBR_LH_MIN:
            return ("buy", f"上涨整理近{CBR_LOOK}根高点下移{lh}次→禁多(轧多风险)")
    return (None, "")


def trendWhipsawAt(q, st1, look, cluster):
    """Trend Whipsaw Filter：近 look 根 1h 内 SuperTrend 翻转次数 >= cluster -> 锯齿噪音(True)。
    用于治 #31-34/#52-55(连续小幅翻转的震荡), 且不误杀单次趋势起点(如 #43/#84/#92 仅 1 次翻转)。"""
    if q < 1:
        return False
    lo = max(0, q - look + 1)
    cnt = sum(1 for k in range(lo, q + 1) if st1["flip"][k])
    return cnt >= cluster


def computeRegimeContext(candles):
    n = len(candles)
    close = [x["c"] for x in candles]
    er = efficiencyRatio(candles)
    atr = wilderATR(candles)
    ma30 = sma(close, MA_P)
    adx = computeADX(candles)
    st = computeSuperTrend(candles, ST_FACTOR, atr, ATR_P)
    ma30Slope = [None] * n
    for i in range(MA_P, n):
        a = ma30[i]; b = ma30[i - MA_LOOK]
        ma30Slope[i] = (a - b) / atr[i] if (a is not None and b is not None and atr[i]) else None
    range20 = [None] * n; range50 = [None] * n
    for i in range(50, n):
        range20[i] = rangeOf(candles, i, 20)
        range50[i] = rangeOf(candles, i, 50)
    # 价格净漂移（ATR 归一化，DRIFT_K 窗口）：>0 上行 / <0 下行 / ≈0 横盘
    drift = [None] * n
    for i in range(DRIFT_K, n):
        a = atr[i] or 1
        drift[i] = (close[i] - close[i - DRIFT_K]) / a if atr[i] else None
    volSma = sma([c["vol"] for c in candles], 20)
    volRatio = [None] * n
    for i in range(20, n):
        if volSma[i]:
            volRatio[i] = candles[i]["vol"] / volSma[i]
    atrSma = sma([v or 0 for v in atr], 20)
    atrRatio = [None] * n
    for i in range(20, n):
        if atrSma[i]:
            atrRatio[i] = atr[i] / atrSma[i]
    scores = [None] * n; exhaustion = [None] * n
    sCtx = {"er": er, "adx": adx, "ma30Slope": ma30Slope, "range20": range20, "range50": range50, "drift": drift}
    eCtx = {"candles": candles, "atrRatio": atrRatio, "volRatio": volRatio,
            "ma30Slope": ma30Slope, "range20": range20, "range50": range50, "er": er}
    for i in range(WARMUP, n):
        scores[i] = regimeScoresAt(i, sCtx)
        exhaustion[i] = exhaustionAt(i, eCtx)
    return {
        "candles": candles, "er": er, "adx": adx, "atr": atr, "st": st,
        "ma30Slope": ma30Slope, "range20": range20, "range50": range50, "drift": drift,
        "volRatio": volRatio, "atrRatio": atrRatio, "scores": scores, "exhaustion": exhaustion,
    }


def computeRegimeSignals(candles):
    """精确复刻 /model 页面 computeRegime 的 result.signals（升级到 6 状态）：
      trend            -> 不发信号（由 SuperTrend Trend Agent 接管）
      neutral_range    -> 高抛低吸（支撑买 / 压力卖）
      uptrend_consol   -> 只回踩低吸（支撑买；禁止高抛）；支撑失守平多
      downtrend_consol -> 只反弹做空（压力卖；禁止低吸）；压力失守平空
      transition/breakout -> 放量+ATR扩张+ST同向 突破（顺势介入）
    {'action','i','state','reason'} 与原 backtest_st.backtest 兼容。
    side 跟踪用于抑制“同向自翻转”（避免已持多又发 buy 实现亏损）。
    """
    ctx = computeRegimeContext(candles)
    n = len(candles)
    signals = []
    lastBuy = -10 ** 9; lastSell = -10 ** 9
    COOL = 8
    side = 0
    for i in range(WARMUP, n):
        resistance, support = zonesAt(candles, i, ctx["atr"], win=400)  # 对齐 JS slice(-400)
        env = {**ctx, "resistance": resistance, "support": support}
        stt = stateAt(i, env)
        cl = candles[i]
        ex_i = ctx["exhaustion"][i] or 0

        if stt in ("neutral_range", "uptrend_consol", "downtrend_consol"):
            if ex_i < 50 and support and resistance:
                sSpan = support["high"] - support["low"]
                posS = (cl["c"] - support["low"]) / sSpan if sSpan > 0 else 0
                rSpan = resistance["high"] - resistance["low"]
                posR = (cl["c"] - resistance["low"]) / rSpan if rSpan > 0 else 0
                if stt == "neutral_range":
                    # 真正横盘：双边均值回归
                    if posS < 0.2 and support["strength"] > 60 and cl["c"] > cl["o"] \
                            and i - lastBuy > COOL and side != 1:
                        signals.append({"action": "buy", "i": i, "state": "neutral_range",
                                        "reason": "横盘-支撑反弹", "price": cl["l"]})
                        lastBuy = i; side = 1
                    if posR > 0.8 and resistance["strength"] > 60 and cl["c"] < cl["o"] \
                            and i - lastSell > COOL and side != -1:
                        signals.append({"action": "sell", "i": i, "state": "neutral_range",
                                        "reason": "横盘-压力回落", "price": cl["h"]})
                        lastSell = i; side = -1
                elif stt == "uptrend_consol":
                    # 上涨整理：只低吸，禁止高抛；支撑失守则平多（不裸空，等趋势态）
                    if posS < 0.25 and support["strength"] > 55 and cl["c"] > cl["o"] \
                            and i - lastBuy > COOL and side != 1:
                        signals.append({"action": "buy", "i": i, "state": "uptrend_consol",
                                        "reason": "上涨整理-回踩低吸", "price": cl["l"]})
                        lastBuy = i; side = 1
                    elif side == 1 and cl["c"] < support["low"] and i - lastSell > COOL:
                        signals.append({"action": "sell", "i": i, "state": "uptrend_consol",
                                        "reason": "上涨整理-支撑失守离场", "price": cl["c"]})
                        lastSell = i; side = -1
                elif stt == "downtrend_consol":
                    # 下跌整理：只做空，禁止低吸；压力失守则平空（不裸多，等趋势态）
                    if posR > 0.75 and resistance["strength"] > 55 and cl["c"] < cl["o"] \
                            and i - lastSell > COOL and side != -1:
                        signals.append({"action": "sell", "i": i, "state": "downtrend_consol",
                                        "reason": "下跌整理-反弹做空", "price": cl["h"]})
                        lastSell = i; side = -1
                    elif side == -1 and cl["c"] > resistance["high"] and i - lastBuy > COOL:
                        signals.append({"action": "buy", "i": i, "state": "downtrend_consol",
                                        "reason": "下跌整理-压力失守离场", "price": cl["c"]})
                        lastBuy = i; side = 1
        elif stt in ("transition", "breakout"):
            if ex_i >= 70:
                if breakoutUp(i, env, resistance, ctx["st"]) and i - lastBuy > COOL and side != 1:
                    signals.append({"action": "buy", "i": i, "state": stt,
                                    "reason": "突破压力区", "price": cl["c"]})
                    lastBuy = i; side = 1
                if breakoutDown(i, env, support, ctx["st"]) and i - lastSell > COOL and side != -1:
                    signals.append({"action": "sell", "i": i, "state": stt,
                                    "reason": "跌破支撑区", "price": cl["c"]})
                    lastSell = i; side = -1
    return signals


def computeRegimeSignalsMTF(k4h, k1h, cool_h=24, strength_min=60, return_nt=False):
    """多周期版（严格对齐用户模型 Market Regime）：
    4h 计算 S/R 支撑压力 + 市场状态；1h 触发信号。
    状态决定策略权限：
      - Trend   -> SuperTrend Agent：4h SuperTrend 顺势（进入 trend 态 或 ST flip 时，
                   在下一 4h 桶首根 1h 执行）
      - Range   -> Range Agent：1h 价格走到 4h 支撑(low<=sup.high 且收阳)=开多平空；
                   走到 4h 压力(high>=res.low 且收阴)=开空平多（高抛低吸）
      - Transition/Breakout -> Breakout Agent：4h 突破确认(放量+ATR扩张+ST同向)，
                   在下一 4h 桶首根 1h 顺势介入；突破后 SuperTrend 重新接管(回到 Trend)
    返回 1h 索引空间的信号序列，直接喂给 backtest_st.backtest(k1h, sigs)。
    """
    ctx = computeRegimeContext(k4h)
    m = len(k4h)
    st = ctx["st"]
    res4 = [None] * m; sup4 = [None] * m; state4 = [None] * m; ex4 = [None] * m
    for j in range(WARMUP, m):
        r, s = zonesAt(k4h, j, ctx["atr"], win=400)
        env = {**ctx, "resistance": r, "support": s}
        res4[j] = r; sup4[j] = s
        state4[j] = stateAt(j, env)
        ex4[j] = ctx["exhaustion"][j] or 0

    # Market Permission Layer：逐 4h 桶计算三 Agent 权限矩阵 + 评分
    #   (取代原 No Trade Zone：不再“全市场禁”，而是“决定哪些策略可运行/权重”)
    perm4 = [None for _ in range(m)]
    consec_trend = 0
    for j in range(WARMUP, m):
        if state4[j] == "trend":
            consec_trend += 1
        else:
            consec_trend = 0
        perm4[j] = permissionMatrixAt(j, ctx, k4h, consec_trend, state4[j])

    ts4 = [x["ts"] for x in k4h]

    def bucket(t):
        lo, hi, ans = 0, m - 1, 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if ts4[mid] <= t:
                ans = mid; lo = mid + 1
            else:
                hi = mid - 1
        return ans

    p = len(k1h)
    first1h = [None] * m
    for q in range(p):
        j = bucket(k1h[q]["ts"])
        if first1h[j] is None:
            first1h[j] = q

    # 1h SuperTrend（及时执行），用于 Trend Agent 的门控触发
    atr1 = wilderATR(k1h)
    st1 = computeSuperTrend(k1h, ST_FACTOR, atr1, ATR_P)

    cands = []
    # 1) Trend Agent：1h SuperTrend 顺势，仅当所属 4h 桶为 trend 态时执行
    #    （4h 决定“是否允许顺势”，1h 决定“何时进场”，避免 4h 延后执行的滞后）
    #    Market Permission Layer：
    #      - 权限矩阵 allow=False(B弱ADX/C低波动/TrendScore过低) -> 不生成任何 flip(关 Trend Agent)
    #      - down(A概率接近/D老化) -> 抬高入场门槛(需更大 ATR 位移才追)
    #      - Trend Noise Filter: 近 10 根 1h 实际位移不足 -> 不追(治 #31-34/#52-55)
    last_trend_q = -10 ** 9
    last_trend_price = k1h[0]["c"] if p else 0.0
    for q in range(p):
        j = bucket(k1h[q]["ts"])
        if j < WARMUP or state4[j] != "trend" or not st1["flip"][q]:
            continue
        perm = perm4[j]
        t = perm["trend"]
        if not t["allow"]:
            # 趋势被禁用(B弱ADX / C低波动 / TrendScore过低): 不新开也不翻转, 关闭 Trend Agent
            continue
        c = k1h[q]["c"]
        if MARKET_PERMISSION_ENABLED:
            # 同向自翻转/微动过滤(基础门槛, 降权态不额外加严——降权仅作仓位权重, 不硬挡大趋势)
            if q - last_trend_q < TREND_MIN_HOLD:
                continue
            if abs(c - last_trend_price) < TREND_MIN_MOVE_ATR * (atr1[q] or 1):
                continue
            # 趋势噪声过滤：近 TNF_LOOK_1H 根 1h 内 ST 翻转簇(>=3 次=锯齿) -> 不追(治 #31-34)
            if trendWhipsawAt(q, st1, TNF_LOOK_1H, TREND_FLIP_CLUSTER):
                continue
        action = "buy" if st1["trend"][q] == 1 else "sell"
        cands.append((q, action, "trend", "Trend:SuperTrend顺势", c))
        last_trend_q = q; last_trend_price = c
    # 2) Transition/Breakout Agent：4h 突破确认(放量+ATR扩张+ST同向)，下一 4h 桶首根 1h 顺势介入
    for j in range(WARMUP, m):
        if state4[j] in ("transition", "breakout") and ex4[j] >= 70:
            # C 低波动 -> Breakout 等待(不新开, 等波动扩张); 其余允许(低ADX反是突破优势)
            if perm4[j]["breakout"]["wait"]:
                continue
            env = {**ctx, "resistance": res4[j], "support": sup4[j]}
            qe = first1h[j + 1] if j + 1 < m else None
            if qe is not None:
                if breakoutUp(j, env, res4[j], st):
                    cands.append((qe, "buy", "breakout", "突破压力区", k1h[qe]["c"]))
                elif breakoutDown(j, env, sup4[j], st):
                    cands.append((qe, "sell", "breakout", "跌破支撑区", k1h[qe]["c"]))
    # 3) Range Agent：1h 触 4h 支撑/压力；按 4h 震荡子状态决定策略权限
    #    neutral_range    -> 高抛低吸（支撑买 / 压力卖）
    #    uptrend_consol   -> 只回踩低吸（禁止高抛）；支撑失守平多
    #    downtrend_consol -> 只反弹做空（禁止低吸）；压力失守平空
    #    Market Permission Layer: 区间 allow=False(C低波动) -> 整段跳过;
    #      no_chase(D老化) -> 禁止追趋势的新开, 只让支撑/压力失守离场
    #    Consolidation Break Risk: 整理逆势单前查高低点结构, 防轧空/轧多(治 #27/#42/#59)
    for q in range(p):
        j = bucket(k1h[q]["ts"])
        if j < WARMUP:
            continue
        stt = state4[j]; ex = ex4[j]; sup = sup4[j]; res = res4[j]
        cl = k1h[q]
        if stt not in ("neutral_range", "uptrend_consol", "downtrend_consol"):
            continue
        if ex >= 50 or not sup or not res:
            continue
        perm = perm4[j]["range"]
        if not perm["allow"]:
            continue
        cbr_dir, _ = consolidationBreakRisk(stt, j, k4h, st) if MARKET_PERMISSION_ENABLED else (None, "")
        if stt == "neutral_range":
            if cl["l"] <= sup["high"] and sup["strength"] >= strength_min and cl["c"] > cl["o"]:
                cands.append((q, "buy", "neutral_range", "4h横盘-支撑反弹", cl["c"]))
            if cl["h"] >= res["low"] and res["strength"] >= strength_min and cl["c"] < cl["o"]:
                cands.append((q, "sell", "neutral_range", "4h横盘-压力回落", cl["c"]))
        elif stt == "uptrend_consol":
            if perm["no_chase"]:
                # 老化: 禁止追趋势(回踩低吸是逆小回撤, 不追), 但允许支撑失守离场
                if cl["c"] < sup["low"]:
                    cands.append((q, "sell", "uptrend_consol", "4h上涨整理-支撑失守", cl["c"]))
            else:
                if cl["l"] <= sup["high"] and sup["strength"] >= strength_min and cl["c"] > cl["o"] \
                        and cbr_dir != "buy":
                    cands.append((q, "buy", "uptrend_consol", "4h上涨整理-回踩低吸", cl["c"]))
                elif cl["c"] < sup["low"]:
                    cands.append((q, "sell", "uptrend_consol", "4h上涨整理-支撑失守", cl["c"]))
        elif stt == "downtrend_consol":
            # 结构偏置(旧, 并入 CBR): 本 4h 桶 SuperTrend 已转多 -> 禁止裸空
            st4_up = STRUCTURE_BIAS and st["trend"][j] == 1
            if perm["no_chase"]:
                # 老化: 禁止追趋势(反弹做空), 只让压力失守离场
                if cl["c"] > res["high"]:
                    cands.append((q, "buy", "downtrend_consol", "4h下跌整理-压力失守", cl["c"]))
            else:
                if not st4_up and cbr_dir != "sell" and \
                        cl["h"] >= res["low"] and res["strength"] >= strength_min and cl["c"] < cl["o"]:
                    cands.append((q, "sell", "downtrend_consol", "4h下跌整理-反弹做空", cl["c"]))
                elif cl["c"] > res["high"]:
                    cands.append((q, "buy", "downtrend_consol", "4h下跌整理-压力失守", cl["c"]))

    # Trend Agent 每次 1h ST flip 都跟（无 cooldown，保证及时反转）；
    # Range/Breakout 用 cooldown 防止同态内信号扎堆
    trend_cands = [(i, a, s, r, p) for (i, a, s, r, p) in cands if s == "trend"]
    other = [(i, a, s, r, p) for (i, a, s, r, p) in cands if s != "trend"]
    other.sort(key=lambda x: x[0])
    sigs = []
    lastBuy = lastSell = -10 ** 9
    for (i, action, state, reason, price) in other:
        if action == "buy" and i - lastBuy > cool_h:
            sigs.append({"action": "buy", "i": i, "state": state, "reason": reason, "price": price})
            lastBuy = i
        elif action == "sell" and i - lastSell > cool_h:
            sigs.append({"action": "sell", "i": i, "state": state, "reason": reason, "price": price})
            lastSell = i
    for (i, action, state, reason, price) in sorted(trend_cands):
        sigs.append({"action": action, "i": i, "state": state, "reason": reason, "price": price})
    # 同向自翻转抑制：已持多不再发 buy，已持空不再发 sell（避免实现亏损的重复开仓）
    # Market Permission Layer 语义：候选已在各 Agent 内按权限矩阵/结构风险过滤(不生成=不开仓),
    #   这里只做全局同向去重与最终装配。不再有“持仓时强制翻转”的特殊分支——
    #   因为禁用只作用于“新开/追单”，持仓退出由 Range 失守/Breakout 接管逻辑负责。
    final = []
    sside = 0
    for sig in sorted(sigs, key=lambda x: x["i"]):
        if sig["action"] == "buy" and sside == 1:
            continue
        if sig["action"] == "sell" and sside == -1:
            continue
        final.append(sig)
        sside = 1 if sig["action"] == "buy" else -1
    if return_nt:
        return final, perm4, state4
    return final
