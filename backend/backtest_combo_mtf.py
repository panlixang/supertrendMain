"""综合指标 · 多周期共振回测 + 参数扫描（本地只读，OKX 公共行情）。

共振两种模式（--mode）：
  hard  : 硬过滤——小周期方向必须与大小周期方向一致才交易（方向不一致直接砍）。
  blend : 打分叠加——大周期连续打分按 BIG_WEIGHT 叠进小周期综合分，仅当大周期
          明确逆势(|bigScore|>BIG_BLOCK)时禁止反向开仓。

出场两种逻辑：
  固定(SL/TP) : 入场 ± slMult×ATR 硬止损、± tpMult×ATR 固定止盈，外加信号反转出场。
  移动止损     : --trail >0 时启用，去掉固定止盈与信号出场，只用「入场硬止损 +
                trailing stop(峰值回撤 trailMult×ATR)」，让利润奔跑、避免趋势市被洗。

盈亏按固定名义 $10k/笔、扣 OKX taker 单边 0.05%。扫描输出复利曲线与买入持有对照。

用法:
  python backtest_combo_mtf.py BTC-USDT --big 4H --small 1H --limit 22000
  python backtest_combo_mtf.py BTC-USDT --big 4H --small 1H --limit 22000 --scan --mode hard
  python backtest_combo_mtf.py BTC-USDT --big 4H --small 1H --limit 22000 --scan --mode hard --trail-grid 1.0,1.5,2.0,2.5,3.0
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
import sys
import time
from collections import Counter

import history

COMBO_DEFAULTS = dict(
    fastLen=20, slowLen=60, rsiLen=14, atrLen=14, adxLen=14, volLen=20,
    adxMin=20, buyThreshold=0.35, sellThreshold=-0.35,
    rsiLongMin=45, rsiLongMax=72, rsiShortMin=28, rsiShortMax=55,
    atrPctMin=0.004, slMult=2.0, tpMult=2.0,
)

NOTIONAL = 10_000.0
FEE = 0.05 / 100          # taker 单边
TF_MIN = {'1m': 1, '3m': 3, '5m': 5, '15m': 15, '30m': 30, '1H': 60,
          '2H': 120, '4H': 240, '6H': 360, '12H': 720, '1D': 1440, '1W': 10080}

GRID = dict(
    buyThreshold=[0.20, 0.25, 0.30, 0.35, 0.40, 0.45],
    slMult=[1.5, 2.0, 2.5, 3.0],
    tpMult=[1.5, 2.0, 2.5, 3.0],
    adxMin=[15, 20, 25, 30],
    bigWeight=[0.2, 0.4, 0.6],
    bigBlock=[0.10, 0.20, 0.30],
)
BIG_WEIGHT_DEFAULT = 0.3
BIG_BLOCK_DEFAULT = 0.2

W_TREND, W_X, W_MOM, W_VOL, W_VOLAT = 0.30, 0.10, 0.25, 0.15, 0.10


def norm_tf(tf):
    return tf.replace('H', 'h').replace('D', 'd').replace('W', 'w')


# ───────────────────────── 指标 ─────────────────────────
def ema(vals, p):
    k = 2 / (p + 1)
    out = [None] * len(vals)
    prev = None
    for i, v in enumerate(vals):
        if v is None:
            out[i] = prev
            continue
        prev = v if prev is None else v * k + prev * (1 - k)
        out[i] = prev
    return out


def sma(vals, p):
    out = [None] * len(vals)
    if p <= 0:
        return out
    s = 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= p:
            s -= vals[i - p]
        if i >= p - 1:
            out[i] = s / p
    return out


def rma(vals, p):
    out = [None] * len(vals)
    if p <= 0:
        return out
    a = 1 / p
    prev = None
    for i, v in enumerate(vals):
        prev = v if prev is None else a * v + (1 - a) * prev
        out[i] = prev
    return out


def rsi(closes, p=14):
    n = len(closes)
    out = [None] * n
    if n < p + 1:
        return out
    gains = [0.0] * (n - 1)
    losses = [0.0] * (n - 1)
    for i in range(1, n):
        ch = closes[i] - closes[i - 1]
        gains[i - 1] = max(ch, 0.0)
        losses[i - 1] = max(-ch, 0.0)
    avgG = sum(gains[:p]) / p
    avgL = sum(losses[:p]) / p
    out[p] = 100.0 if avgL == 0 else 100 - 100 / (1 + avgG / avgL)
    for i in range(p, len(gains)):
        avgG = (avgG * (p - 1) + gains[i]) / p
        avgL = (avgL * (p - 1) + losses[i]) / p
        out[i + 1] = 100.0 if avgL == 0 else 100 - 100 / (1 + avgG / avgL)
    return out


def atr(highs, lows, closes, p=14):
    n = len(highs)
    out = [None] * n
    if n < 2:
        return out
    tr = [0.0] * n
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    return rma(tr, p)


def adx(highs, lows, closes, p=14):
    n = len(highs)
    adx_out = [None] * n
    pdi = [None] * n
    mdi = [None] * n
    if n < 2:
        return adx_out, pdi, mdi
    pDM = [0.0] * n
    mDM = [0.0] * n
    tr = [0.0] * n
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        dn = lows[i - 1] - lows[i]
        pDM[i] = up if (up > dn and up > 0) else 0.0
        mDM[i] = dn if (dn > up and dn > 0) else 0.0
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    trS = rma(tr, p)
    pDMS = rma(pDM, p)
    mDMS = rma(mDM, p)
    dx = [0.0] * n
    for i in range(n):
        pdi[i] = 100 * pDMS[i] / trS[i] if trS[i] else 0.0
        mdi[i] = 100 * mDMS[i] / trS[i] if trS[i] else 0.0
        s = pdi[i] + mdi[i]
        dx[i] = 100 * abs(pdi[i] - mdi[i]) / s if s else 0.0
    adxS = rma(dx, p)
    warm = 2 * p
    for i in range(n):
        adx_out[i] = adxS[i] if i >= warm else None
        pdi[i] = pdi[i] if i >= warm else None
        mdi[i] = mdi[i] if i >= warm else None
    return adx_out, pdi, mdi


# ───────────────────────── 预计算底层指标 ─────────────────────────
def precompute(candles, o):
    n = len(candles)
    closes = [c.c for c in candles]
    highs = [c.h for c in candles]
    lows = [c.l for c in candles]
    vols = [c.vol for c in candles]
    ts = [c.ts for c in candles]
    ef = ema(closes, o['fastLen'])
    es = ema(closes, o['slowLen'])
    r = rsi(closes, o['rsiLen'])
    a = atr(highs, lows, closes, o['atrLen'])
    adv, _, _ = adx(highs, lows, closes, o['adxLen'])
    vma = sma(vols, o['volLen'])
    return dict(closes=closes, highs=highs, lows=lows, vols=vols, ts=ts,
                ef=ef, es=es, r=r, a=a, adv=adv, vma=vma, n=n)


def warmup(o):
    return max(o['slowLen'], 2 * o['adxLen']) + 10


def compute_raw_score(pc, o):
    n = pc['n']
    closes, vols = pc['closes'], pc['vols']
    ef, es, r, a, adv, vma = pc['ef'], pc['es'], pc['r'], pc['a'], pc['adv'], pc['vma']
    score = [0.0] * n
    W = warmup(o)
    for i in range(1, n):
        if i < W:
            continue
        efi, esi, ri, ai, advi, vmai, vi = ef[i], es[i], r[i], a[i], adv[i], vma[i], vols[i]
        if None in (efi, esi, ri, ai, advi, vmai):
            continue
        trendUp = efi > esi
        trendDown = efi < esi
        golden = efi > esi and ef[i - 1] <= es[i - 1]
        death = efi < esi and ef[i - 1] >= es[i - 1]
        adxOK = advi >= o['adxMin']
        volOK = vi > vmai
        atrPct = ai / closes[i]
        s = 0.0
        if adxOK:
            if trendUp:
                s += W_TREND
            elif trendDown:
                s -= W_TREND
            if golden:
                s += W_X
            if death:
                s -= W_X
        s += ((ri - 50) / 50) * W_MOM
        s += W_VOL if volOK else -0.10
        if atrPct >= o['atrPctMin']:
            s += W_VOLAT
        score[i] = s
    return score


def align_big_to_small(big_arr, big_candles, small_candles):
    bt = [c.ts for c in big_candles]
    out = [0.0] * len(small_candles)
    for i, c in enumerate(small_candles):
        j = bisect.bisect_right(bt, c.ts) - 1
        out[i] = big_arr[j] if j >= 0 else 0.0
    return out


# ───────────────────────── 持仓生成 ─────────────────────────
def _open_pos(positions, dirn, i, closes, ai, o):
    ep = closes[i]
    if dirn == 'buy':
        return dict(entryI=i, exitI=None, dir='buy', entryPrice=ep,
                    stop=ep - o['slMult'] * ai, tp=ep + o['tpMult'] * ai, atr=ai)
    return dict(entryI=i, exitI=None, dir='sell', entryPrice=ep,
                stop=ep + o['slMult'] * ai, tp=ep - o['tpMult'] * ai, atr=ai)


def combo_positions(pc, o):
    n = pc['n']
    closes, highs, lows, vols, ts = pc['closes'], pc['highs'], pc['lows'], pc['vols'], pc['ts']
    ef, es, r, a, adv, vma = pc['ef'], pc['es'], pc['r'], pc['a'], pc['adv'], pc['vma']
    positions = []
    pos = 0
    cur = None
    W = warmup(o)
    score = compute_raw_score(pc, o)
    for i in range(1, n):
        if i < W:
            continue
        efi, esi, ri, ai, advi, vmai, vi = ef[i], es[i], r[i], a[i], adv[i], vma[i], vols[i]
        if None in (efi, esi, ri, ai, advi, vmai):
            continue
        trendUp = efi > esi
        trendDown = efi < esi
        golden = efi > esi and ef[i - 1] <= es[i - 1]
        death = efi < esi and ef[i - 1] >= es[i - 1]
        adxOK = advi >= o['adxMin']
        volOK = vi > vmai
        s = score[i]
        rsiLongOK = o['rsiLongMin'] <= ri <= o['rsiLongMax']
        rsiShortOK = o['rsiShortMin'] <= ri <= o['rsiShortMax']
        if pos == 0:
            if s >= o['buyThreshold'] and trendUp and adxOK and rsiLongOK and volOK:
                cur = _open_pos(positions, 'buy', i, closes, ai, o)
                positions.append(cur)
                pos = 1
            elif s <= o['sellThreshold'] and trendDown and adxOK and rsiShortOK and volOK:
                cur = _open_pos(positions, 'sell', i, closes, ai, o)
                positions.append(cur)
                pos = -1
        elif pos == 1:
            if trendDown or death or s <= -0.05:
                cur['exitI'] = i
                cur = None
                pos = 0
        elif pos == -1:
            if trendUp or golden or s >= 0.05:
                cur['exitI'] = i
                cur = None
                pos = 0
    return positions


def trend_regime(pc, o):
    ef, es, adv = pc['ef'], pc['es'], pc['adv']
    n = pc['n']
    out = [0] * n
    for i in range(n):
        if ef[i] is None or es[i] is None or adv[i] is None:
            out[i] = 0
        elif adv[i] >= o['adxMin']:
            out[i] = 1 if ef[i] > es[i] else (-1 if ef[i] < es[i] else 0)
        else:
            out[i] = 0
    return out


def resonant_positions(small_positions, big_regime, small_candles, big_candles):
    bt = [c.ts for c in big_candles]
    kept = []
    dropped = 0
    for p in small_positions:
        entry_ts = small_candles[p['entryI']].ts
        j = bisect.bisect_right(bt, entry_ts) - 1
        reg = big_regime[j] if j >= 0 else 0
        if (p['dir'] == 'buy' and reg == 1) or (p['dir'] == 'sell' and reg == -1):
            kept.append(p)
        else:
            dropped += 1
    return kept, dropped


def combo_positions_blend(small_pc, big_score_aligned, o, BIG_WEIGHT, BIG_BLOCK):
    n = small_pc['n']
    closes, highs, lows, vols = small_pc['closes'], small_pc['highs'], small_pc['lows'], small_pc['vols']
    ef, es, r, a, adv, vma = small_pc['ef'], small_pc['es'], small_pc['r'], small_pc['a'], small_pc['adv'], small_pc['vma']
    small_score = compute_raw_score(small_pc, o)
    positions = []
    pos = 0
    cur = None
    W = warmup(o)
    for i in range(1, n):
        if i < W:
            continue
        efi, esi, ri, ai, advi, vmai, vi = ef[i], es[i], r[i], a[i], adv[i], vma[i], vols[i]
        if None in (efi, esi, ri, ai, advi, vmai):
            continue
        trendUp = efi > esi
        trendDown = efi < esi
        golden = efi > esi and ef[i - 1] <= es[i - 1]
        death = efi < esi and ef[i - 1] >= es[i - 1]
        adxOK = advi >= o['adxMin']
        volOK = vi > vmai
        bscore = big_score_aligned[i]
        combo = small_score[i] + BIG_WEIGHT * bscore
        rsiLongOK = o['rsiLongMin'] <= ri <= o['rsiLongMax']
        rsiShortOK = o['rsiShortMin'] <= ri <= o['rsiShortMax']
        if pos == 0:
            if combo >= o['buyThreshold'] and trendUp and adxOK and rsiLongOK and volOK and bscore >= -BIG_BLOCK:
                cur = _open_pos(positions, 'buy', i, closes, ai, o)
                positions.append(cur)
                pos = 1
            elif combo <= o['sellThreshold'] and trendDown and adxOK and rsiShortOK and volOK and bscore <= BIG_BLOCK:
                cur = _open_pos(positions, 'sell', i, closes, ai, o)
                positions.append(cur)
                pos = -1
        elif pos == 1:
            if combo <= -0.05 or trendDown or death or bscore <= -BIG_BLOCK:
                cur['exitI'] = i
                cur = None
                pos = 0
        elif pos == -1:
            if combo >= 0.05 or trendUp or golden or bscore >= BIG_BLOCK:
                cur['exitI'] = i
                cur = None
                pos = 0
    return positions


# ───────────────────────── 模拟 + 指标 ─────────────────────────
def simulate(positions, candles, o, trailMult=0.0):
    closes = [c.c for c in candles]
    highs = [c.h for c in candles]
    lows = [c.l for c in candles]
    trades = []
    for p in positions:
        ei = p['entryI']
        xi = p['exitI']
        long = p['dir'] == 'buy'
        entry = p['entryPrice']
        stop, tp = p['stop'], p['tp']
        coins = NOTIONAL / entry
        atr0 = p.get('atr', entry * 0.01)

        if trailMult > 0:
            # 移动止损：硬止损 + trailing stop(峰值回撤 trailMult×ATR)，忽略信号出场/固定止盈
            end = len(candles) - 1
            peak = entry
            trail = None
            ex, reason = None, None
            for j in range(ei + 1, end + 1):
                hi, lo = highs[j], lows[j]
                if long:
                    if hi > peak:
                        peak = hi
                    if trail is None and peak - entry >= atr0:
                        trail = peak - trailMult * atr0
                    elif trail is not None:
                        trail = max(trail, peak - trailMult * atr0)
                    eff = max(stop, trail) if trail is not None else stop
                    if lo <= eff:
                        ex, reason = eff, 'TR'
                        break
                else:
                    if hi < peak:
                        peak = hi
                    if trail is None and entry - peak >= atr0:
                        trail = peak + trailMult * atr0
                    elif trail is not None:
                        trail = min(trail, peak + trailMult * atr0)
                    eff = min(stop, trail) if trail is not None else stop
                    if hi >= eff:
                        ex, reason = eff, 'TR'
                        break
            if ex is None:
                ex = closes[end]
                reason = 'HOLD'
            fee = (entry + ex) * coins * FEE
            pnl = (ex - entry) * coins if long else (entry - ex) * coins
            pnl -= fee
            trades.append(dict(dir=p['dir'], entry=entry, exit=ex, pnl=pnl,
                               ret_pct=pnl / NOTIONAL * 100, reason=reason, bars=end - ei))
            continue

        # —— 固定 SL/TP + 信号出场 ——
        end = xi if xi is not None else len(candles) - 1
        ex, reason = None, None
        for j in range(ei + 1, end + 1):
            if long:
                if lows[j] <= stop:
                    ex, reason = stop, 'SL'
                    break
                if tp is not None and highs[j] >= tp:
                    ex, reason = tp, 'TP'
                    break
            else:
                if highs[j] >= stop:
                    ex, reason = stop, 'SL'
                    break
                if tp is not None and lows[j] <= tp:
                    ex, reason = tp, 'TP'
                    break
        if ex is None:
            ex = closes[end]
            reason = 'SIG' if xi is not None else 'OPEN'
        fee = (entry + ex) * coins * FEE
        pnl = (ex - entry) * coins if long else (entry - ex) * coins
        pnl -= fee
        trades.append(dict(dir=p['dir'], entry=entry, exit=ex, pnl=pnl,
                           ret_pct=pnl / NOTIONAL * 100, reason=reason, bars=end - ei))
    return trades


def metrics(trades):
    n = len(trades)
    if n == 0:
        return dict(n=0, wr=0, ret=0, avg=0, pf=0, max_dd=0, worst_streak=0,
                    longs=0, shorts=0, avg_bars=0, compound=0)
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    gross_win = sum(t['pnl'] for t in wins)
    gross_loss = -sum(t['pnl'] for t in losses)
    pf = gross_win / gross_loss if gross_loss > 0 else float('inf')
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    streak = 0
    worst_streak = 0
    cmult = 1.0
    for t in trades:
        eq += t['pnl']
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / NOTIONAL * 100)
        if t['pnl'] > 0:
            streak = 0
        else:
            streak += 1
            worst_streak = max(worst_streak, streak)
        cmult *= (1 + t['ret_pct'] / 100)
    avg_bars = sum(t['bars'] for t in trades) / n
    return dict(
        n=n, wr=len(wins) / n * 100, ret=sum(t['pnl'] for t in trades) / NOTIONAL * 100,
        avg=sum(t['pnl'] for t in trades) / n / NOTIONAL * 100, pf=pf, max_dd=max_dd,
        worst_streak=worst_streak,
        longs=sum(1 for t in trades if t['dir'] == 'buy'),
        shorts=sum(1 for t in trades if t['dir'] == 'sell'),
        avg_bars=avg_bars, compound=(cmult - 1) * 100,
    )


def fmt(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime('%Y-%m-%d')


def fetch_pair(symbol, big_tf, small_tf, limit):
    small_limit = limit
    big_limit = int(small_limit * TF_MIN[small_tf] / TF_MIN[big_tf]) + 400
    print(f"拉取方向周期 {big_tf} (limit={big_limit}) …")
    big_raw = history.fetch_candles(norm_tf(big_tf), limit=big_limit, symbol=symbol)
    print(f"拉取信号周期 {small_tf} (limit={small_limit}) …")
    small_raw = history.fetch_candles(norm_tf(small_tf), limit=small_limit, symbol=symbol)
    big = [c for c in big_raw if c.confirm]
    small = [c for c in small_raw if c.confirm]
    if not big or not small:
        print("抓取失败（网络/代理不可达 OKX），退出")
        return None, None
    print(f"方向 {len(big)} 根 ({fmt(big[0].ts)}~{fmt(big[-1].ts)})  "
          f"信号 {len(small)} 根 ({fmt(small[0].ts)}~{fmt(small[-1].ts)})")
    return big, small


def run(symbol, big_tf, small_tf, limit, o, big_weight, big_block, trail):
    print(f"\n===== 多周期共振回测  {symbol}  方向={big_tf} 信号={small_tf}  "
          f"(blend w={big_weight} blk={big_block} trail={trail}) =====")
    big, small = fetch_pair(symbol, big_tf, small_tf, limit)
    if big is None:
        return

    big_pc = precompute(big, o)
    small_pc = precompute(small, o)
    big_regime = trend_regime(big_pc, o)
    hold = (small[-1].c / small[0].c - 1) * 100

    pc_big = Counter(big_regime)
    print("大周期方向分布: 多=%d 空=%d 震荡=%d" % (pc_big.get(1, 0), pc_big.get(-1, 0), pc_big.get(0, 0)))
    print(f"买入持有对照: {hold:+.2f}%  (基准)")

    all_pos = combo_positions(small_pc, o)
    kept_pos, dropped = resonant_positions(all_pos, big_regime, small, big)
    big_score = compute_raw_score(big_pc, o)
    big_aligned = align_big_to_small(big_score, big, small)
    blend_pos = combo_positions_blend(small_pc, big_aligned, o, big_weight, big_block)

    m_all = metrics(simulate(all_pos, small, o, trail))
    m_res = metrics(simulate(kept_pos, small, o, trail))
    m_blend = metrics(simulate(blend_pos, small, o, trail))

    hdr = (f"{'方案':<34}{'笔数':>6}{'多/空':>8}{'胜率':>8}{'收益%':>9}{'复利%':>9}"
           f"{'均笔%':>8}{'盈亏比':>8}{'回撤%':>9}{'最长连亏':>8}{'均持仓':>7}")
    print("\n" + hdr)
    print("-" * len(hdr))
    pf_all = 'inf' if m_all['pf'] == float('inf') else f"{m_all['pf']:.2f}"
    pf_res = 'inf' if m_res['pf'] == float('inf') else f"{m_res['pf']:.2f}"
    pf_bl = 'inf' if m_blend['pf'] == float('inf') else f"{m_blend['pf']:.2f}"
    print(f"{'A) 纯小周期(无共振)':<34}{m_all['n']:>6}{m_all['longs']}/{m_all['shorts']:>5}"
          f"{m_all['wr']:>7.1f}%{m_all['ret']:>9.2f}{m_all['compound']:>9.2f}{m_all['avg']:>8.3f}{pf_all:>8}"
          f"{m_all['max_dd']:>9.2f}{m_all['worst_streak']:>8}{m_all['avg_bars']:>7.1f}")
    print(f"{'B) 硬过滤共振(方向一致)':<34}{m_res['n']:>6}{m_res['longs']}/{m_res['shorts']:>5}"
          f"{m_res['wr']:>7.1f}%{m_res['ret']:>9.2f}{m_res['compound']:>9.2f}{m_res['avg']:>8.3f}{pf_res:>8}"
          f"{m_res['max_dd']:>9.2f}{m_res['worst_streak']:>8}{m_res['avg_bars']:>7.1f}")
    print(f"{'C) 打分叠加共振(blend)':<34}{m_blend['n']:>6}{m_blend['longs']}/{m_blend['shorts']:>5}"
          f"{m_blend['wr']:>7.1f}%{m_blend['ret']:>9.2f}{m_blend['compound']:>9.2f}{m_blend['avg']:>8.3f}{pf_bl:>8}"
          f"{m_blend['max_dd']:>9.2f}{m_blend['worst_streak']:>8}{m_blend['avg_bars']:>7.1f}")

    if trades := simulate(kept_pos, small, o, trail):
        print("\n最近 B(硬过滤) 成交样本（最多 8 笔）：")
        for t in trades[-8:]:
            print(f"  {t['dir']:>4} 入场={t['entry']:.2f} 出场={t['exit']:.2f} "
                  f"盈亏=${t['pnl']:+.1f} ({t['ret_pct']:+.2f}%) 出场={t['reason']} 持仓={t['bars']}根")


def scan(symbol, big_tf, small_tf, limit, o, grid, mode, trail_grid):
    print(f"\n===== 参数网格扫描[{mode}]  {symbol}  方向={big_tf} 信号={small_tf} =====")
    big, small = fetch_pair(symbol, big_tf, small_tf, limit)
    if big is None:
        return
    big_pc = precompute(big, o)
    small_pc = precompute(small, o)
    hold = (small[-1].c / small[0].c - 1) * 100

    # ── 移动止损扫描（tp=0 让利润奔跑）──
    if trail_grid:
        combos = [(bt, sl, adx, tr)
                  for bt in grid['buyThreshold'] for sl in grid['slMult']
                  for adx in grid['adxMin'] for tr in trail_grid]
        print(f"移动止损网格: buy={grid['buyThreshold']} sl={grid['slMult']} "
              f"adx={grid['adxMin']} trail={trail_grid}  (tp=0, 忽略信号出场)")
        print(f"买入持有对照: {hold:+.2f}%  共 {len(combos)} 组参数")
        results = []
        t0 = time.time()
        for idx, (bt, sl, adx, tr) in enumerate(combos):
            o2 = dict(o)
            o2.update(buyThreshold=bt, sellThreshold=-bt, slMult=sl, tpMult=0, adxMin=adx)
            big_regime = trend_regime(big_pc, o2)
            small_pos = combo_positions(small_pc, o2)
            kept = resonant_positions(small_pos, big_regime, small, big)[0]
            m = metrics(simulate(kept, small, o2, trailMult=tr))
            results.append((bt, sl, adx, tr, m))
            if (idx + 1) % 96 == 0:
                print(f"  …已跑 {idx + 1}/{len(combos)}  ({(time.time() - t0):.1f}s)")
        results.sort(key=lambda x: x[4]['ret'], reverse=True)
        print(f"扫描完成，耗时 {time.time() - t0:.1f}s\n")
        hdr = (f"{'buy':>5}{'sl':>5}{'adx':>5}{'trail':>7}{'笔数':>6}{'胜率':>8}"
               f"{'收益%':>9}{'复利%':>9}{'盈亏比':>8}{'回撤%':>9}{'均持仓':>7}")
        print(hdr)
        print("-" * len(hdr))
        for bt, sl, adx, tr, m in results[:20]:
            pf = 'inf' if m['pf'] == float('inf') else f"{m['pf']:.2f}"
            print(f"{bt:>5.2f}{sl:>5.1f}{adx:>5d}{tr:>7.1f}{m['n']:>6}"
                  f"{m['wr']:>7.1f}%{m['ret']:>9.2f}{m['compound']:>9.2f}{pf:>8}{m['max_dd']:>9.2f}{m['avg_bars']:>7.1f}")
        rets = [r[4]['ret'] for r in results]
        cmpds = [r[4]['compound'] for r in results]
        ns = [r[4]['n'] for r in results]
        best = results[0][4]
        print("\n汇总(全部%s组, 移动止损硬过滤):" % len(results))
        print(f"  最优收益%={best['ret']:.2f}  复利%={best['compound']:.2f}  "
              f"笔数={best['n']} 胜率={best['wr']:.1f}%  (buy={results[0][0]} sl={results[0][1]} adx={results[0][2]} trail={results[0][3]})")
        print(f"  收益中位数%={sorted(rets)[len(rets)//2]:.2f}  "
              f"复利收益中位数%={sorted(cmpds)[len(cmpds)//2]:.2f}")
        print(f"  笔数中位数={sorted(ns)[len(ns)//2]}  笔数范围={min(ns)}~{max(ns)}")
        print(f"  跑赢买入持有({hold:.2f}%)的参数组数: {sum(1 for x in rets if x > hold)}/{len(rets)}")
        return

    # ── 常规 hard / blend 扫描 ──
    if mode == 'blend':
        print(f"网格: buy={grid['buyThreshold']} sl={grid['slMult']} tp={grid['tpMult']} "
              f"adx={grid['adxMin']} bw={grid['bigWeight']} blk={grid['bigBlock']}")
        combos = [(bt, sl, tp, adx, bw, blk)
                  for bt in grid['buyThreshold'] for sl in grid['slMult']
                  for tp in grid['tpMult'] for adx in grid['adxMin']
                  for bw in grid['bigWeight'] for blk in grid['bigBlock']]
    else:
        print(f"网格: buy={grid['buyThreshold']} sl={grid['slMult']} tp={grid['tpMult']} adx={grid['adxMin']}")
        combos = [(bt, sl, tp, adx)
                  for bt in grid['buyThreshold'] for sl in grid['slMult']
                  for tp in grid['tpMult'] for adx in grid['adxMin']]
    print(f"买入持有对照: {hold:+.2f}%  共 {len(combos)} 组参数")

    results = []
    t0 = time.time()
    for idx, combo in enumerate(combos):
        if mode == 'blend':
            bt, sl, tp, adx, bw, blk = combo
        else:
            bt, sl, tp, adx = combo
            bw = blk = None
        o2 = dict(o)
        o2.update(buyThreshold=bt, sellThreshold=-bt, slMult=sl, tpMult=tp, adxMin=adx)
        big_regime = trend_regime(big_pc, o2)
        small_pos = combo_positions(small_pc, o2)
        m_b = metrics(simulate(resonant_positions(small_pos, big_regime, small, big)[0], small, o2))
        if mode == 'blend':
            big_score = compute_raw_score(big_pc, o2)
            big_aligned = align_big_to_small(big_score, big, small)
            blend_pos = combo_positions_blend(small_pc, big_aligned, o2, bw, blk)
            m_c = metrics(simulate(blend_pos, small, o2))
            results.append((bt, sl, tp, adx, bw, blk, m_c, m_b))
        else:
            results.append((bt, sl, tp, adx, None, None, m_b, m_b))
        if (idx + 1) % 144 == 0:
            print(f"  …已跑 {idx + 1}/{len(combos)}  ({(time.time() - t0):.1f}s)")

    results.sort(key=lambda x: x[6]['ret'], reverse=True)
    print(f"扫描完成，耗时 {time.time() - t0:.1f}s\n")

    hdr = (f"{'buy':>5}{'sl':>5}{'tp':>5}{'adx':>5}"
           f"{'bw':>5}{'blk':>5}{'笔数':>6}{'胜率':>8}{'收益%':>9}{'复利%':>9}{'盈亏比':>8}{'回撤%':>9}")
    print(hdr)
    print("-" * len(hdr))
    for bt, sl, tp, adx, bw, blk, m, mb in results[:20]:
        pf = 'inf' if m['pf'] == float('inf') else f"{m['pf']:.2f}"
        bws = f"{bw:>5.2f}" if bw is not None else "    -"
        blks = f"{blk:>5.2f}" if blk is not None else "    -"
        print(f"{bt:>5.2f}{sl:>5.1f}{tp:>5.1f}{adx:>5d}{bws}{blks}"
              f"{m['n']:>6}{m['wr']:>7.1f}%{m['ret']:>9.2f}{m['compound']:>9.2f}{pf:>8}{m['max_dd']:>9.2f}")

    rets = [r[6]['ret'] for r in results]
    ns = [r[6]['n'] for r in results]
    cmpds = [r[6]['compound'] for r in results]
    best = results[0][6]
    med = sorted(rets)[len(rets) // 2]
    best_b = results[0][7]['ret']
    print("\n汇总(全部%s组, 方案%s):" % (len(results), 'C blend' if mode == 'blend' else 'B hard'))
    tag = (f"buy={results[0][0]} sl={results[0][1]} tp={results[0][2]} adx={results[0][3]}"
           + (f" bw={results[0][4]} blk={results[0][5]}" if mode == 'blend' else ""))
    print(f"  最优收益%={best['ret']:.2f}  复利%={best['compound']:.2f}  "
          f"笔数={best['n']} 胜率={best['wr']:.1f}%  ({tag})")
    if mode == 'blend':
        print(f"  同组硬过滤(B)收益%={best_b:.2f}  → blend 相对增益={best['ret'] - best_b:+.2f}%")
    print(f"  收益中位数%={med:.2f}   复利收益中位数%={sorted(cmpds)[len(cmpds)//2]:.2f}")
    print(f"  共振后笔数中位数={sorted(ns)[len(ns)//2]}  笔数范围={min(ns)}~{max(ns)}")
    print(f"  跑赢买入持有({hold:.2f}%)的参数组数: {sum(1 for x in rets if x > hold)}/{len(rets)}")


def main():
    ap = argparse.ArgumentParser(description="综合指标多周期共振回测 + 参数扫描")
    ap.add_argument('symbol', nargs='?', default='BTC-USDT')
    ap.add_argument('--big', default='1H', help='方向周期 (默认 1H)')
    ap.add_argument('--small', default='15m', help='信号周期 (默认 15m)')
    ap.add_argument('--limit', type=int, default=16000, help='信号周期拉取根数')
    ap.add_argument('--scan', action='store_true', help='启用参数网格扫描')
    ap.add_argument('--mode', default='blend', choices=['hard', 'blend'],
                    help='共振模式: hard=硬过滤, blend=打分叠加 (默认 blend)')
    ap.add_argument('--bw', type=float, default=BIG_WEIGHT_DEFAULT, help='blend 大周期权重')
    ap.add_argument('--blk', type=float, default=BIG_BLOCK_DEFAULT, help='blend 明确逆势阈值')
    ap.add_argument('--trail', type=float, default=0.0, help='移动止损倍数(>0 启用，让利润奔跑)')
    ap.add_argument('--trail-grid', default=None, help='移动止损扫描网格，逗号分隔')
    ap.add_argument('--buy-grid', default=None)
    ap.add_argument('--sl-grid', default=None)
    ap.add_argument('--tp-grid', default=None)
    ap.add_argument('--adx-grid', default=None)
    ap.add_argument('--bw-grid', default=None)
    ap.add_argument('--blk-grid', default=None)
    for k, v in COMBO_DEFAULTS.items():
        ap.add_argument(f'--{k}', type=float, default=None, help=f'{k} (默认 {v})')
    args = ap.parse_args()

    o = dict(COMBO_DEFAULTS)
    for k in COMBO_DEFAULTS:
        val = getattr(args, k)
        if val is not None:
            o[k] = val

    if args.big not in TF_MIN or args.small not in TF_MIN:
        print("未知周期，支持:", ', '.join(TF_MIN))
        sys.exit(1)

    trail_grid = [float(x) for x in args.trail_grid.split(',')] if args.trail_grid else None

    if args.scan:
        g = dict(GRID)
        if args.buy_grid:
            g['buyThreshold'] = [float(x) for x in args.buy_grid.split(',')]
        if args.sl_grid:
            g['slMult'] = [float(x) for x in args.sl_grid.split(',')]
        if args.tp_grid:
            g['tpMult'] = [float(x) for x in args.tp_grid.split(',')]
        if args.adx_grid:
            g['adxMin'] = [int(float(x)) for x in args.adx_grid.split(',')]
        if args.bw_grid:
            g['bigWeight'] = [float(x) for x in args.bw_grid.split(',')]
        if args.blk_grid:
            g['bigBlock'] = [float(x) for x in args.blk_grid.split(',')]
        scan(args.symbol, args.big, args.small, args.limit, o, g, args.mode, trail_grid)
    else:
        run(args.symbol, args.big, args.small, args.limit, o, args.bw, args.blk, args.trail)


if __name__ == '__main__':
    main()
