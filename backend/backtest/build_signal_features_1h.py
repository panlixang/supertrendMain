# -*- coding: utf-8 -*-
"""为 st_signals_1h.csv 的 808 笔信号，逐笔统计：
  时间 / 信号 / 出入场 / 入场后最高值 / 最低值 / 止盈止损 / 盈亏 / 4h方向
  趋势区间（market_regime 六态分类：趋势启动/运行/延续/衰减期、假突破期、震荡吸收期、恐慌释放期）
  A. 前20根  涨跌幅 / ATR变化 / ER变化 / ADX变化 / ST距离变化
  B. 前50根  ST翻转次数 / 高低点次数 / 趋势持续时间
  C. 前100根 趋势生命周期 / 横盘周期 / 波动周期

数据：从 OKX 拉取 BTC-USDT 1h 全量历史（覆盖 2022-08 ~ 今），本地缓存后复用。
指标口径与仓库一致：SuperTrend(period=10, mult=3.0, change_atr=True)；
  ATR_pct = ATR/close*100；ER20 = Kaufman 效率比(len=20)；
  ADX14；ST距离 = (close - 当前趋势带)/ATR（带符号，多正空负）。

"前N根"窗口 = 信号bar之前紧邻的 N 根，即 [i-N, i-1]：
  - 变化类 = 区间末(i-1) 减 区间首(i-N)；
  - 计数类（翻转/高低点/横盘/波动）= 落在 [i-N, i-1] 的根数。
"""
import sys, os, json, csv, math, calendar, datetime, bisect

BASE = r"d:\个人项目代码\supertrendMain\backend"
sys.path.insert(0, BASE)
from indicators import super_trend, ta_adx, ta_sma
import history
import market_regime
import signal_v3

OUT_JSON = os.path.join(BASE, "backtest", "btc_1h_full.json")
MASTER = os.path.join(BASE, "backtest", "st_signals_1h.csv")
OUT_CSV = os.path.join(BASE, "backtest", "st_signals_1h_features.csv")

ST_PERIODS, ST_MULT = 10, 3.0

# ── 1. 获取全量 1h 历史 ──────────────────────────────────────────
def load_base():
    if os.path.exists(OUT_JSON):
        print("加载缓存全量 1h:", OUT_JSON)
        d = json.load(open(OUT_JSON, encoding="utf-8"))
        return d["base"]
    print("拉取 BTC-USDT 1h 全量历史（约 36000 根，覆盖 2022-08 起）…")
    cs = history.fetch_candles("1h", limit=36500, symbol="BTC-USDT")
    base = [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol} for c in cs]
    json.dump({"base": base}, open(OUT_JSON, "w", encoding="utf-8"))
    print("已保存", len(base), "根 ->", OUT_JSON)
    return base

def load_h4():
    OUT = os.path.join(BASE, "backtest", "btc_4h_full.json")
    if os.path.exists(OUT):
        print("加载缓存全量 4h:", OUT)
        return json.load(open(OUT, encoding="utf-8"))["h4"]
    print("拉取 BTC-USDT 4h 全量历史（约 9200 根，覆盖 2022-08 起）…")
    cs = history.fetch_candles("4h", limit=9200, symbol="BTC-USDT")
    h4 = [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol} for c in cs]
    json.dump({"h4": h4}, open(OUT, "w", encoding="utf-8"))
    print("已保存", len(h4), "根 ->", OUT)
    return h4

# ── 2. 逐bar指标序列 ─────────────────────────────────────────────
def build_series(base):
    o = [b["o"] for b in base]; h = [b["h"] for b in base]
    l = [b["l"] for b in base]; c = [b["c"] for b in base]
    v = [b["vol"] for b in base]; ts = [b["ts"] for b in base]
    n = len(c)
    st = super_trend(o, h, l, c, periods=ST_PERIODS, multiplier=ST_MULT, change_atr=True)
    trend = st["trend"]; atr = st["atr"]; up = st["up"]; dn = st["dn"]
    flips = sorted(f["i"] for f in st["flips"])          # ST 翻转 bar 索引
    adx = ta_adx(h, l, c, 14)

    atr_pct = [ (atr[i] / c[i] * 100.0 if (atr[i] and c[i]) else 0.0) for i in range(n) ]
    # ER20 向量化（cumsum of |Δclose|）
    cum = [0.0] * (n + 1)
    for i in range(1, n):
        cum[i] = cum[i - 1] + abs(c[i] - c[i - 1])
    er20 = [0.0] * n
    for i in range(20, n):
        den = cum[i] - cum[i - 20]
        er20[i] = abs(c[i] - c[i - 20]) / den if den > 0 else 0.0
    # ST 距离（带符号，多正空负）
    st_dist = [0.0] * n
    for i in range(n):
        if trend[i] is None or atr[i] in (None, 0):
            st_dist[i] = 0.0; continue
        line = up[i] if trend[i] == 1 else dn[i]
        st_dist[i] = (c[i] - line) / atr[i]
    return dict(o=o, h=h, l=l, c=c, v=v, ts=ts, n=n, trend=trend, atr=atr,
                flips=flips, adx=adx, atr_pct=atr_pct, er20=er20, st_dist=st_dist)

# ── 3. 工具 ─────────────────────────────────────────────────────
def msec(s):
    return calendar.timegm(datetime.datetime.strptime(s, "%Y/%m/%d %H:%M").timetuple())

def _num(v):
    """CSV 列可能是空串；统一转成 float，取不到返回 None（交给闸门按缺数据处理）。"""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except Exception:
        return None

def swing_count(h, l, a, b):
    """[a,b] 内 摆动高点 + 低点 个数（±2 邻域）"""
    cnt = 0
    for k in range(a + 2, b - 1):
        if (h[k] > h[k - 1] and h[k] >= h[k + 1] and h[k] > h[k - 2] and h[k] >= h[k + 2]):
            cnt += 1
        if (l[k] < l[k - 1] and l[k] <= l[k + 1] and l[k] < l[k - 2] and l[k] <= l[k + 2]):
            cnt += 1
    return cnt

# ── 4. 主流程 ───────────────────────────────────────────────────
def main():
    base = load_base()
    S = build_series(base)
    c, h, l = S["c"], S["h"], S["l"]
    ts = S["ts"]; n = S["n"]; flips = S["flips"]
    adx, atr_pct, er20, st_dist = S["adx"], S["atr_pct"], S["er20"], S["st_dist"]
    ma30_1h = ta_sma(c, 30)

    # 4h 全量历史 + MA30
    h4 = load_h4()
    c4 = [b["c"] for b in h4]; ts4 = [b["ts"] for b in h4]
    ma30_4h = ta_sma(c4, 30)

    base_sec = {int(b["ts"] / 1000): i for i, b in enumerate(base)}

    rows = list(csv.DictReader(open(MASTER, encoding="utf-8-sig")))
    out = []
    miss = 0
    for r in rows:
        sec = msec(r["time"])
        i = base_sec.get(sec)
        if i is None:
            miss += 1
            out.append(dict(r)); continue

        # ── 基础列（直接取自 master）──
        rec = {
            "时间": r["time"],
            "信号": r["signal"],
            "出入场": f"{r['close']}->{r['reverse_signal_price']}",
            "入场后最高值": r["future_high"],
            "最低值": r["future_low"],
            "止盈止损": r["exit_result"],
            "盈亏": r["pnl_pct"],
            "4h方向": r["htf_dir"],
        }

        # ── 行情趋势区间（market_regime 六态分类，截断到信号bar）──
        g = market_regime.classify_market_regime(
            S["c"][:i + 1], S["h"][:i + 1], S["l"][:i + 1],
            S["atr"][:i + 1], S["adx"][:i + 1], flips, i, int(r["signal"]))
        rec["趋势区间"] = g["regime_cn"]
        rec["区间置信度"] = g["confidence"]
        rec["区间可交易"] = g["tradeable"]

        # ── MA30 斜率 / 距离（1h 与 4h）──
        price = c[i]
        # 1h MA30：斜率 = 过去10根 MA30 累计变化%；距离 = 信号价相对 MA30 的偏离%
        m1 = ma30_1h[i]
        if m1:
            rec["1h_MA30斜率"] = round((ma30_1h[i] - ma30_1h[i - 10]) / ma30_1h[i - 10] * 100, 4) if (i >= 10 and ma30_1h[i - 10]) else ""
            rec["1h_MA30距离"] = round((price - m1) / m1 * 100, 4)
        else:
            rec["1h_MA30斜率"] = rec["1h_MA30距离"] = ""
        # 4h：取信号时刻之前最近一根 4h K
        j4 = bisect.bisect_right(ts4, ts[i]) - 1
        if j4 >= 10:
            m4 = ma30_4h[j4]
            if m4:
                rec["4h_MA30斜率"] = round((ma30_4h[j4] - ma30_4h[j4 - 10]) / ma30_4h[j4 - 10] * 100, 4) if ma30_4h[j4 - 10] else ""
                rec["4h_MA30距离"] = round((price - m4) / m4 * 100, 4)
            else:
                rec["4h_MA30斜率"] = rec["4h_MA30距离"] = ""
        else:
            rec["4h_MA30斜率"] = rec["4h_MA30距离"] = ""

        # ── A. 前20根 [i-20, i-1] ──
        a0, a1 = max(0, i - 20), i - 1
        if i >= 20:
            rec["前20根_涨跌幅"] = round((c[a1] - c[a0]) / c[a0] * 100, 4)
            rec["前20根_ATR变化"] = round(atr_pct[a1] - atr_pct[a0], 4)
            rec["前20根_ER变化"] = round(er20[a1] - er20[a0], 4)
            rec["前20根_ADX变化"] = round((adx[a1] or 0) - (adx[a0] or 0), 4)
            rec["前20根_ST距离变化"] = round(st_dist[a1] - st_dist[a0], 4)
        else:
            for k in ["涨跌幅", "ATR变化", "ER变化", "ADX变化", "ST距离变化"]:
                rec["前20根_" + k] = ""

        # ── B. 前50根 [i-50, i-1] ──
        b0 = i - 50
        lo_b = max(0, b0)
        flips_b = sum(1 for f in flips if b0 <= f < i)          # 窗口内翻转次数
        sw_b = swing_count(h, l, lo_b, i)                       # 高低点次数
        # 趋势持续时间 = 当前趋势已持续根数（到信号bar为止）
        pos = bisect.bisect_left(flips, i)
        last_flip = flips[pos - 1] if pos > 0 else 0
        trend_dur = i - last_flip
        rec["前50根_ST翻转次数"] = flips_b
        rec["前50根_高低点次数"] = sw_b
        rec["前50根_趋势持续时间"] = trend_dur

        # ── C. 前100根 [i-100, i-1] ──
        c0 = i - 100
        lo_c = max(0, c0)
        win = range(lo_c, i)
        flips_c = sum(1 for f in flips if c0 <= f < i)
        lifecycle = flips_c + 1                            # 趋势段数（生命周期）
        med_atr = sorted(atr_pct[k] for k in win if atr_pct[k] > 0)
        med_atr = med_atr[len(med_atr) // 2] if med_atr else 0.0
        side = sum(1 for k in win if (adx[k] or 0) < 20)        # 横盘（ADX<20）
        volat = sum(1 for k in win if med_atr > 0 and atr_pct[k] > 1.5 * med_atr)  # 波动
        rec["前100根_趋势生命周期"] = lifecycle
        rec["前100根_横盘周期"] = side
        rec["前100根_波动周期"] = volat

        # ── Signal Engine V3：趋势打分闸门（与实盘 pattern_trade 共用 signal_v3 口径）──
        #   ST Signal → Trend Gate(成熟趋势) / Early Breakout V2(早期启动) → Trend Score
        #   → Range Penalty(两级 Fuse) → Execute
        sig_dir = int(r["signal"])
        bf = signal_v3.base_features(c, S["atr"], atr_pct, er20, st_dist,
                                     flips, ma30_1h, i, sig_dir)
        if bf is None:
            rec["V3_分数"] = rec["V3_路径"] = rec["V3_可执行"] = ""
        else:
            # 斜率/距离类直接沿用本表已算好的列，避免二次计算和四舍五入差异
            feats = dict(bf)
            feats.update({
                "slope_htf":    _num(rec.get("4h_MA30斜率")),
                "dist_base_ma": _num(rec.get("1h_MA30距离")),
                "dist_htf_ma":  _num(rec.get("4h_MA30距离")),
                "adx_chg20":    _num(rec.get("前20根_ADX变化")),
                "er_chg20":     _num(rec.get("前20根_ER变化")),
                "vol100":       _num(rec.get("前100根_波动周期")),
            })
            v = signal_v3.v3_decide(sig_dir, feats)
            rec["V3_分数"] = v["score"]
            rec["V3_路径"] = v["path"]
            rec["V3_可执行"] = "TRUE" if v["execute"] else "FALSE"

        out.append(rec)

    header = ["时间", "信号", "出入场", "入场后最高值", "最低值", "止盈止损", "盈亏", "4h方向",
              "趋势区间", "区间置信度", "区间可交易",
              "1h_MA30斜率", "4h_MA30斜率", "1h_MA30距离", "4h_MA30距离",
              "前20根_涨跌幅", "前20根_ATR变化", "前20根_ER变化", "前20根_ADX变化", "前20根_ST距离变化",
              "前50根_ST翻转次数", "前50根_高低点次数", "前50根_趋势持续时间",
              "前100根_趋势生命周期", "前100根_横盘周期", "前100根_波动周期",
              "V3_分数", "V3_路径", "V3_可执行"]
    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(out)
    print(f"写出 {len(out)} 笔 -> {OUT_CSV}")
    print(f"未匹配时间: {miss}")

if __name__ == "__main__":
    main()
