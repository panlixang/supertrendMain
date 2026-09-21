"""
BTC 近半年回测:  4h MA30 + 斜率 + Distance 判方向  +  15m SuperTrend 入场(同向) + 震荡不做
═══════════════════════════════════════════════════════════════════════
方向(4h, 每根4h收盘判定一次):
  TREND_UP  (LONG_ONLY) : slope > 0.8  且 close > MA30  且 |close-MA30|/ATR < 3
  TREND_DOWN(SHORT_ONLY): slope < -0.8  且 close < MA30  且 |close-MA30|/ATR < 3
  其他                   : RANGE -> NO_TRADE
入场(15m SuperTrend 翻转):
  仅当方向与 4h regime 同向才成交; RANGE 不开仓、且持仓在 regime 离开同向时平仓
费用: Taker 0.05% + 滑点 0.03%/边(市价进出)   杠杆: 1x 满仓
═══════════════════════════════════════════════════════════════════════
"""
import sys, time, json, urllib.request
from datetime import datetime
sys.path.insert(0, r"d:/个人项目代码/supertrendMain/backend")
from indicators import super_trend

SYMBOL      = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
YEAR        = sys.argv[2] if len(sys.argv) > 2 else None
MODE        = sys.argv[3] if len(sys.argv) > 3 else "dir"   # dir=评分定方向  intercept=默认ST信号+评分拦截
DAYS         = 180
WARMUP_DAYS = 7
DIR_BAR      = "4h"
DIR_MA       = 30
ATR_PERIOD   = 14
SLOPE_LB     = 10          # 斜率回看(4h根)
DIST_TH      = 3.0         # 入场 Distance 阈值(ATR)
PERSIST_LB   = 10          # MA 方向持续性回看(4h根)
NEAR_THRESH  = 0.5         # "价格接近MA30" 的距离阈值(ATR)
SCORE_LONG   = 60          # 评分 >= 此值 -> LONG
SCORE_SHORT  = 40          # 评分 <= 此值 -> SHORT
                         # 40~60 之间 -> RANGE(不做)
ST_PERIODS   = 10
ST_MULT      = 3.0
TAKER_FEE    = 0.0005
SLIP         = 0.0003
START_EQ     = 10000.0

INTERVAL_MS = {"15m": 15 * 60 * 1000, "1h": 60 * 60 * 1000,
               "4h": 4 * 60 * 60 * 1000, "1d": 24 * 60 * 60 * 1000}


def fetch_klines(symbol, interval, since_ms, until_ms):
    interval_ms = INTERVAL_MS[interval]
    base = (f"https://api.binance.com/api/v3/klines?symbol={symbol}"
            f"&interval={interval}&limit=1000")
    out, cur = [], since_ms
    while cur < until_ms:
        u = f"{base}&startTime={cur}"
        req = urllib.request.Request(u, headers={"User-Agent": "bt/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            rows = json.loads(r.read())
        if not isinstance(rows, list):
            print(f"  [警告] 数据接口返回错误: {rows}")
            return out
        if not rows:
            break
        for row in rows:
            ot = row[0]
            if ot >= until_ms:
                break
            out.append({"ts": ot, "o": float(row[1]), "h": float(row[2]),
                        "l": float(row[3]), "c": float(row[4]), "v": float(row[5])})
        cur = rows[-1][0] + interval_ms
        time.sleep(0.03)
    return out


def sma(values, period):
    n = len(values)
    out = [None] * n
    for i in range(period - 1, n):
        out[i] = sum(values[i - period + 1:i + 1]) / period
    return out


def atr(h, l, c, period):
    n = len(c)
    tr = [0.0] * n
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    out = [None] * n
    for i in range(period, n):
        out[i] = sum(tr[i - period + 1:i + 1]) / period
    return out


def regime_4h(d4):
    """4H Trend Score (满分100) -> 方向
    ① MA30斜率(ATR归一)   40分: >0.5:+40 / 0.2~0.5:+25 / 0~0.2:+10 / <0:对称负分
    ② 价格位置            30分: 远>MA30:+30 / 接近MA30(+/-0.5ATR):+15 / <MA30:0
    ③ MA方向持续性(近10)  30分: 涨8/10:+30 / 6/10:+20 / 4/10:+10 / 其他:0
    >=60 LONG   <=40 SHORT   40~60 RANGE(不做)
    """
    c = [x["c"] for x in d4]; h = [x["h"] for x in d4]; l = [x["l"] for x in d4]
    ma = sma(c, DIR_MA)
    a = atr(h, l, c, ATR_PERIOD)
    res = []  # (ts, reg, ma, atr, score)
    for i, x in enumerate(d4):
        warm = DIR_MA - 1 + SLOPE_LB + PERSIST_LB
        if ma[i] is None or a[i] is None or i < warm or i < PERSIST_LB:
            continue
        # ① 斜率
        slope = (ma[i] - ma[i - SLOPE_LB]) / a[i]
        if slope > 0.5:   s1 = 40
        elif slope > 0.2: s1 = 25
        elif slope > 0.0: s1 = 10
        elif slope > -0.2: s1 = -10
        elif slope > -0.5: s1 = -25
        else:             s1 = -40
        # ② 价格位置
        d = (x["c"] - ma[i]) / a[i]
        if abs(d) < NEAR_THRESH: s2 = 15
        elif x["c"] > ma[i]:     s2 = 30
        else:                    s2 = 0
        # ③ MA 方向持续性
        cnt = sum(1 for k in range(1, PERSIST_LB + 1) if ma[i - k] > ma[i - k - 1])
        if cnt >= 8:   s3 = 30
        elif cnt >= 6: s3 = 20
        elif cnt >= 4: s3 = 10
        else:          s3 = 0
        score = s1 + s2 + s3
        reg = "L" if score >= SCORE_LONG else ("S" if score <= SCORE_SHORT else "N")
        res.append((x["ts"], reg, ma[i], a[i], score))
    return res


def backtest(d4, d15, st_flips, start_ts=0):
    reg4 = regime_4h(d4)
    c15 = [x["c"] for x in d15]
    n = len(d15)
    flip_map = {f["i"]: f["type"] for f in st_flips if f["i"] < n}

    def regime_at(t):
        lo, hi = 0, len(reg4) - 1
        ans = None
        while lo <= hi:
            mid = (lo + hi) // 2
            if reg4[mid][0] <= t:
                ans = reg4[mid]; lo = mid + 1
            else:
                hi = mid - 1
        return ans

    equity = START_EQ
    pos = 0; entry = 0.0; units = 0.0
    fees = 0.0; trades = []; curve = []
    reg_counts = {"L": 0, "S": 0, "N": 0}
    bh_start = None

    def open_pos(side, price):
        nonlocal equity, pos, entry, units, fees
        fill = price * (1 + SLIP) if side == 1 else price * (1 - SLIP)
        u = equity / fill
        f = u * fill * TAKER_FEE
        equity -= f; fees += f
        pos = side; entry = fill; units = u

    def close_pos(price):
        nonlocal equity, pos, units, fees, trades
        fill = price * (1 - SLIP) if pos == 1 else price * (1 + SLIP)
        f = units * fill * TAKER_FEE
        pnl = units * (fill - entry) if pos == 1 else units * (entry - fill)
        equity += pnl - f; fees += f
        trades.append(pnl / (units * entry) * 100)
        pos = 0; units = 0.0

    for j in range(n):
        t = d15[j]["ts"]
        if t < start_ts:
            continue
        r4 = regime_at(t)
        reg = (r4[1] if r4 else "N")
        reg_counts[reg] += 1
        price = c15[j]
        if bh_start is None:
            bh_start = price
        if MODE == "intercept":
            # 出场: ST 翻转(反向)才平 —— 默认ST信号自己管出场
            if pos == 1 and j in flip_map and flip_map[j] == "sell":
                close_pos(price)
            elif pos == -1 and j in flip_map and flip_map[j] == "buy":
                close_pos(price)
            # 入场: 默认ST翻转信号 + 分数拦截(仅排除RANGE, 方向由ST定)
            if pos == 0 and j in flip_map and r4 is not None and reg != "N":
                typ = flip_map[j]
                ma, atr = r4[2], r4[3]
                dist = abs(price - ma) / atr
                if dist < DIST_TH:
                    if typ == "buy":
                        open_pos(1, price)
                    elif typ == "sell":
                        open_pos(-1, price)
        else:
            # dir 模式(默认): 评分定方向, 离开同向即平
            if pos == 1 and reg != "L":
                close_pos(price)
            elif pos == -1 and reg != "S":
                close_pos(price)
            # 入场: 同方向 ST 翻转, 且入场时距离 MA30 不超 DIST_TH(不追太远)
            if pos == 0 and j in flip_map and r4 is not None:
                typ = flip_map[j]
                ma, atr = r4[2], r4[3]
                dist = abs(price - ma) / atr
                if dist < DIST_TH:
                    if reg == "L" and typ == "buy":
                        open_pos(1, price)
                    elif reg == "S" and typ == "sell":
                        open_pos(-1, price)
        unreal = 0.0
        if pos != 0:
            unreal = units * (price - entry) if pos == 1 else units * (entry - price)
        curve.append(equity + unreal)

    if pos != 0:
        close_pos(c15[-1])

    peak = START_EQ; mdd = 0.0
    for v in curve:
        peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    wins = sum(1 for p in trades if p > 0)
    return {
        "trades": len(trades),
        "final_equity": equity,
        "total_return_pct": equity / START_EQ * 100 - 100,
        "win_rate_pct": (wins / len(trades) * 100) if trades else 0,
        "avg_trade_pct": (sum(trades) / len(trades)) if trades else 0,
        "max_drawdown_pct": mdd * 100,
        "fees_pct": fees / START_EQ * 100,
        "bh_return_pct": (c15[-1] / bh_start * 100 - 100) if bh_start else 0,
        "reg_counts": reg_counts,
    }


def main():
    print(f"方向: {DIR_BAR} MA{DIR_MA} 连续评分(斜率40+价格30+持续30, >= {SCORE_LONG}多 / <= {SCORE_SHORT}空)  "
          f"入场: 15m ST({ST_PERIODS},{ST_MULT}) 同向  Distance<{DIST_TH}ATR不追远")
    if YEAR:
        y = int(YEAR)
        start_ms = int(datetime(y, 1, 1).timestamp() * 1000)
        until_ms = int(datetime(y + 1, 1, 1).timestamp() * 1000)
        period_label = str(y)
    else:
        until_ms = int(time.time() * 1000)
        start_ms = until_ms - DAYS * 86400 * 1000
        period_label = f"近{DAYS}天"
    since_ms = start_ms - WARMUP_DAYS * 86400 * 1000
    d4 = fetch_klines(SYMBOL, DIR_BAR, since_ms, until_ms)
    d15 = fetch_klines(SYMBOL, "15m", since_ms, until_ms)
    st = super_trend([x["o"] for x in d15], [x["h"] for x in d15],
                     [x["l"] for x in d15], [x["c"] for x in d15],
                     ST_PERIODS, ST_MULT, "hl2", True)
    print(f"数据: 4h {len(d4)} 根, 15m {len(d15)} 根, ST翻转 {len(st['flips'])} 次")
    res = backtest(d4, d15, st["flips"], start_ts=start_ms)
    rc = res.pop("reg_counts")
    total = rc["L"] + rc["S"] + rc["N"]
    print(f"\n{'='*62}\n {SYMBOL}  [{MODE}] 4h MA30 评分  + 15m ST  {period_label}\n{'='*62}")
    print(f"  regime占比: LONG={rc['L']/total*100:.0f}%  "
          f"SHORT={rc['S']/total*100:.0f}%  RANGE={rc['N']/total*100:.0f}%  (按15m根计)")
    print(f"  交易次数 : {res['trades']}")
    print(f"  最终净值 : {res['final_equity']:.2f}  (起始 {START_EQ:.0f})")
    print(f"  总收益   : {res['total_return_pct']:.2f}%")
    print(f"  胜率     : {res['win_rate_pct']:.1f}%")
    print(f"  平均单笔 : {res['avg_trade_pct']:.2f}%")
    print(f"  最大回撤 : {res['max_drawdown_pct']:.2f}%")
    print(f"  费用占比 : {res['fees_pct']:.2f}% (占初始净值)")
    print(f"  买入持有 : {res['bh_return_pct']:.2f}%")


if __name__ == "__main__":
    main()
