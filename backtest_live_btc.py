"""
BTC 回测: 默认 SuperTrend 信号(15,9.1) + 线上 v1 打分拦截
═════════════════════════════════════════════════════════════════
复用 backend/backtest.py 的生产引擎 run_backtest:
  - 信号: 默认 ST(periods=15, multiplier=9.1, src=hl2, change_atr=True) 翻转(buy/sell 双方向)
  - 拦截: live_gate = TradeConfig 默认值(=线上 v1 打分制: use_scoring=True,
           score_engine=""(v1), 阈值 80/60/40, 动态ER, min_score=2, allow_grades=[A,B])
  - 出场: ST 翻转(与 4h MA30 版一致, 不含 TP/SL)
  - BTC 本地无自定义评分配置 -> 用 TradeConfig 代码默认值(即线上未改时的基线)
费用: taker 0.05%/边
用法: python backtest_live_btc.py BTCUSDT           (近半年)
      python backtest_live_btc.py BTCUSDT 2025       (2025 全年)
"""
import sys, time, json, urllib.request
from datetime import datetime

sys.path.insert(0, r"d:/个人项目代码/supertrendMain/backend")
from regime import TradeConfig
from backtest import run_backtest

INTERVAL_MS = {"15m": 15 * 60 * 1000, "1h": 60 * 60 * 1000, "4h": 4 * 60 * 60 * 1000}


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
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            ot = row[0]
            if ot >= until_ms:
                break
            out.append({"ts": ot, "o": float(row[1]), "h": float(row[2]),
                        "l": float(row[3]), "c": float(row[4]), "vol": float(row[5])})
        cur = rows[-1][0] + interval_ms
        time.sleep(0.03)
    return out


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    year = sys.argv[2] if len(sys.argv) > 2 else None
    thr_arg = float(sys.argv[3]) if len(sys.argv) > 3 else None
    # 生产是单闸门(三阈值相等): MU=40, ETH/SPCX=44。BTC 本地无自定义配置,
    # 这里扫 40 / 44 两个候选; 若你线上 BTC 用别的值, 用第3参数覆盖。
    thrs = [thr_arg] if thr_arg else [40.0, 44.0]

    windows = []
    if year:
        y = int(year)
        windows.append((str(y), int(datetime(y, 1, 1).timestamp() * 1000),
                        int(datetime(y + 1, 1, 1).timestamp() * 1000)))
    else:
        u = int(time.time() * 1000)
        windows.append(("近半年", u - 180 * 86400 * 1000, u))

    p = {"periods": 15, "multiplier": 9.1, "src": "hl2", "change_atr": True}

    for label, start_ms, until_ms in windows:
        c15 = fetch_klines(symbol, "15m", start_ms, until_ms)
        c1h = fetch_klines(symbol, "1h", start_ms, until_ms)
        c4h = fetch_klines(symbol, "4h", start_ms, until_ms)
        candles_by_tf = {"15m": c15, "1h": c1h, "4h": c4h}
        print(f"\n=== {symbol} 默认ST(15,9.1) + 线上v1评分拦截  {label} ===")
        print(f"数据: 15m {len(c15)} / 1h {len(c1h)} / 4h {len(c4h)} 根")
        for thr in thrs:
            cfg = TradeConfig()  # 线上 v1 打分制默认参数
            cfg.enabled = True    # 总开关: 默认 False, 线上为 True
            cfg.scoring_full_threshold = thr
            cfg.scoring_half_threshold = thr
            cfg.scoring_alert_threshold = thr
            res = run_backtest(c15, p, init_cash=10000.0, fee_rate=0.0005,
                               allow_short=True, live_gate=cfg, gate_tf="15m",
                               candles_by_tf=candles_by_tf, exit_rules=None,
                               sizing="equity", leverage=1)
            if "error" in res:
                print(f"  [阈值 {thr}] ERR: {res['error']}")
                continue
            print(f"  ── 评分闸门={thr} ──")
            print(f"    交易次数 : {res['trades']}   被拦: {res['er_blocked']} 笔")
            print(f"    总收益   : {res['return_pct']:.2f}%   "
                  f"买入持有 {res['hold_pct']:.2f}%  (α={res['alpha_pct']:+.2f}%)")
            print(f"    胜率     : {res['win_rate']:.1f}%   "
                  f"均盈+{res['avg_win']:.2f}%/均亏{res['avg_loss']:.2f}%  PF={res['profit_factor']}")
            print(f"    最大回撤 : {res['max_dd_pct']:.2f}%")


if __name__ == "__main__":
    main()
