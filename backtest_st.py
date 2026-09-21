"""
BTC 超级趋势(10x3) 回测 + 移动止损 + Gate 过滤对比
- 信号源: 项目 indicators.super_trend (periods=10, multiplier=3.0, hl2, changeATR)
- 仓位管理(两版相同):
    翻转入场满仓 -> 涨1.5%平30%且止损移到开仓价(保本)
                  -> 涨3%平40%且止损移到TP1价
                  -> 剩余30%跑单: 触及止损价 或 反向信号(翻转)平仓并翻仓
- 版本A 不过滤: 每个 SuperTrend flip 都是信号
- 版本B 带Gate过滤: 只在本页 Gate 全通过(<=阈值)的 flip 上动作
- 数据: Binance BTCUSDT klines 近180天 15m / 1h
"""
import sys, json, time, urllib.request
from datetime import datetime, timezone

BACKEND = r"d:/个人项目代码/supertrendMain/backend"
sys.path.insert(0, BACKEND)
from indicators import super_trend

# ── 可调参数 ──
PERIODS    = 10
MULT       = 3.0
SRC        = "hl2"
CHANGE_ATR = True
DAYS       = 180
TP1_PCT    = 0.010   # 1.0%
TP2_PCT    = 0.020   # 2.0%
TP1_FRAC   = 0.30    # 平30%
TP2_FRAC   = 0.40    # 平40%  (剩30%跑单)

DEFAULT_GATE = {
    "gateA": {"enabled": True, "bodyAtrThreshold": 1.5},
    "gateB": {"enabled": True, "densityThreshold": 0.15, "lookback": 20},
    "gateC": {"enabled": True, "wickRatioThreshold": 0.6},
    "gateD": {"enabled": True, "trendAgeThreshold": 50},
    "gateE": {"enabled": True, "gapRatioThreshold": 0.5},
}


# ── 拉 Binance 数据 ──
def fetch_klines(symbol, interval, days):
    interval_ms = {"15m": 15 * 60 * 1000, "1h": 60 * 60 * 1000}[interval]
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86400 * 1000
    base = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit=1000"
    out = []
    cur = start_ms
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
                        "l": float(row[3]), "c": float(row[4]), "vol": float(row[5])})
        cur = rows[-1][0] + interval_ms
        time.sleep(0.03)
    return out


# ── 复刻研究页 ATR(SMA14) 与 Gate 特征 ──
def compute_atr(h, l, c, period=14):
    n = len(c)
    tr = [0.0] * (n - 1)
    for k in range(n - 1):
        i = k + 1
        tr[k] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    atr = [None] * n
    for i in range(n):
        if i < period - 1:
            atr[i] = None
        else:
            a, b = i - period + 1, i + 1
            seg = tr[a:b]
            atr[i] = sum(seg) / period
    return atr


def build_flips_with_features(candles, st):
    o = [x["o"] for x in candles]; h = [x["h"] for x in candles]
    l = [x["l"] for x in candles]; c = [x["c"] for x in candles]
    n = len(candles)
    atr = compute_atr(h, l, c, 14)
    raw = st["flips"]
    sigs = [{"action": f["type"], "i": f["i"], "ts": candles[f["i"]]["ts"], "price": c[f["i"]]}
            for f in raw]
    out = []
    for sig in sigs:
        i = sig["i"]
        atr_v = atr[i - 1] if (i - 1 >= 0 and atr[i - 1]) else 1.0
        if atr_v <= 0:
            atr_v = 1.0
        body = abs(c[i] - o[i])
        bodyAtrRatio = body / atr_v
        lookback = 20
        cnt = sum(1 for s2 in sigs if (i - lookback) <= s2["i"] < i)
        flipDensity = cnt / lookback
        upperWick = h[i] - max(o[i], c[i])
        lowerWick = min(o[i], c[i]) - l[i]
        totalWick = upperWick + lowerWick
        wickRatio = totalWick / body if body > 0 else 0.0
        trendAge = 1
        direction = "down" if sig["action"] == "buy" else "up"
        for k in range(i - 1, -1, -1):
            isDown = c[k] < o[k]
            if (direction == "down" and isDown) or (direction == "up" and not isDown):
                trendAge += 1
            else:
                break
        gapRatio = 0.0
        if i > 0:
            gap = (l[i] - h[i - 1]) if sig["action"] == "buy" else (l[i - 1] - h[i])
            gapRatio = max(0.0, gap) / atr_v
        out.append({**sig, "bodyAtrRatio": bodyAtrRatio, "flipDensity": flipDensity,
                    "wickRatio": wickRatio, "trendAge": trendAge, "gapRatio": gapRatio})
    return out


def passes_gate(f, cfg=DEFAULT_GATE):
    gA = (not cfg["gateA"]["enabled"]) or (f["bodyAtrRatio"] <= cfg["gateA"]["bodyAtrThreshold"])
    gB = (not cfg["gateB"]["enabled"]) or (f["flipDensity"] <= cfg["gateB"]["densityThreshold"])
    gC = (not cfg["gateC"]["enabled"]) or (f["wickRatio"] <= cfg["gateC"]["wickRatioThreshold"])
    gD = (not cfg["gateD"]["enabled"]) or (f["trendAge"] <= cfg["gateD"]["trendAgeThreshold"])
    gE = (not cfg["gateE"]["enabled"]) or (f["gapRatio"] <= cfg["gateE"]["gapRatioThreshold"])
    return gA and gB and gC and gD and gE


# ── OKX 永续费率 + 滑点 ──
TAKER_FEE = 0.0005   # 0.05% 开仓/市价平仓
MAKER_FEE = 0.0002   # 0.02% 限价止盈(TP)
SLIP      = 0.0003   # 0.03% 单边滑点(仅 taker 成交)


# ── 回测(给定信号序列) ──
def backtest(candles, signals, apply_fees=True, tp1_pct=TP1_PCT, tp2_pct=TP2_PCT):
    h = [x["h"] for x in candles]; l = [x["l"] for x in candles]
    c = [x["c"] for x in candles]
    n = len(c)
    ft = TAKER_FEE if apply_fees else 0.0
    fm = MAKER_FEE if apply_fees else 0.0
    sl = SLIP if apply_fees else 0.0
    TP1_PCT_ = tp1_pct; TP2_PCT_ = tp2_pct

    equity = 10000.0
    curve = []
    side = 0; entry = 0.0; entry_units = 0.0; remain = 0.0
    tp1 = tp2 = False; stop = None
    trades = []; tp1_hits = tp2_hits = 0; fees_paid = 0.0

    def open_pos(price, action):
        nonlocal equity, side, entry, entry_units, remain, tp1, tp2, stop, fees_paid
        sgn = 1 if action == "buy" else -1
        fill = price * (1 + sl) if sgn == 1 else price * (1 - sl)
        units = equity / fill
        notional = units * fill
        fee = notional * ft
        equity -= fee; fees_paid += fee
        side = sgn; entry = fill; entry_units = units; remain = units
        tp1 = tp2 = False; stop = None

    def close_partial(frac, price, is_taker):
        nonlocal equity, remain, fees_paid
        if side == 0 or remain <= 0:
            return
        sgn = side
        u = frac * entry_units
        fill = price * (1 - sl) if sgn == 1 else price * (1 + sl)
        notional = u * fill
        fee = notional * (ft if is_taker else fm)
        pnl = u * (fill - entry) if sgn == 1 else u * (entry - fill)
        equity += pnl - fee; fees_paid += fee
        remain -= u

    def close_full(price, is_taker):
        nonlocal equity, side, remain, entry_units, tp1, tp2, stop, fees_paid, trades
        if side == 0:
            return
        sgn = side
        fill = price * (1 - sl) if sgn == 1 else price * (1 + sl)
        notional = remain * fill
        fee = notional * (ft if is_taker else fm)
        pnl = remain * (fill - entry) if sgn == 1 else remain * (entry - fill)
        equity += pnl - fee; fees_paid += fee
        trades.append({"side": sgn, "pnl_pct": (pnl - fee) / (entry_units * entry) * 100})
        side = 0; remain = 0.0; entry_units = 0.0; tp1 = tp2 = False; stop = None

    def bar(j):
        nonlocal tp1, tp2, stop, tp1_hits, tp2_hits
        if side == 1:
            tp1p = entry * (1 + TP1_PCT_); tp2p = entry * (1 + TP2_PCT_)
            if not tp1 and h[j] >= tp1p:
                close_partial(TP1_FRAC, tp1p, False); tp1 = True; stop = entry; tp1_hits += 1
            if not tp2 and h[j] >= tp2p:
                close_partial(TP2_FRAC, tp2p, False); tp2 = True; stop = tp1p; tp2_hits += 1
            if stop is not None and l[j] <= stop:
                close_full(stop, True); return
        else:
            tp1p = entry * (1 - TP1_PCT_); tp2p = entry * (1 - TP2_PCT_)
            if not tp1 and l[j] <= tp1p:
                close_partial(TP1_FRAC, tp1p, False); tp1 = True; stop = entry; tp1_hits += 1
            if not tp2 and l[j] <= tp2p:
                close_partial(TP2_FRAC, tp2p, False); tp2 = True; stop = tp1p; tp2_hits += 1
            if stop is not None and h[j] >= stop:
                close_full(stop, True); return

    for k in range(len(signals)):
        sig = signals[k]
        ei = sig["i"]
        if sig.get("action") == "close":   # Market Filter 平仓信号: 只平不新开
            if side != 0:
                close_full(c[ei], True)
            curve.append(equity)
            continue
        if side != 0:                      # 反向信号 -> 平旧仓剩余(翻仓, 市价taker)
            close_full(c[ei], True)
        open_pos(c[ei], sig["action"])
        end = signals[k + 1]["i"] if k + 1 < len(signals) else n - 1
        for j in range(ei + 1, end + 1):
            if side == 0:
                break
            bar(j)
        unreal = 0.0
        if side != 0:
            unreal = remain * (c[ei] - entry) if side == 1 else remain * (entry - c[ei])
        curve.append(equity + unreal)

    if side != 0:
        close_full(c[-1], True)

    peak = 10000.0; mdd = 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    wins = sum(1 for t in trades if t["pnl_pct"] > 0)
    return {
        "signals": len(signals), "trades": len(trades),
        "tp1_hits": tp1_hits, "tp2_hits": tp2_hits,
        "final_equity": equity, "total_return_pct": equity / 10000.0 * 100 - 100,
        "win_rate_pct": (wins / len(trades) * 100) if trades else 0,
        "avg_trade_pct": (sum(t["pnl_pct"] for t in trades) / len(trades)) if trades else 0,
        "max_drawdown_pct": mdd * 100,
        "fees_pct": fees_paid / 10000.0 * 100,
        "bh_return_pct": (c[-1] / c[0] - 1) * 100,
    }


def main():
    print(f"费率: Taker={TAKER_FEE*100:.2f}% Maker={MAKER_FEE*100:.2f}% 滑点={SLIP*100:.2f}%/边(仅taker)")
    for interval in ["15m", "1h"]:
        k = fetch_klines("BTCUSDT", interval, DAYS)
        st = super_trend([x["o"] for x in k], [x["h"] for x in k],
                         [x["l"] for x in k], [x["c"] for x in k],
                         PERIODS, MULT, SRC, CHANGE_ATR)
        feats = build_flips_with_features(k, st)
        all_sigs = [{"action": f["action"], "i": f["i"]} for f in feats]
        gated = [f for f in feats if passes_gate(f)]
        gated_sigs = [{"action": f["action"], "i": f["i"]} for f in gated]
        print(f"\n{'='*70}\n BTCUSDT {interval}  ST(10,3.0)  近{DAYS}天  (K线 {len(k)} 根)\n{'='*70}")
        print(f"  flip总数={len(all_sigs)}  Gate通过={len(gated_sigs)}  "
              f"过滤率={(1-len(gated_sigs)/max(1,len(all_sigs)))*100:.0f}%  "
              f"BTC买入持有={(k[-1]['c']/k[0]['c']-1)*100:.2f}%")
        for label, sigs in [("不过滤(全部flip)", all_sigs), ("带 Gate 过滤", gated_sigs)]:
            rg = backtest(k, sigs, apply_fees=False)
            rn = backtest(k, sigs, apply_fees=True)
            print(f"\n  ── {label} ──")
            _p(rg, "无费用")
            _p(rn, f"含费用(滑点)")


def _p(r, tag):
    print(f"     [{tag}] 信号/头寸: {r['signals']}/{r['trades']}  TP1={r['tp1_hits']} TP2={r['tp2_hits']}")
    print(f"           最终净值: {r['final_equity']:.2f}  总收益: {r['total_return_pct']:.2f}%  "
          f"(费用占初始 {r['fees_pct']:.2f}%)")
    print(f"           胜率: {r['win_rate_pct']:.1f}%  平均单笔: {r['avg_trade_pct']:.2f}%  "
          f"最大回撤: {r['max_drawdown_pct']:.2f}%")


def run_grid(k, sigs):
    """15m 带Gate + 费率滑点下，网格搜索最优 TP1/TP2，返回按净收益降序的 rows"""
    tp1_list = [0.005, 0.0075, 0.010, 0.0125, 0.015, 0.020, 0.025]
    tp2_list = [0.015, 0.020, 0.025, 0.030, 0.040, 0.050, 0.060]
    rows = []
    for t1 in tp1_list:
        for t2 in tp2_list:
            if t2 <= t1 + 0.002:
                continue
            r = backtest(k, sigs, apply_fees=True, tp1_pct=t1, tp2_pct=t2)
            rows.append((t1, t2, r))
    rows.sort(key=lambda x: x[2]["total_return_pct"], reverse=True)
    return rows


def sweep_tp(k, sigs):
    rows = run_grid(k, sigs)
    print(f"\n{'='*70}\n  TP1/TP2 网格搜索 (15m 带Gate, 含费率+滑点)  共 {len(rows)} 组\n{'='*70}")
    print(f"  {'TP1':>6} {'TP2':>6} {'净收益':>9} {'回撤':>8} {'胜率':>7} {'TP1命中':>7} {'TP2命中':>7}")
    for t1, t2, r in rows[:12]:
        print(f"  {t1*100:5.2f}% {t2*100:5.2f}% {r['total_return_pct']:8.2f}% "
              f"{r['max_drawdown_pct']:7.2f}% {r['win_rate_pct']:6.1f}% "
              f"{r['tp1_hits']:7d} {r['tp2_hits']:7d}")
    return rows


def walk_forward(symbol="BTCUSDT"):
    """前90天寻参 / 后90天验证 (15m 带Gate, 含费率+滑点)"""
    k = fetch_klines(symbol, "15m", DAYS)
    st = super_trend([x["o"] for x in k], [x["h"] for x in k],
                     [x["l"] for x in k], [x["c"] for x in k],
                     PERIODS, MULT, SRC, CHANGE_ATR)
    split = len(k) // 2
    feats = build_flips_with_features(k, st)   # 全序列算特征，避免 ST/特征跨窗口失真
    sig_tr = [{"action": f["action"], "i": f["i"]}
              for f in feats if f["i"] < split and passes_gate(f)]
    sig_te = [{"action": f["action"], "i": f["i"] - split}
              for f in feats if f["i"] >= split and passes_gate(f)]
    ktr, kte = k[:split], k[split:]

    rows = run_grid(ktr, sig_tr)
    bt1, bt2, r_tr = rows[0]
    r_te = backtest(kte, sig_te, apply_fees=True, tp1_pct=bt1, tp2_pct=bt2)

    refs = {}
    for name, (a, b) in [("训练选出最优", (bt1, bt2)),
                         ("2.0/4.0", (0.02, 0.04)),
                         ("1.5/3.0", (0.015, 0.03)),
                         ("1.0/2.0", (0.01, 0.02))]:
        refs[name] = backtest(kte, sig_te, apply_fees=True, tp1_pct=a, tp2_pct=b)

    print(f"\n{'='*70}\n  WALK-FORWARD  {symbol} 15m 带Gate 含费率+滑点\n  前90天 K线 {len(ktr)} (信号 {len(sig_tr)})  后90天 K线 {len(kte)} (信号 {len(sig_te)})\n{'='*70}")
    print(f"  训练段选出: TP1={bt1*100:.2f}% TP2={bt2*100:.2f}%  -> 训练净收益 {r_tr['total_return_pct']:.2f}%  回撤 {r_tr['max_drawdown_pct']:.2f}%")
    print(f"\n  —— 该参数在后90天(样本外)验证 ——")
    print(f"   {'配置':<14} {'净收益':>9} {'回撤':>8} {'胜率':>7} {'TP1':>5} {'TP2':>5}")
    for name, r in refs.items():
        mark = "  <== 训练选出" if name == "训练选出最优" else ""
        print(f"   {name:<14} {r['total_return_pct']:8.2f}% {r['max_drawdown_pct']:7.2f}% "
              f"{r['win_rate_pct']:6.1f}% {r['tp1_hits']:5d} {r['tp2_hits']:5d}{mark}")
    print(f"\n  {symbol}后90天买入持有: {(kte[-1]['c']/kte[0]['c']-1)*100:.2f}%")


def analyze(symbol):
    print(f"\n\n{'#'*72}\n#  {symbol}  15m  ST(10,3.0)  研究页Gate  OKX费率+滑点\n{'#'*72}")
    k = fetch_klines(symbol, "15m", DAYS)
    st = super_trend([x["o"] for x in k], [x["h"] for x in k],
                     [x["l"] for x in k], [x["c"] for x in k],
                     PERIODS, MULT, SRC, CHANGE_ATR)
    feats = build_flips_with_features(k, st)
    all_sigs = [{"action": f["action"], "i": f["i"]} for f in feats]
    gated_sigs = [{"action": f["action"], "i": f["i"]} for f in feats if passes_gate(f)]
    r_all = backtest(k, gated_sigs, apply_fees=True)
    print(f"  flip总数={len(all_sigs)}  Gate通过={len(gated_sigs)}  "
          f"过滤率={(1-len(gated_sigs)/max(1,len(all_sigs)))*100:.0f}%  "
          f"{symbol}买入持有={(k[-1]['c']/k[0]['c']-1)*100:.2f}%")
    print(f"  带Gate 含费用: 净收益 {r_all['total_return_pct']:.2f}%  回撤 {r_all['max_drawdown_pct']:.2f}%  "
          f"胜率 {r_all['win_rate_pct']:.1f}%")
    print(f"\n  ── TP1/TP2 全180天网格 (含费用) ──")
    sweep_tp(k, gated_sigs)
    print(f"\n  ── Walk-forward (前90天寻参/后90天验证) ──")
    walk_forward(symbol)


def scan_symbols(symbols):
    """跨币种检验: 同一套 Gate 过滤 + BTC最优TP(2.0/4.0) + 费率滑点, 看是否通用"""
    print(f"\n\n{'#'*72}\n#  跨币种检验  15m  ST(10,3.0)  研究页Gate  OKX费率+滑点\n#  "
          f"固定列用 BTC最优 TP1=2.0%/TP2=4.0%; 最优列=该币种自身网格最优\n{'#'*72}")
    print(f"  {'币种':<10} {'Gate信号':>8} {'买入持有':>9} {'净@2/4':>9} {'最优净':>9} "
          f"{'最优TP':>9} {'胜率':>6} {'回撤':>8}")
    for sym in symbols:
        try:
            k = fetch_klines(sym, "15m", DAYS)
            st = super_trend([x["o"] for x in k], [x["h"] for x in k],
                             [x["l"] for x in k], [x["c"] for x in k],
                             PERIODS, MULT, SRC, CHANGE_ATR)
            feats = build_flips_with_features(k, st)
            gated = [{"action": f["action"], "i": f["i"]} for f in feats if passes_gate(f)]
            r_fixed = backtest(k, gated, apply_fees=True, tp1_pct=0.02, tp2_pct=0.04)
            rows = run_grid(k, gated)
            bt1, bt2, r_best = rows[0]
            bh = (k[-1]["c"] / k[0]["c"] - 1) * 100
            print(f"  {sym:<10} {len(gated):>8} {bh:>8.1f}% {r_fixed['total_return_pct']:>8.1f}% "
                  f"{r_best['total_return_pct']:>8.1f}% {bt1*100:>4.1f}/{bt2*100:>4.1f} "
                  f"{r_best['win_rate_pct']:>5.1f}% {r_best['max_drawdown_pct']:>7.1f}%")
        except Exception as e:
            print(f"  {sym:<10} 跳过: {e}")


def compare_filtered(symbol="BTCUSDT"):
    """同一币种: Gate过滤 vs 不过滤, 各自回测收益 + 最优TP1/TP2"""
    k = fetch_klines(symbol, "15m", DAYS)
    st = super_trend([x["o"] for x in k], [x["h"] for x in k],
                     [x["l"] for x in k], [x["c"] for x in k],
                     PERIODS, MULT, SRC, CHANGE_ATR)
    feats = build_flips_with_features(k, st)
    all_sigs = [{"action": f["action"], "i": f["i"]} for f in feats]
    gated_sigs = [{"action": f["action"], "i": f["i"]} for f in feats if passes_gate(f)]
    bh = (k[-1]["c"] / k[0]["c"] - 1) * 100
    print(f"\n\n{'#'*72}\n#  {symbol} 15m  ST(10,3.0)  含OKX费率+滑点  过滤 vs 不过滤\n{'#'*72}")
    print(f"  flip总数={len(all_sigs)}  Gate通过={len(gated_sigs)}  "
          f"过滤率={(1-len(gated_sigs)/max(1,len(all_sigs)))*100:.0f}%  买入持有={bh:.2f}%")
    for label, sigs in [("不过滤(全部flip)", all_sigs), ("带 Gate 过滤", gated_sigs)]:
        rows = run_grid(k, sigs)
        r24 = backtest(k, sigs, apply_fees=True, tp1_pct=0.02, tp2_pct=0.04)
        b = rows[0]
        print(f"\n  ── {label}  (信号 {len(sigs)}) ──")
        print(f"     固定 TP1=2.0%/TP2=4.0% 净收益 {r24['total_return_pct']:.2f}%  回撤 {r24['max_drawdown_pct']:.2f}%  胜率 {r24['win_rate_pct']:.1f}%")
        print(f"     网格最优: TP1={b[0]*100:.2f}% TP2={b[1]*100:.2f}%  净收益 {b[2]['total_return_pct']:.2f}%  回撤 {b[2]['max_drawdown_pct']:.2f}%  胜率 {b[2]['win_rate_pct']:.1f}%")
        print(f"     {'TP1':>6} {'TP2':>6} {'净收益':>9} {'回撤':>8} {'胜率':>7} {'TP1命中':>7} {'TP2命中':>7}")
        for t1, t2, r in rows[:6]:
            print(f"     {t1*100:5.2f}% {t2*100:5.2f}% {r['total_return_pct']:8.2f}% "
                  f"{r['max_drawdown_pct']:7.2f}% {r['win_rate_pct']:6.1f}% "
                  f"{r['tp1_hits']:7d} {r['tp2_hits']:7d}")


if __name__ == "__main__":
    compare_filtered("BTCUSDT")
