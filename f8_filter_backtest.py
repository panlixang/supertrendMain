# -*- coding: utf-8 -*-
"""
Do F8 (price-band distance) and F2/F3 (ATR percentile) have PRACTICAL value
beyond statistical association? Backtest vs baseline always-in ST(10,3).

Strategies (BTC 1h, fee 0.05% per side => 0.1% per position change):
  A baseline      : flip every ST flip (always in market)
  B vol-filter    : skip flips with atr_pctile < CUTOFF (keep old position)
  C f8-early-exit : go flat when distance-to-band < EXIT_ATR, re-enter on next flip
  D B + C combined
Metrics: cumulative return, ann. return, max DD, trades, per-trade stats.
Also: fate of skipped flips (whipsaw avoided vs missed trend).
"""
import numpy as np
import pandas as pd

PERIOD, MULT = 10, 3.0
FEE = 0.001          # per position change (round trip of a flip)
ROLL_WIN = 8760      # 1y on 1h
CUTOFFS = (0.10, 0.20, 0.30, 0.50)
EXIT_ATR_LIST = (0.25, 0.50, 0.75)
WHIP = 10            # bars


def load(path):
    df = pd.read_csv(path)
    df['dt'] = pd.to_datetime(df['open_time'], unit='ms')
    for c in ('open', 'high', 'low', 'close'):
        df[c] = df[c].astype(float)
    return df


def compute_st(df):
    high, low, close = df['high'].values, df['low'].values, df['close'].values
    pc = np.roll(close, 1); pc[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - pc), np.abs(low - pc)))
    atr = pd.Series(tr).ewm(alpha=1 / PERIOD, adjust=False).mean().values
    hl2 = (high + low) / 2
    up_r, dn_r = hl2 - MULT * atr, hl2 + MULT * atr
    n = len(df)
    up = np.empty(n); dn = np.empty(n); trend = np.zeros(n, int)
    up[0], dn[0], trend[0] = up_r[0], dn_r[0], 1
    for i in range(1, n):
        up1, dn1 = up[i - 1], dn[i - 1]
        up[i] = max(up_r[i], up1) if close[i - 1] > up1 else up_r[i]
        dn[i] = min(dn_r[i], dn1) if close[i - 1] < dn1 else dn_r[i]
        if trend[i - 1] == -1 and close[i] > dn1:
            trend[i] = 1
        elif trend[i - 1] == 1 and close[i] < up1:
            trend[i] = -1
        else:
            trend[i] = trend[i - 1]
    st_line = np.where(trend == 1, up, dn)
    return atr, trend, st_line


def perf(pos, close, fee=FEE, name=''):
    """pos: decision array {-1,0,1} decided at close of bar i -> applied to bar i+1.
    Returns r[i] = close[i]/close[i-1]-1 is earned by pos[i-1] (no lookahead)."""
    r = pd.Series(close).pct_change().fillna(0).values
    pos_eff = np.concatenate([[0.0], pos[:-1]])        # shift: decision effective next bar
    strat = pd.Series(pos_eff) * r
    changed = np.diff(np.concatenate([[0.0], pos_eff])) != 0
    strat = strat - fee * changed
    eq = (1 + strat).cumprod()
    total = eq.iloc[-1] - 1
    yrs = len(r) / 8760
    ann = eq.iloc[-1] ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    # per-segment trade stats (entry at decision bar close ~ effective next bar)
    ch = np.where(np.diff(np.concatenate([[0.0], pos])) != 0)[0]
    seg_ends = np.append(ch[1:], len(pos))
    wins, rets = 0, []
    for s, e in zip(ch, seg_ends):
        if e - s < 2:
            continue
        ret = close[min(e, len(pos) - 1)] / close[s] - 1
        if pos[s] == 1:
            rets.append(ret); wins += ret > 0
        elif pos[s] == -1:
            rets.append(-ret); wins += ret < 0
    n_tr = len(rets)
    print(f'  {name:28s} total={total * 100:+9.1f}%  ann={ann * 100:+7.2f}%  '
          f'maxDD={dd * 100:7.1f}%  trades={n_tr:4d}  '
          f'win={wins / max(n_tr, 1) * 100:5.1f}%  avgTr={np.mean(rets) * 100 if rets else 0:+.3f}%')
    return total, ann, dd


def main():
    df = load('btc_1h_2019_2026.csv')
    atr, trend, st_line = compute_st(df)
    close = df['close'].values
    n = len(df)
    atr_pctile = pd.Series(atr / close * 100).rolling(ROLL_WIN, min_periods=ROLL_WIN // 4).rank(pct=True).values
    band = np.where(trend == 1, close - st_line, st_line - close)
    dist = band / atr
    flip = np.zeros(n, int); flip[1:] = (trend[1:] != trend[:-1]).astype(int)
    flip_idx = np.where(flip == 1)[0]
    warm = np.where(~np.isnan(atr_pctile))[0][0]   # first valid bar
    flip_idx = flip_idx[flip_idx >= warm]

    # --- baseline A ---
    posA = trend.astype(float)
    print(f'BTC 1h ST(10,3)  bars={n}  flips(in sample)={len(flip_idx)}  fee={FEE * 100:.2f}% per change')
    print('  buy&hold reference: total = %+.1f%%' % ((close[-1] / close[warm] - 1) * 100))
    perf(posA[warm:], close[warm:], name='A baseline every flip')

    # --- B vol filter ---
    for cut in CUTOFFS:
        pos = np.empty(n)
        cur = trend[warm]
        for i in range(warm, n):
            if flip[i] == 1 and not np.isnan(atr_pctile[i]) and atr_pctile[i] >= cut:
                cur = trend[i]
            pos[i] = cur
        perf(pos[warm:], close[warm:], name=f'B vol-filter cut>={cut:.2f}')

    # fate of skipped flips (cut=0.20)
    cut = 0.20
    skipped = [i for i in flip_idx if atr_pctile[i] < cut]
    taken = [i for i in flip_idx if atr_pctile[i] >= cut]
    good_skip = bad_skip = 0
    for i in skipped:
        nxt = flip_idx[flip_idx > i]
        if len(nxt) == 0:
            continue
        seg = nxt[0] - i
        if seg <= WHIP:
            good_skip += 1
        else:
            move = (close[nxt[0]] / close[i] - 1) * trend[i]
            if move > 0.03:
                bad_skip += 1
    print(f'  skipped flips (cut=0.20): n={len(skipped)}  avoided-whipsaw={good_skip} ({good_skip / len(skipped) * 100:.0f}%)'
          f'  missed->3%move={bad_skip} ({bad_skip / len(skipped) * 100:.0f}%)  other={len(skipped) - good_skip - bad_skip}')
    print(f'  (taken flips whipsaw ref: '
          f'{np.mean([(flip_idx[flip_idx > i][0] - i) <= WHIP for i in taken if (flip_idx > i).any()]) * 100:.0f}% whipsaw)')

    # --- C f8 early exit ---
    for ex in EXIT_ATR_LIST:
        pos = np.zeros(n)
        cur = 0.0
        for i in range(warm, n):
            if flip[i] == 1:
                cur = trend[i]
            elif cur != 0 and dist[i] < ex:
                cur = 0.0   # price hugging band -> exit early, wait for flip
            pos[i] = cur
        perf(pos[warm:], close[warm:], name=f'C f8-early-exit {ex:.2f}ATR')

    # --- D combined: vol filter + early exit ---
    for cut, ex in ((0.20, 0.25), (0.20, 0.50), (0.30, 0.50)):
        pos = np.zeros(n)
        cur = 0.0
        for i in range(warm, n):
            if flip[i] == 1 and atr_pctile[i] >= cut:
                cur = trend[i]
            elif cur != 0 and dist[i] < ex:
                cur = 0.0
            pos[i] = cur
        perf(pos[warm:], close[warm:], name=f'D cut={cut:.2f} + exit {ex:.2f}ATR')

    # --- tautology check: how predictable is a flip from F8 alone? ---
    print('\nF8 tautology check: P(flip next bar | dist) and where flips originate:')
    for lo, hi in ((0, 0.25), (0.25, 0.5), (0.5, 1), (1, 2), (2, 10)):
        m = (dist >= lo) & (dist < hi)
        m &= ~np.isnan(atr_pctile)
        m[-1] = False
        nxt_flip = flip[1:].tolist() + [0]
        p = np.mean(np.array(nxt_flip)[m.astype(bool)]) if m.sum() else 0
        print(f'  dist in [{lo:.2f},{hi:.2f}): P(flip next bar)={p * 100:5.2f}%  bars={int(m.sum())}')
    d_at_flip = dist[np.where(flip == 1)[0] - 1]
    print(f'  dist at bar BEFORE a flip: median={np.median(d_at_flip):.3f} ATR, '
          f'p90={np.percentile(d_at_flip, 90):.2f}  (most flips happen from close range)')


if __name__ == '__main__':
    main()
