"""
BTC 近半年回测:  4h MA30 定方向 + 15m RSI(3) 超卖回升入场 + 单方向
- 方向(4h):  收价 > MA30 -> 只做多;  收价 < MA30 -> 只做空
- 入场(15m): 仅同向时, RSI(3) 从<20 回升(做多) / 从>80 回落(做空)
- 出场:      4h 方向翻转 或 RSI(3) 进入对侧超买/超卖
- 费用:      Taker 0.05% + 滑点 0.03%/边(市价进出)
- 杠杆:      1x 满仓(名义=净值), 与之前 45.6% 口径可比
数据: Binance BTCUSDT klines
"""
import time, json, urllib.request

SYMBOL     = "BTCUSDT"
DAYS        = 180
DIR_BAR     = "4h"
DIR_MA      = 30
ENT_BAR     = "15m"
RSI_PERIOD  = 3
RSI_LOW     = 20
RSI_HIGH    = 80
TAKER_FEE   = 0.0005
SLIP        = 0.0003
START_EQ    = 10000.0

INTERVAL_MS = {"15m": 15 * 60 * 1000, "1h": 60 * 60 * 1000,
               "4h": 4 * 60 * 60 * 1000, "1d": 24 * 60 * 60 * 1000}


def fetch_klines(symbol, interval, days, extra_days=0):
    interval_ms = INTERVAL_MS[interval]
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (days + extra_days) * 86400 * 1000
    base = (f"https://api.binance.com/api/v3/klines?symbol={symbol}"
            f"&interval={interval}&limit=1000")
    out, cur = [], start_ms
    while cur < end_ms:
        u = f"{base}&startTime={cur}"
        req = urllib.request.Request(u, headers={"User-Agent": "bt/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            rows = json.loads(r.read())
        if not rows:
            break
        for row in rows:
            ot = row[0]
            if ot >= end_ms:
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


def rsi(closes, period):
    n = len(closes)
    res = [None] * n
    ch = [0.0] * n
    for i in range(1, n):
        ch[i] = closes[i] - closes[i - 1]
    for i in range(period, n):
        seg = ch[i - period + 1:i + 1]
        g = sum(max(x, 0) for x in seg) / period
        l = sum(max(-x, 0) for x in seg) / period
        if l == 0:
            res[i] = 100.0
        elif g == 0:
            res[i] = 0.0
        else:
            rs = g / l
            res[i] = 100 - 100 / (1 + rs)
    return res


def backtest(d4, d15):
    # 4h MA30
    c4 = [x["c"] for x in d4]
    ma4 = sma(c4, DIR_MA)
    # 4h 方向查表: 每个 4h 收盘给出 (ts, dir) dir=1多/-1空
    dir4 = []  # (ts, direction) 该 4h 收盘后方向
    for i, x in enumerate(d4):
        if ma4[i] is None:
            continue
        dir4.append((x["ts"], 1 if x["c"] > ma4[i] else -1))
    # 15m RSI
    c15 = [x["c"] for x in d15]
    r = rsi(c15, RSI_PERIOD)

    # 方向查找(二分)
    def direction_at(t):
        lo, hi = 0, len(dir4) - 1
        ans = None
        while lo <= hi:
            mid = (lo + hi) // 2
            if dir4[mid][0] <= t:
                ans = dir4[mid][1]; lo = mid + 1
            else:
                hi = mid - 1
        return ans

    equity = START_EQ
    pos = 0              # 0 空仓, 1 多, -1 空
    entry = 0.0
    units = 0.0
    fees = 0.0
    trades = []
    curve = []
    prev_r = None

    def open_pos(side, price):
        nonlocal equity, pos, entry, units, fees
        fill = price * (1 + SLIP) if side == 1 else price * (1 - SLIP)
        u = equity / fill
        notional = u * fill
        f = notional * TAKER_FEE
        equity -= f; fees += f
        pos = side; entry = fill; units = u

    def close_pos(price):
        nonlocal equity, pos, units, fees, trades
        fill = price * (1 - SLIP) if pos == 1 else price * (1 + SLIP)
        notional = units * fill
        f = notional * TAKER_FEE
        pnl = units * (fill - entry) if pos == 1 else units * (entry - fill)
        equity += pnl - f; fees += f
        trades.append(pnl / (units * entry) * 100)
        pos = 0; units = 0.0

    for j in range(len(d15)):
        if r[j] is None or prev_r is None:
            prev_r = r[j]
            continue
        t = d15[j]["ts"]
        d = direction_at(t)
        price = c15[j]
        rr = r[j]
        if pos == 0:
            if d == 1 and prev_r < RSI_LOW and rr >= RSI_LOW:
                open_pos(1, price)
            elif d == -1 and prev_r > RSI_HIGH and rr <= RSI_HIGH:
                open_pos(-1, price)
        elif pos == 1:
            if d == -1 or (prev_r > RSI_HIGH and rr <= RSI_HIGH):
                close_pos(price)
        elif pos == -1:
            if d == 1 or (prev_r < RSI_LOW and rr >= RSI_LOW):
                close_pos(price)
        # 标记曲线(含未实现)
        unreal = 0.0
        if pos != 0:
            unreal = units * (price - entry) if pos == 1 else units * (entry - price)
        curve.append(equity + unreal)
        prev_r = rr

    if pos != 0:
        close_pos(c15[-1])

    peak = START_EQ
    mdd = 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    wins = sum(1 for p in trades if p > 0)
    total_ret = equity / START_EQ * 100 - 100
    bh = c15[-1] / c15[0] * 100 - 100
    return {
        "trades": len(trades),
        "final_equity": equity,
        "total_return_pct": total_ret,
        "win_rate_pct": (wins / len(trades) * 100) if trades else 0,
        "avg_trade_pct": (sum(trades) / len(trades)) if trades else 0,
        "max_drawdown_pct": mdd * 100,
        "fees_pct": fees / START_EQ * 100,
        "bh_return_pct": bh,
    }


def main():
    print(f"方向: {DIR_BAR} MA{DIR_MA}  入场: {ENT_BAR} RSI({RSI_PERIOD}) "
          f"超卖{RSI_LOW}/超买{RSI_HIGH}  单方向  费率Taker={TAKER_FEE*100:.2f}% 滑点={SLIP*100:.2f}%/边")
    d4 = fetch_klines(SYMBOL, DIR_BAR, DAYS, extra_days=10)
    d15 = fetch_klines(SYMBOL, ENT_BAR, DAYS)
    print(f"数据: 4h {len(d4)} 根, 15m {len(d15)} 根, 区间 "
          f"{d15[0]['ts']//1000 and time.strftime('%Y-%m-%d', time.gmtime(d15[0]['ts']/1000))} ~ "
          f"{time.strftime('%Y-%m-%d', time.gmtime(d15[-1]['ts']/1000))}")
    res = backtest(d4, d15)
    print(f"\n{'='*60}\n BTCUSDT  {DIR_BAR} MA{DIR_MA} + {ENT_BAR} RSI{RSI_PERIOD}  近{DAYS}天\n{'='*60}")
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
