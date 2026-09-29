# -*- coding: utf-8 -*-
"""在 5.7 年 BTC 1h 历史（本地 btc_1h_full.json，已扩展到 2021-01-01）上，对
每一个 SuperTrend 翻转信号（ST period=10, mult=3.0 —— 即 V3 / 策略学习页用的那套）
做统计与「大波动学习」。

产出：
  1) 每个信号的「未来 20 根」 outcome：
        max_up      : 未来 20 根内相对入场价的最大涨幅（多头最有利）
        max_down    : 未来 20 根内相对入场价的最大跌幅（多头最不利）
        max_profit  : 信号方向上的最大有利偏移（多=max_up，空=-max_down）
        max_adverse : 信号方向上的最大不利偏移
        big_move    : 未来 20 根出现 |波动| >= 5.2% 的摆动
        big_up      : 出现 >= +5.2% 的上涨摆动
        big_down    : 出现 <= -5.2% 的下跌摆动
        big_fav     : 信号方向上有 >= 5.2% 的有利摆动
  2) 11 大类、20 个核心特征（见文件底部 FEATURES 列表），全部在信号 bar 处计算，
     用于喂后续机器学习 / 决策树。
  3) 学习分析：用纯 Python 计算「哪些条件产生大波动」——
        - 每个数值特征的与 big_move 的点二列相关系数
        - 十分位数分桶后的大波动命中率 + 提升度
        - 单特征最优切分（决策树桩）命中率
        - 类别特征（交易时段 / 市场状态）分组命中率
     结果排序后写入 JSON + 打印。

数据：本地缓存，不联网。4h 用 btc_4h_full.json 的 h4（2022-07 起，更早的用 1h 近似不影响特征）。
"""
import sys, os, json, csv, bisect, math, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from indicators import super_trend, ta_adx, ta_sma

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BTC1H = os.path.join(BASE, "backtest", "btc_1h_full.json")
BTC4H = os.path.join(BASE, "backtest", "btc_4h_full.json")
OUT_CSV = os.path.join(BASE, "backtest", "st_signals_full.csv")
OUT_JSON = os.path.join(BASE, "backtest", "st_bigmove_learn.json")

ST_PERIODS, ST_MULT = 10, 3.0
FWD = 20                      # 未来 20 根
BIG = 0.052                   # 大波动阈值 5.2%
MIN_I = 100                   # 需要前 100 根窗口算特征

# ── 加载数据 ─────────────────────────────────────────────────────
def load():
    base = json.load(open(BTC1H, encoding="utf-8"))["base"]
    h4 = json.load(open(BTC4H, encoding="utf-8"))["h4"]
    return base, h4

# ── 预计算全序列指标 ─────────────────────────────────────────────
def build_series(base):
    o = [b["o"] for b in base]; h = [b["h"] for b in base]
    l = [b["l"] for b in base]; c = [b["c"] for b in base]
    v = [b["vol"] for b in base]; ts = [b["ts"] for b in base]
    n = len(c)
    st = super_trend(o, h, l, c, periods=ST_PERIODS, multiplier=ST_MULT, change_atr=True)
    trend = st["trend"]; atr = st["atr"]; up = st["up"]; dn = st["dn"]
    flips = sorted(f["i"] for f in st["flips"])
    adx = ta_adx(h, l, c, 14)
    atr_pct = [(atr[i] / c[i] * 100.0 if (atr[i] and c[i]) else 0.0) for i in range(n)]

    cum = [0.0] * (n + 1)
    for i in range(1, n):
        cum[i] = cum[i - 1] + abs(c[i] - c[i - 1])
    er20 = [0.0] * n
    for i in range(20, n):
        den = cum[i] - cum[i - 20]
        er20[i] = abs(c[i] - c[i - 20]) / den if den > 0 else 0.0

    st_dist = [0.0] * n
    for i in range(n):
        if trend[i] is None or atr[i] in (None, 0):
            continue
        line = up[i] if trend[i] == 1 else dn[i]
        st_dist[i] = (c[i] - line) / atr[i]

    ma10 = ta_sma(c, 10); ma30 = ta_sma(c, 30); ma60 = ta_sma(c, 60)
    vol_ma5 = ta_sma(v, 5); vol_ma20 = ta_sma(v, 20)
    return dict(o=o, h=h, l=l, c=c, v=v, ts=ts, n=n, trend=trend, atr=atr,
                up=up, dn=dn, flips=flips, adx=adx, atr_pct=atr_pct,
                er20=er20, st_dist=st_dist, ma10=ma10, ma30=ma30, ma60=ma60,
                vol_ma5=vol_ma5, vol_ma20=vol_ma20)

# ── 4h 辅助 ─────────────────────────────────────────────────────
def h4_ctx(h4):
    c4 = [b["c"] for b in h4]; ts4 = [b["ts"] for b in h4]
    ma30_4h = ta_sma(c4, 30)
    return c4, ts4, ma30_4h

# ── 单信号特征 ───────────────────────────────────────────────────
def hour_session(ts_ms):
    hh = (ts_ms // 3_600_000) % 24
    if 0 <= hh < 8:   return "asia"
    if 8 <= hh < 16:  return "eu"
    return "us"

def pct_rank(value, window):
    if not window:
        return 0.5
    le = sum(1 for x in window if x <= value)
    return le / len(window)

def features_at(S, i, c4ts, c4ma30, j4):
    c, h, l, o, v = S["c"], S["h"], S["l"], S["o"], S["v"]
    atr = S["atr"]; adx = S["adx"]; er20 = S["er20"]; st_dist = S["st_dist"]
    ma10, ma30, ma60 = S["ma30"], S["ma30"], S["ma60"]
    vol_ma5, vol_ma20 = S["vol_ma5"], S["vol_ma20"]
    ai = atr[i] or 0.0
    if ai <= 0 or c[i] <= 0:
        return None
    price = c[i]

    # 3. 趋势强度 / 4. 波动 变化类需要 i-1..i-10
    def g(seq, k):
        return seq[k] if (0 <= k < len(seq) and seq[k] is not None) else None

    adx_i = adx[i] or 0.0
    adx_5 = g(adx, i - 5) or 0.0; adx_1 = g(adx, i - 1) or 0.0
    er_i = er20[i]; er_5 = g(er20, i - 5) or 0.0; er_10 = g(er20, i - 10) or 0.0
    ap_i = S["atr_pct"][i]; ap_5 = g(S["atr_pct"], i - 5) or 0.0; ap_10 = g(S["atr_pct"], i - 10) or 0.0

    # 5. ST 质量
    sd_i = st_dist[i]
    sd_chg = st_dist[i - 1] - st_dist[i - 20] if i >= 20 else 0.0
    # 连续同方向趋势根数
    t = S["trend"][i]; same_dir = 0; j = i - 1
    while j >= 0 and S["trend"][j] == t:
        same_dir += 1; j -= 1
    flip_20 = sum(1 for f in S["flips"] if i - 20 < f <= i)
    flip_50 = sum(1 for f in S["flips"] if i - 50 < f <= i)
    flip_100 = sum(1 for f in S["flips"] if i - 100 < f <= i)

    # 6. 动量
    mom5 = (c[i] - c[i - 5]) / ai if i >= 5 else 0.0
    ret_5 = (c[i] - c[i - 5]) / c[i - 5] * 100 if i >= 5 and c[i - 5] else 0.0
    ret_10 = (c[i] - c[i - 10]) / c[i - 10] * 100 if i >= 10 and c[i - 10] else 0.0
    ret_20 = (c[i] - c[i - 20]) / c[i - 20] * 100 if i >= 20 and c[i - 20] else 0.0
    momentum_change = ret_5 - ret_10

    # 7. K 线结构
    body = abs(c[i] - o[i])
    body_atr = body / ai
    upper_shadow = h[i] - max(o[i], c[i]); upper_shadow_ratio = upper_shadow / ai
    lower_shadow = min(o[i], c[i]) - l[i]; lower_shadow_ratio = lower_shadow / ai
    sd_candle = 0; k = i; upc = c[i] > o[i]
    while k >= 0 and (c[k] > o[k]) == upc:
        sd_candle += 1; k -= 1

    # 8. 突破结构
    hi20 = max(h[max(0, i - 19):i + 1]); lo20 = min(l[max(0, i - 19):i + 1])
    hi50 = max(h[max(0, i - 49):i + 1]); lo50 = min(l[max(0, i - 49):i + 1])
    dist_high20 = (hi20 - price) / price * 100
    dist_low20 = (price - lo20) / price * 100
    break_high20 = 1 if price >= hi20 else 0
    break_high50 = 1 if price >= hi50 else 0
    break_low20 = 1 if price <= lo20 else 0
    break_strength = (price - hi20) / ai   # 正=已突破 20 高

    # 9. 区间震荡
    range_width_20 = (hi20 - lo20) / price * 100
    range_width_50 = (hi50 - lo50) / price * 100
    range_position = (price - lo20) / (hi20 - lo20) if hi20 > lo20 else 0.5
    # 横盘时间：自最近一次 20 根区间突破以来的连续根数
    rd = 0
    for j in range(i - 1, max(-1, i - 51), -1):
        lo = min(l[max(0, j - 19):j]); hi = max(h[max(0, j - 19):j])
        if hi > lo and lo <= c[j] <= hi:
            rd += 1
        else:
            break

    # 10. 成交量
    vol_ma20_i = vol_ma20[i] or 0.0
    volume_ratio = v[i] / vol_ma20_i if vol_ma20_i > 0 else 1.0
    vol_ma5_i = vol_ma5[i] or 0.0
    volume_change_5 = (v[i] / vol_ma5_i - 1) * 100 if vol_ma5_i > 0 else 0.0

    # 1. 时间
    session = hour_session(S["ts"][i])

    # 2. 趋势环境
    m30 = ma30[i]; m10 = ma10[i]; m60 = ma60[i]
    ma30_slope_4h = ""
    if j4 >= 10 and c4ma30[j4] and c4ma30[j4 - 10]:
        ma30_slope_4h = (c4ma30[j4] - c4ma30[j4 - 10]) / c4ma30[j4 - 10] * 100
    close_ma30_distance_ATR = (price - m30) / ai if m30 else 0.0
    ma10_above_ma30 = 1 if (m10 and m30 and m10 > m30) else 0
    ma30_above_ma60 = 1 if (m30 and m60 and m30 > m60) else 0

    # 4. 波动
    atr_percent = ap_i
    atr_change_5 = ap_i - ap_5
    atr_change_10 = ap_i - ap_10
    atr_percentile_100 = pct_rank(ap_i, S["atr_pct"][max(0, i - 99):i + 1])

    # 11. 市场状态（规则标签）
    if adx_i < 20 and range_width_20 < 3.0:
        market_state = 0          # 震荡
    elif adx_i >= 25 and ma10_above_ma30 and ma30_above_ma60 and close_ma30_distance_ATR < 2.5:
        market_state = 1          # 趋势
    elif adx_i >= 20 and (ap_i - ap_10) > 0 and flip_20 <= 2 and same_dir <= 8:
        market_state = 2          # 启动
    elif close_ma30_distance_ATR > 3.0 and adx_i > 30:
        market_state = 3          # 过热
    else:
        market_state = 1 if adx_i >= 22 else 0

    return dict(
        # 1 时间
        session=session,
        # 2 趋势环境
        ma30_slope_4h=ma30_slope_4h,
        close_ma30_distance_ATR=round(close_ma30_distance_ATR, 4),
        ma10_above_ma30=ma10_above_ma30,
        ma30_above_ma60=ma30_above_ma60,
        # 3 趋势强度
        ADX14=round(adx_i, 3),
        ADX_change_5=round(adx_1 - adx_5, 3),
        ER20=round(er_i, 4),
        ER_change_5=round(er20[i] - er_5, 4),
        ER_change_10=round(er20[i] - er_10, 4),
        # 4 波动
        ATR_percent=round(atr_percent, 4),
        ATR_change_5=round(atr_change_5, 4),
        ATR_change_10=round(atr_change_10, 4),
        ATR_percentile_100=round(atr_percentile_100, 4),
        # 5 ST 质量
        st_distance_ATR=round(sd_i, 4),
        st_distance_change=round(sd_chg, 4),
        st_same_direction_count=same_dir,
        flip_20=flip_20, flip_50=flip_50, flip_100=flip_100,
        # 6 动量
        mom5=round(mom5, 4),
        return_5=round(ret_5, 4), return_10=round(ret_10, 4), return_20=round(ret_20, 4),
        momentum_change=round(momentum_change, 4),
        # 7 K线结构
        body_ATR=round(body_atr, 4),
        upper_shadow_ratio=round(upper_shadow_ratio, 4),
        lower_shadow_ratio=round(lower_shadow_ratio, 4),
        same_direction_candle_count=sd_candle,
        # 8 突破
        distance_high_20=round(dist_high20, 4), distance_low_20=round(dist_low20, 4),
        break_high_20=break_high20, break_high_50=break_high50, break_low_20=break_low20,
        break_strength=round(break_strength, 4),
        # 9 区间
        range_width_20=round(range_width_20, 4), range_width_50=round(range_width_50, 4),
        range_position=round(range_position, 4), range_duration=rd,
        # 10 成交量
        volume_ratio=round(volume_ratio, 3), volume_change_5=round(volume_change_5, 3),
        # 11 市场状态
        market_state=market_state,
    )

# ── 未来 20 根 outcome ───────────────────────────────────────────
def future_outcome(S, i, side):
    c = S["c"]; price = c[i]
    max_up = -1e9; max_down = 1e9
    for k in range(1, FWD + 1):
        if i + k >= len(c):
            break
        ret = (c[i + k] - price) / price
        if ret > max_up: max_up = ret
        if ret < max_down: max_down = ret
    max_up = max(max_up, 0.0); max_down = min(max_down, 0.0)
    max_profit = max_up if side > 0 else -max_down
    max_adverse = max_down if side > 0 else -max_up
    big_up = max_up >= BIG
    big_down = max_down <= -BIG
    big_move = big_up or big_down
    big_fav = (max_profit >= BIG)
    return dict(max_up=round(max_up * 100, 3), max_down=round(max_down * 100, 3),
                max_profit=round(max_profit * 100, 3),
                max_adverse=round(max_adverse * 100, 3),
                big_move=big_move, big_up=big_up, big_down=big_down, big_fav=big_fav)

# ── 学习分析（纯 Python）─────────────────────────────────────────
def analyze(rows, target="big_move"):
    n = len(rows)
    base_rate = sum(1 for r in rows if r[target]) / n
    # 数值特征列表（排除 outcome / 类别 / 标识）
    num_feats = ["ma30_slope_4h", "close_ma30_distance_ATR", "ma10_above_ma30",
                 "ma30_above_ma60", "ADX14", "ADX_change_5", "ER20", "ER_change_5",
                 "ER_change_10", "ATR_percent", "ATR_change_5", "ATR_change_10",
                 "ATR_percentile_100", "st_distance_ATR", "st_distance_change",
                 "st_same_direction_count", "flip_20", "flip_50", "flip_100",
                 "mom5", "return_5", "return_10", "return_20", "momentum_change",
                 "body_ATR", "upper_shadow_ratio", "lower_shadow_ratio",
                 "same_direction_candle_count", "distance_high_20", "distance_low_20",
                 "break_strength", "range_width_20", "range_width_50",
                 "range_position", "range_duration", "volume_ratio", "volume_change_5"]
    cat_feats = ["session", "market_state", "break_high_20", "break_high_50",
                 "break_low_20", "ma10_above_ma30", "ma30_above_ma60"]

    def corr(xs, ys):
        m = len(xs); mx = sum(xs) / m; my = sum(ys) / m
        cov = sum((xs[k] - mx) * (ys[k] - my) for k in range(m))
        vx = sum((xs[k] - mx) ** 2 for k in range(m)) ** 0.5
        vy = sum((ys[k] - my) ** 2 for k in range(m)) ** 0.5
        return cov / (vx * vy) if vx and vy else 0.0

    report = {"target": target, "base_rate": round(base_rate, 4), "n": n, "features": {}}

    for f in num_feats:
        pairs = [(r[f], 1 if r[target] else 0) for r in rows
                 if isinstance(r.get(f), (int, float))]
        if len(pairs) < 50:
            continue
        xs = [v for v, _ in pairs]; ys = [lab for _, lab in pairs]
        coef = corr(xs, ys)
        # 十分位数
        svals = sorted(xs)
        dec = []
        for d in range(10):
            lo_i = (len(svals) * d) // 10
            hi_i = (len(svals) * (d + 1)) // 10
            seg = ys[lo_i:hi_i] if d < 9 else ys[lo_i:]
            if seg:
                dec.append((d, round(sum(seg) / len(seg), 4), len(seg)))
        best_dec = max(dec, key=lambda x: x[1])
        # 最优切分（决策树桩）：阈值取 5..95 百分位，比较「>=阈值」组命中率。
        # 优先选 n_hi >= max(100, 8%n) 的稳健切分；否则退化为最大 lift。
        qs = [svals[int(len(svals) * p / 100)] for p in range(5, 100, 5)]
        min_hi = max(100, int(n * 0.08))
        best_split = None; best_robust = None
        for q in qs:
            hi = [lab for x, lab in pairs if x >= q]
            lo = [lab for x, lab in pairs if x < q]
            if hi and lo:
                r_hi = sum(hi) / len(hi); r_lo = sum(lo) / len(lo)
                lift = r_hi - r_lo
                if best_split is None or lift > best_split["lift"]:
                    best_split = {"thr": round(q, 4), "rate_hi": round(r_hi, 4),
                                  "n_hi": len(hi), "rate_lo": round(r_lo, 4),
                                  "n_lo": len(lo), "lift": round(lift, 4)}
                if len(hi) >= min_hi and (best_robust is None or lift > best_robust["lift"]):
                    best_robust = {"thr": round(q, 4), "rate_hi": round(r_hi, 4),
                                   "n_hi": len(hi), "rate_lo": round(r_lo, 4),
                                   "n_lo": len(lo), "lift": round(lift, 4),
                                   "robust": True}
        if best_robust:
            best_split = best_robust
        report["features"][f] = {
            "corr_with_big_move": round(coef, 4),
            "best_decile": {"decile": best_dec[0], "rate": best_dec[1],
                            "lift": round(best_dec[1] - base_rate, 4)},
            "best_split": best_split,
        }

    for f in cat_feats:
        groups = {}
        for r in rows:
            key = r.get(f)
            if key is None or key == "":
                continue
            groups.setdefault(key, []).append(1 if r[target] else 0)
        res = {str(k): {"rate": round(sum(v) / len(v), 4), "n": len(v),
                        "lift": round(sum(v) / len(v) - base_rate, 4)}
               for k, v in groups.items()}
        if res:
            report["features"][f] = {"categorical": True, "groups": res}

    # 排序：相关绝对值 + 最优切分 lift 综合
    scored = []
    for f, d in report["features"].items():
        if d.get("categorical"):
            best_lift = max((g["lift"] for g in d["groups"].values()), default=0)
            score = best_lift
        else:
            score = max(abs(d["corr_with_big_move"]), d["best_split"]["lift"] if d["best_split"] else 0)
        scored.append((f, score, d))
    scored.sort(key=lambda x: x[1], reverse=True)
    report["ranking"] = [{"feature": f, "score": round(s, 4)} for f, s, _ in scored]
    return report

# ── 主流程 ───────────────────────────────────────────────────────
def main():
    base, h4 = load()
    S = build_series(base)
    c4ts, c4, c4ma30 = h4_ctx(h4)
    n = S["n"]
    print(f"1h 数据: {n} 根, 翻转信号(全): {len(S['flips'])}")

    rows = []
    for fi in S["flips"]:
        i = fi
        if i < MIN_I or i + FWD >= n:
            continue
        side = 1 if S["trend"][i] == 1 else -1       # 翻转后的方向（buy=+1）
        fdict = features_at(S, i, c4ts, c4ma30, bisect.bisect_right(c4ts, S["ts"][i]) - 1)
        if fdict is None:
            continue
        out = future_outcome(S, i, side)
        rec = {
            "ts": S["ts"][i],
            "time": datetime.datetime.fromtimestamp(S["ts"][i] / 1000, datetime.UTC).strftime("%Y-%m-%d %H:%M"),
            "side": side,          # 1=多 -1=空
            "price": round(S["c"][i], 2),
            **out,
            **fdict,
        }
        rows.append(rec)

    print(f"有效信号(可量未来20根): {len(rows)}")
    bm = sum(1 for r in rows if r["big_move"])
    bf = sum(1 for r in rows if r["big_fav"])
    print(f"大波动(>=5.2%摆动): {bm} ({bm/len(rows)*100:.1f}%)  方向有利大波动: {bf} ({bf/len(rows)*100:.1f}%)")

    # 写出 CSV
    cols = ["ts", "time", "side", "price", "max_up", "max_down", "max_profit",
            "max_adverse", "big_move", "big_up", "big_down", "big_fav"] + \
           ["session", "ma30_slope_4h", "close_ma30_distance_ATR", "ma10_above_ma30",
            "ma30_above_ma60", "ADX14", "ADX_change_5", "ER20", "ER_change_5",
            "ER_change_10", "ATR_percent", "ATR_change_5", "ATR_change_10",
            "ATR_percentile_100", "st_distance_ATR", "st_distance_change",
            "st_same_direction_count", "flip_20", "flip_50", "flip_100", "mom5",
            "return_5", "return_10", "return_20", "momentum_change", "body_ATR",
            "upper_shadow_ratio", "lower_shadow_ratio", "same_direction_candle_count",
            "distance_high_20", "distance_low_20", "break_high_20", "break_high_50",
            "break_low_20", "break_strength", "range_width_20", "range_width_50",
            "range_position", "range_duration", "volume_ratio", "volume_change_5",
            "market_state"]
    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader(); w.writerows(rows)
    print(f"写出信号表 -> {OUT_CSV}")

    reports = {}
    for tgt in ("big_move", "big_fav"):
        reports[tgt] = analyze(rows, tgt)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(reports, f, ensure_ascii=False, indent=2)
    print(f"写出学习报告 -> {OUT_JSON}")

    for tgt, title in (("big_move", "大波动(>=5.2%摆动，任意方向)"),
                      ("big_fav", "方向有利大波动(信号方向>=5.2%，即最大盈利)")):
        report = reports[tgt]
        print(f"\n===== {title}：最强条件 Top 12 =====")
        print(f"（基准命中率 = {report['base_rate']*100:.1f}%，样本 n={report['n']}）\n")
        for item in report["ranking"][:12]:
            f = item["feature"]; d = report["features"][f]
            if d.get("categorical"):
                best = max(d["groups"].items(), key=lambda kv: kv[1]["lift"])
                print(f"  {f:22s} 类别 {best[0]:>4}  命中 {best[1]['rate']*100:5.1f}%  提升 {best[1]['lift']*100:+5.1f}%  (n={best[1]['n']})")
            else:
                bs = d["best_split"]
                corr = d["corr_with_big_move"]
                if bs:
                    tag = "稳健" if bs.get("robust") else "极端尾"
                    print(f"  {f:22s} 相关 {corr:+5.2f}  [{tag}]≥{bs['thr']} 命中 {bs['rate_hi']*100:5.1f}%(n={bs['n_hi']}) vs {bs['rate_lo']*100:5.1f}%(n={bs['n_lo']}) 提升 {bs['lift']*100:+5.1f}%")
                else:
                    print(f"  {f:22s} 相关 {corr:+5.2f}  (样本不足)")

if __name__ == "__main__":
    main()
