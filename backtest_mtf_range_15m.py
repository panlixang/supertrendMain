"""
BTC 回测：15m 震荡期 + 1h 同 4h 方向（默认 SuperTrend 10x3）
==================================================================
策略（用户指定）：
  - 信号源：1h SuperTrend(10,3) 翻转（默认参数）
  - 入场过滤：仅当「15m 当前处于震荡期(range)」且「1h 翻转方向 == 4h 当前方向」才做
              （不做任何其他排除）
  - 止损：4h SuperTrend(10,3) 出现反向信号（4h 翻转为相反方向）后平仓
  - 止盈：固定 +1.5%（多：+1.5% / 空：-1.5%）
  - 数据：本地 candle_data.db (BTC-USDT 15m / 1h / 4h)，免联网
  - 费用：OKX Taker 0.05% + 滑点 0.03% / 单边（市价进出）
用法：python backtest_mtf_range_15m.py
"""
import sys, os, sqlite3
from datetime import datetime, timezone
from collections import defaultdict
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
BACKEND = r'd:/个人项目代码/supertrendMain/backend'
sys.path.insert(0, BACKEND)

from indicators import super_trend

DB = r'd:/个人项目代码/supertrendMain/backend/candle_data.db'
SYMBOL = 'BTC-USDT'
TF1 = '1h'; TF4 = '4h'; TF15 = '15m'
ST_P = 10; ST_M = 3.0; ST_SRC = 'hl2'; ST_CH = True
TP_PCT = 0.015          # 止盈 1.5%
FOUR_H_MS = 4 * 3600 * 1000
WIN15 = 32              # 15m 震荡判定回看窗口（8h = 32 根 15m）
MIN_FLIPS15 = 2         # 回看窗口内 15m ST 翻转 >= 此值 => 震荡期

TAKER = 0.0005
SLIP = 0.0003


def load(symbol, tf):
    c = sqlite3.connect(DB)
    rows = c.execute(
        "SELECT ts,o,h,l,c,vol FROM candles WHERE symbol=? AND tf=? ORDER BY ts ASC",
        (symbol, tf)).fetchall()
    c.close()
    return [{"ts": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "vol": r[5]} for r in rows]


def fmt(ts):
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def bucket_closed(ts_arr, t, dur_ms):
    """最新一根「已收盘」的 dur_ms 周期 K 线索引：ts+ dur_ms <= t"""
    lo, hi, ans = 0, len(ts_arr) - 1, -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if ts_arr[mid] + dur_ms <= t:
            ans = mid; lo = mid + 1
        else:
            hi = mid - 1
    return ans


def bucket_ts(ts_arr, t):
    """最新一根 ts <= t 的 K 线索引"""
    lo, hi, ans = 0, len(ts_arr) - 1, -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if ts_arr[mid] <= t:
            ans = mid; lo = mid + 1
        else:
            hi = mid - 1
    return ans


def main():
    k1 = load(SYMBOL, TF1)
    k4 = load(SYMBOL, TF4)
    k15 = load(SYMBOL, TF15)
    ts1 = [x["ts"] for x in k1]; ts4 = [x["ts"] for x in k4]; ts15 = [x["ts"] for x in k15]
    bh = (k1[-1]["c"] / k1[0]["c"] - 1) * 100
    print(f"{'#'*78}\n#  {SYMBOL}  MTF  15m震荡  +  1h同4h方向  |  ST(10,3)\n"
          f"#  1h K线 {len(k1)} 根 ({fmt(ts1[0])}..{fmt(ts1[-1])})\n"
          f"#  15m {len(k15)} 根 | 4h {len(k4)} 根 | 买入持有(1h)={bh:.2f}%\n"
          f"#  入场: 15m=震荡(回看{WIN15}根ST翻转>={MIN_FLIPS15}) 且 1h翻转方向==4h方向 | 止盈={TP_PCT*100:.1f}% | "
          f"止损=4h反向 | 费用 Taker{TAKER*100:.2f}%+滑点{SLIP*100:.2f}%\n{'#'*78}")

    o1=[x["o"] for x in k1]; h1=[x["h"] for x in k1]; l1=[x["l"] for x in k1]; c1=[x["c"] for x in k1]
    o4=[x["o"] for x in k4]; h4=[x["h"] for x in k4]; l4=[x["l"] for x in k4]; c4=[x["c"] for x in k4]
    o15=[x["o"] for x in k15]; h15=[x["h"] for x in k15]; l15=[x["l"] for x in k15]; c15=[x["c"] for x in k15]

    st1 = super_trend(o1, h1, l1, c1, ST_P, ST_M, ST_SRC, ST_CH)
    st4 = super_trend(o4, h4, l4, c4, ST_P, ST_M, ST_SRC, ST_CH)
    trend4 = st4["trend"]          # +1/-1/None
    flips1 = st1["flips"]          # [{i,type}]

    # ── 15m 震荡期检测：回看窗口内 15m ST(10,3) 翻转次数 >= 阈值 ⇒ 震荡 ──
    st15 = super_trend(o15, h15, l15, c15, ST_P, ST_M, ST_SRC, ST_CH)
    flips15_idx = [f["i"] for f in st15["flips"]]
    # 每个 15m 索引 m 之前窗口内的翻转数（前缀和）
    pref = [0] * (len(k15) + 1)
    cnt = 0
    fi = 0
    for m in range(len(k15)):
        while fi < len(flips15_idx) and flips15_idx[fi] <= m:
            cnt += 1; fi += 1
        pref[m + 1] = cnt
    def choppy15(m):
        if m < 0:
            return False
        lo = max(0, m - WIN15)
        return (pref[m + 1] - pref[lo]) >= MIN_FLIPS15
    # 统计 15m 震荡占比（观察用）
    n_chop = sum(1 for m in range(len(k15)) if choppy15(m))

    # ── 生成策略信号 ──
    n_dir_gate = 0; n_chop_gate = 0
    signals = []
    for f in flips1:
        q = f["i"]; t = k1[q]["ts"]
        jc = bucket_closed(ts4, t, FOUR_H_MS)
        if jc < 0 or trend4[jc] is None:
            continue
        d4 = trend4[jc]; d1 = 1 if f["type"] == "buy" else -1
        if d1 != d4:
            continue
        n_dir_gate += 1
        m15 = bucket_ts(ts15, t)
        if not choppy15(m15):
            continue
        n_chop_gate += 1
        signals.append({"i": q, "action": f["type"], "ts": t, "dir": d1})
    signals.sort(key=lambda x: x["i"])
    print(f"\n  1h ST(10,3) 翻转总数 = {len(flips1)}")
    print(f"  通过「方向==4h」过滤 = {n_dir_gate}")
    print(f"  再叠加「15m震荡(回看{WIN15}根翻转>={MIN_FLIPS15})」过滤 = {n_chop_gate}  → "
          f"最终策略信号 {len(signals)} 笔")
    print(f"  (15m 全样本约 {n_chop/len(k15)*100:.1f}% 的时间处于震荡期)")

    # ── 回测引擎（TP 1.5% / 4h反向止损，满仓，双边费用）──
    def run(sig_list, label):
        sig_at = defaultdict(list)
        for s in sig_list:
            sig_at[s["i"]].append(s)
        n = len(k1)
        equity = 10000.0
        pos = None            # {"side","entry","units","notional","bar"}
        trades = []
        curve = []
        # 统计 15m range 占比（仅观察用）
        for q in range(n):
            t = ts1[q]; cq = c1[q]; hq = h1[q]; lq = l1[q]
            if pos is None:
                for s in sig_at.get(q, []):
                    side = 1 if s["action"] == "buy" else -1
                    fill = cq * (1 + SLIP) if side == 1 else cq * (1 - SLIP)
                    units = equity / fill
                    fee = units * fill * TAKER
                    equity -= fee
                    pos = {"side": side, "entry": fill, "units": units,
                           "notional": units * fill, "bar": q, "entry_ts": t, "entry_px": cq}
                if pos is None:
                    curve.append(equity)
                    continue
            # 持仓管理（仅当已持仓）
            side = pos["side"]; entry = pos["entry"]
            tp = entry * (1 + TP_PCT) if side == 1 else entry * (1 - TP_PCT)
            exit_px = None; reason = None; fee_out = TAKER
            if side == 1:
                if hq >= tp:
                    exit_px = tp; reason = "TP+1.5%"; fee_out = TAKER
            else:
                if lq <= tp:
                    exit_px = tp; reason = "TP-1.5%"; fee_out = TAKER
            if exit_px is None:
                jc = bucket_closed(ts4, t, FOUR_H_MS)
                if jc >= 0 and trend4[jc] is not None and trend4[jc] == -side:
                    exit_px = cq; reason = "4h反向止损"; fee_out = TAKER
            if exit_px is not None:
                fill = exit_px * (1 - SLIP) if side == 1 else exit_px * (1 + SLIP)
                fee = pos["units"] * fill * fee_out
                pnl = pos["units"] * (fill - entry) if side == 1 else pos["units"] * (entry - fill)
                net = pnl - fee
                equity += net
                pnl_pct = net / pos["notional"] * 100
                trades.append({"side": side, "entry_ts": pos["entry_ts"], "entry_px": pos["entry_px"],
                               "exit_ts": t, "exit_px": exit_px, "reason": reason, "pnl_pct": pnl_pct})
                pos = None
            # 标记权益曲线
            if pos is not None:
                unreal = pos["units"] * (cq - pos["entry"]) if pos["side"] == 1 else pos["units"] * (pos["entry"] - cq)
                curve.append(equity + unreal)
            else:
                curve.append(equity)
        # 结束平仓
        if pos is not None:
            q = n - 1; cq = c1[q]; t = ts1[q]
            side = pos["side"]; entry = pos["entry"]
            fill = cq * (1 - SLIP) if side == 1 else cq * (1 + SLIP)
            fee = pos["units"] * fill * TAKER
            pnl = pos["units"] * (fill - entry) if side == 1 else pos["units"] * (entry - fill)
            net = pnl - fee
            equity += net
            trades.append({"side": side, "entry_ts": pos["entry_ts"], "entry_px": pos["entry_px"],
                           "exit_ts": t, "exit_px": cq, "reason": "持仓至结束", "pnl_pct": net / pos["notional"] * 100})
            pos = None
        # 回撤
        peak = 10000.0; mdd = 0.0
        for v in curve:
            peak = max(peak, v); mdd = min(mdd, v / peak - 1)
        wins = sum(1 for tr in trades if tr["pnl_pct"] > 0)
        res = {"signals": len(sig_list), "trades": len(trades),
               "final_equity": equity, "total_return_pct": equity / 10000.0 * 100 - 100,
               "win_rate_pct": (wins / len(trades) * 100) if trades else 0,
               "avg_trade_pct": (sum(tr["pnl_pct"] for tr in trades) / len(trades)) if trades else 0,
               "max_drawdown_pct": mdd * 100, "trades_list": trades}
        print(f"\n{'='*78}\n  {label}  (信号 {res['signals']} / 成交 {res['trades']})\n{'='*78}")
        print(f"  最终净值={res['final_equity']:.2f}  总收益={res['total_return_pct']:.2f}%  "
              f"胜率={res['win_rate_pct']:.1f}%  平均单笔={res['avg_trade_pct']:.2f}%  "
              f"最大回撤={res['max_drawdown_pct']:.2f}%")
        print(f"  {'#':>3} {'方向':>4} {'入场时间':>16} {'入场价':>10} {'出场时间':>16} "
              f"{'出场价':>10} {'原因':>10} {'单笔%':>8}")
        for i, tr in enumerate(trades, 1):
            d = "多" if tr["side"] == 1 else "空"
            print(f"  {i:>3} {d:>4} {fmt(tr['entry_ts']):>16} {tr['entry_px']:>10.1f} "
                  f"{fmt(tr['exit_ts']):>16} {tr['exit_px']:>10.1f} {tr['reason']:>10} "
                  f"{tr['pnl_pct']:>+7.2f}%")
        return res

    run(signals, "策略：15m震荡 + 1h同4h方向 (TP1.5% / 4h反向止损)")

    # 参考基线：1h ST(10,3) 全部翻转（无过滤），同 TP1.5% + 同费用，便于对比
    base = [{"i": f["i"], "action": f["type"]} for f in flips1]
    print(f"\n{'#'*78}\n# 参考基线：1h ST(10,3) 全部翻转（不做任何过滤），TP1.5%+同费用\n{'#'*78}")
    run(base, "基线：1h全flip (TP1.5% / 仅TP平仓)")


if __name__ == "__main__":
    main()
