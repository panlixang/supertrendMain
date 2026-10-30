# -*- coding: utf-8 -*-
"""
形态识别页（pattern_trade.py）策略回测：1h SKHYNIX。

严格复现线上 43 形态识别页逻辑：
  - 基础信号：1h SuperTrend(10, 3.0, change_atr=True) 翻转，等收盘确认后下一根入场。
  - 过滤（开仓前）：
      block_4h    : 4h ST 方向与信号反向则拦截（默认开）
      filter_v3   : signal_v3.features_from_candles + v3_decide，不放行则不开（SKHYNIX 默认开）
  - 出场（单档 ExitRules，SKHYNIX 配置）：
      tp1=1.5% 平 70% + 保本 + 跟随 ST 线 trail + 同周期反向 flip 平仓剩余 + pct 3% 硬止损

输出：线上真实配置（v3+ST+block4h）主结果 + 三个对照（关掉某个过滤看贡献）。
"""
import os, sys, json, sqlite3, bisect
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from indicators import super_trend
import signal_v3

DB = os.path.join(HERE, "candle_data.db")
ST_P, ST_M = 10, 3.0

# SKHYNIX 在 pattern_trade.json 中的配置
CFG = dict(
    tp1_pct=1.5, tp1_ratio=70.0,
    sl_pct=3.0, sl_mode="pct",
    move_sl_to_entry=True, trail_with_st=True, reverse_close=False,
    filter_v3=True, block_4h=True,
    leverage=10,
)


def load_tf(sym, tf):
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT ts,o,h,l,c,vol FROM candles WHERE symbol=? AND tf=? ORDER BY ts",
        (sym, tf)).fetchall()
    con.close()
    return [{"ts": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "vol": r[5]} for r in rows]


def build_st(candles):
    o = [x["o"] for x in candles]; h = [x["h"] for x in candles]
    l = [x["l"] for x in candles]; c = [x["c"] for x in candles]
    return super_trend(o, h, l, c, periods=ST_P, multiplier=ST_M, change_atr=True)


def simulate(side, entry_i, exit_i, h1, up, dn, trend):
    """逐根模拟一笔持仓的出场，返回 (realized_frac, reason, tp1_hit, bars_held)。
    realized_frac: 整仓加权价格幅度（小数，顺方向为正）。"""
    long = (side == "buy")
    entry = h1[entry_i]["c"]
    stop = entry * (1 - CFG["sl_pct"] / 100) if long else entry * (1 + CFG["sl_pct"] / 100)
    tp1_price = entry * (1 + CFG["tp1_pct"] / 100) if long else entry * (1 - CFG["tp1_pct"] / 100)
    tp1_done = False
    realized = 0.0
    exit_reason = "eod"

    for k in range(entry_i + 1, exit_i + 1):
        bar = h1[k]
        lo, hi, cl = bar["l"], bar["h"], bar["c"]
        # trail：跟随 ST 线（持仓同向轨道），只朝有利方向移动 stop
        if CFG["trail_with_st"]:
            line = up[k] if long else dn[k]
            if line is not None:
                if (long and line > stop) or (not long and line < stop):
                    stop = line
        # 硬止损优先
        if (long and lo <= stop) or (not long and hi >= stop):
            if not tp1_done:
                realized = (stop - entry) / entry if long else (entry - stop) / entry
            else:
                realized += 0.3 * ((stop - entry) / entry if long else (entry - stop) / entry)
            exit_reason = "stop"; return realized, exit_reason, tp1_done, k - entry_i
        # TP1
        if (not tp1_done) and ((long and hi >= tp1_price) or (not long and lo <= tp1_price)):
            realized += 0.7 * ((tp1_price - entry) / entry if long else (entry - tp1_price) / entry)
            tp1_done = True
            stop = entry  # 保本
    # 到达 exit_i（反向 flip 那根）收盘平仓
    if exit_reason == "eod":
        px = h1[exit_i]["c"]
        if not tp1_done:
            realized = (px - entry) / entry if long else (entry - px) / entry
        else:
            realized += 0.3 * ((px - entry) / entry if long else (entry - px) / entry)
        exit_reason = "reverse"
    return realized, exit_reason, tp1_done, exit_i - entry_i


def backtest(h1, h4, st1, st4, use_v3, use_4h):
    trend1 = st1["trend"]; up = st1["up"]; dn = st1["dn"]
    flips = sorted(st1["flips"], key=lambda f: f["i"])
    trend4 = st4["trend"]; ts4 = [x["ts"] for x in h4]
    n = len(h1)
    if n < 60:
        return None

    trades = []
    in_pos = False
    pending = None  # (side, entry_i)
    for f in flips:
        i = f["i"]
        if i is None or i >= n or i < 50:
            # i<50 时 v3 特征不足放行；但该 flip 仍可能是反向平仓点（仅当在仓）
            pass
        side = f["type"]
        if in_pos:
            # 此 flip 为反向 → 平仓
            entry_i = pending[1]
            exit_i = i if i < n else n - 1
            if exit_i < entry_i + 1:
                exit_i = entry_i + 1
            r, reason, tp1, held = simulate(pending[0], entry_i, exit_i, h1, up, dn, trend1)
            trades.append(dict(side=pending[0], entry=h1[entry_i]["c"], exit=h1[exit_i]["c"],
                               ret=r, reason=reason, tp1=tp1, held=held,
                               ets=h1[entry_i]["ts"], xts=h1[exit_i]["ts"]))
            in_pos = False
            pending = None
            continue
        # 候选开仓
        sig_dir = 1 if side == "buy" else -1
        if use_4h:
            j4 = bisect.bisect_right(ts4, h1[i]["ts"]) - 1
            if 0 <= j4 < len(trend4) and trend4[j4] is not None:
                if trend4[j4] == -sig_dir:  # 4h 反向拦截
                    continue
        if use_v3:
            sd = sig_dir
            feats = signal_v3.features_from_candles(h1, i, sd, h4,
                                                    st_periods=ST_P, st_mult=ST_M)
            if feats:
                v = signal_v3.v3_decide(sd, feats)
                if not v["execute"]:
                    continue
        # 开仓
        pending = (side, i)
        in_pos = True

    # 持仓到末尾强制平
    if in_pos and pending:
        entry_i = pending[1]
        exit_i = n - 1
        r, reason, tp1, held = simulate(pending[0], entry_i, exit_i, h1, up, dn, trend1)
        trades.append(dict(side=pending[0], entry=h1[entry_i]["c"], exit=h1[exit_i]["c"],
                           ret=r, reason=reason, tp1=tp1, held=held,
                           ets=h1[entry_i]["ts"], xts=h1[exit_i]["ts"]))

    return trades


def summarize(trades, lev, label):
    if not trades:
        print(f"\n[{label}] 无交易")
        return
    eq = 1.0          # 价格幅度口径（1x 满仓复利）
    eq_roe = 1.0      # ROE 口径（×leverage）
    peak = 1.0
    maxdd = 0.0
    peak_roe = 1.0
    maxdd_roe = 0.0
    wins = 0
    gross_win = 0.0
    gross_loss = 0.0
    tp1_cnt = 0
    reason_cnt = defaultdict(int)
    for t in trades:
        r = t["ret"]
        eq *= (1 + r)
        eq_roe *= (1 + r * lev)
        peak = max(peak, eq); maxdd = max(maxdd, 1 - eq / peak)
        peak_roe = max(peak_roe, eq_roe); maxdd_roe = max(maxdd_roe, 1 - eq_roe / peak_roe)
        if r > 0:
            wins += 1; gross_win += r
        else:
            gross_loss += -r
        if t["tp1"]:
            tp1_cnt += 1
        reason_cnt[t["reason"]] += 1

    n = len(trades)
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    winrate = wins / n * 100
    cum_pct = (eq - 1) * 100
    cum_roe = (eq_roe - 1) * 100
    avg_r = sum(t["ret"] for t in trades) / n * 100
    print(f"\n=== {label} ===")
    print(f"  笔数={n}  胜率={winrate:.1f}%  PF={pf if pf != float('inf') else 'inf'}")
    print(f"  累计(价格幅度/1x满仓)={cum_pct:+.2f}%   累计(ROE×{lev})={cum_roe:+.2f}%")
    print(f"  每笔均值={avg_r:+.2f}%   最大回撤(1x)={maxdd*100:.2f}%   最大回撤(ROE)={maxdd_roe*100:.2f}%")
    print(f"  TP1命中={tp1_cnt}/{n}   平仓原因={dict(reason_cnt)}")
    return dict(n=n, win=winrate, pf=pf, cum=cum_pct, cum_roe=cum_roe,
                dd=maxdd_roe*100, tp1=tp1_cnt, reasons=dict(reason_cnt))


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "SKHYNIX"
    con = sqlite3.connect(DB)
    syms = [r[0] for r in con.execute(
        "SELECT DISTINCT symbol FROM candles WHERE symbol LIKE ?",
        ('%' + arg + '%',)).fetchall()]
    con.close()
    print(f"db 中 {arg} 符号:", syms)
    if not syms:
        print("未找到数据"); return
    sym = syms[0]
    h1 = load_tf(sym, "1h")
    h4 = load_tf(sym, "4h")
    n = len(h1)
    t0 = h1[0]["ts"] if n else 0
    t1 = h1[-1]["ts"] if n else 0
    import datetime as dt
    def fmt(ts): return dt.datetime.utcfromtimestamp(ts/1000).strftime("%Y-%m-%d")
    print(f"品种={sym}  1h根数={n} ({fmt(t0)} ~ {fmt(t1)})  4h根数={len(h4)}")

    st1 = build_st(h1)
    st4 = build_st(h4)
    print(f"1h ST flips={len(st1['flips'])}  4h ST flips={len(st4['flips'])}")

    print("\n" + "=" * 70)
    print(f"形态识别页 1h {sym} 回测（ST 10x3.0, 出场 TP1 1.5%/70%+保本+trail+pct3%）")
    print("=" * 70)

    # 主结果：线上真实（v3 + block_4h）
    tr = backtest(h1, h4, st1, st4, use_v3=True, use_4h=True)
    summarize(tr, CFG["leverage"], "线上真实 (v3 + block_4h)")

    # 对照1：仅 ST，无 v3 无 4h
    tr0 = backtest(h1, h4, st1, st4, use_v3=False, use_4h=False)
    summarize(tr0, CFG["leverage"], "仅 ST 信号 (无 v3 / 无 4h 过滤)")

    # 对照2：ST + block_4h，无 v3
    tr1 = backtest(h1, h4, st1, st4, use_v3=False, use_4h=True)
    summarize(tr1, CFG["leverage"], "ST + block_4h (无 v3)")

    # 对照3：ST + v3，无 4h
    tr2 = backtest(h1, h4, st1, st4, use_v3=True, use_4h=False)
    summarize(tr2, CFG["leverage"], "ST + v3 (无 block_4h)")

    # 明细（主结果前10 + 后10）
    if tr:
        print("\n--- 交易明细（线上真实，前10笔）---")
        for t in tr[:10]:
            print(f"  {t['side']:>4} 入{t['entry']:.4f} 出{t['exit']:.4f} "
                  f"收益{t['ret']*100:+.2f}% [{t['reason']}] tp1={t['tp1']} 持仓{t['held']}根")
        if len(tr) > 20:
            print("  ...")
            for t in tr[-10:]:
                print(f"  {t['side']:>4} 入{t['entry']:.4f} 出{t['exit']:.4f} "
                      f"收益{t['ret']*100:+.2f}% [{t['reason']}] tp1={t['tp1']} 持仓{t['held']}根")


if __name__ == "__main__":
    main()
