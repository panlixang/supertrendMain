# -*- coding: utf-8 -*-
"""
F2/F3 (ATR percentile) as false-signal filter for BTC 1h ST(11,6).

Baseline: always-in, enter on every flip (no lookahead, fee per position change).
Variants:
  V1 skip-low        : skip flips with pctile < c (hold old position)
  V2 skip-low+contract: skip only if pctile < c AND vol contracting (slope<0)
  V3 expand-only     : take flips only when vol expanding (slope > s)
  V4 size-low        : always flip, but size 0.25x when pctile < c
  V5 late-entry      : skip low-pctile flip; enter later when pctile >= c2
                       (vol expansion) if trend unchanged -> compatible with
                       compression-breakout logic
Fate analysis of skipped flips: avoided whipsaw vs missed trend move.
"""
import numpy as np
import pandas as pd

PERIOD, MULT = 11, 6.0
ROLL_WIN = 8760
FEE = 0.001
WHIP = 10
DFILE = 'btc_1h_2019_2026.csv'


def load(path):
    df = pd.read_csv(path)
    df['dt'] = pd.to_datetime(df['open_time'], unit='ms')
    for c in ('open', 'high', 'low', 'close'):
        df[c] = df[c].astype(float)
    return df


def compute_st(df, period=PERIOD, mult=MULT):
    high, low, close = df['high'].values, df['low'].values, df['close'].values
    pc = np.roll(close, 1); pc[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - pc), np.abs(low - pc)))
    atr = pd.Series(tr).ewm(alpha=1 / period, adjust=False).mean().values
    hl2 = (high + low) / 2
    up_r, dn_r = hl2 - mult * atr, hl2 + mult * atr
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
    return atr, trend


def perf(pos, close, fee=FEE, name=''):
    """pos decided at close of bar i -> earns bar i+1 return."""
    r = pd.Series(close).pct_change().fillna(0).values
    pos_eff = np.concatenate([[0.0], pos[:-1]])
    strat = pd.Series(pos_eff) * r
    changed = np.diff(np.concatenate([[0.0], pos_eff])) != 0
    strat = strat - fee * changed
    eq = (1 + strat).cumprod()
    total = eq.iloc[-1] - 1
    yrs = len(r) / 8760
    ann = eq.iloc[-1] ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    ch = np.where(np.diff(np.concatenate([[0.0], pos])) != 0)[0]
    seg_ends = np.append(ch[1:], len(pos))
    wins, rets = 0, []
    for s, e in zip(ch, seg_ends):
        if e - s < 2:
            continue
        ret = close[min(e, len(pos) - 1)] / close[s] - 1
        d = pos[s]
        rets.append(ret if d == 1 else -ret)
        wins += (ret > 0) if d == 1 else (ret < 0)
    n_tr = len(rets)
    print(f'  {name:34s} total={total * 100:+8.1f}%  ann={ann * 100:+7.2f}%  '
          f'maxDD={dd * 100:7.1f}%  trades={n_tr:4d}  '
          f'win={wins / max(n_tr, 1) * 100:5.1f}%  avgTr={np.mean(rets) * 100 if rets else 0:+.3f}%')
    return total


def main():
    df = load(DFILE)
    atr, trend = compute_st(df)
    close = df['close'].values
    n = len(df)
    atr_pct = atr / close * 100
    pctile = pd.Series(atr_pct).rolling(ROLL_WIN, min_periods=ROLL_WIN // 4).rank(pct=True).values
    slope = pd.Series(atr).diff(5).values / pd.Series(atr).shift(5).values
    warm = np.where(~np.isnan(pctile))[0][0]
    flip = np.zeros(n, int); flip[1:] = (trend[1:] != trend[:-1]).astype(int)
    flip_idx = np.where(flip == 1)[0]
    flip_idx = flip_idx[flip_idx >= warm]

    bh = close[-1] / close[warm] - 1
    print(f'BTC 1h ST(11,6)  bars={n - warm}  flips={len(flip_idx)}  fee={FEE * 100:.2f}%/change  buy&hold={bh * 100:+.1f}%')

    # ---- diagnostic: signal quality by vol pctile bucket ON ST(11,6) ----
    seg_len = np.diff(np.append(flip_idx, n))
    whip = seg_len <= WHIP
    px_fwd24 = pd.Series(close).shift(-24).values / close - 1
    print('\n[diag] flip quality by ATR pctile bucket (ST 11x6):')
    print(f'    {"pctile bucket":16s}  n   whipsaw   win24   ret24(direction-adj)')
    for lo, hi in ((0, .1), (.1, .2), (.2, .4), (.4, .6), (.6, .8), (.8, 1)):
        m = (pctile[flip_idx] >= lo) & (pctile[flip_idx] < hi)
        if m.sum() < 5:
            continue
        f = flip_idx[m]
        r24 = px_fwd24[f] * trend[f]
        print(f'    [{lo:.1f},{hi:.1f}){"":10s} {m.sum():3d}   {whip[m].mean() * 100:5.1f}%   '
              f'{(r24 > 0).mean() * 100:5.1f}%   {r24.mean() * 100:+.3f}%')

    print('\n[perf] variants:')
    perf(trend[warm:], close[warm:], name='V0 baseline every flip')

    def run_positions(take_rule, name, size=None, late_c2=None):
        """take_rule(i, decision_to_take) -> bool for acting on flip at bar i."""
        pos = np.zeros(n)
        cur = 0.0
        pending = 0   # trend direction awaiting vol confirmation (V5)
        for i in range(warm, n):
            if flip[i] == 1:
                pending = 0
                if take_rule(i):
                    cur = float(trend[i])
                elif late_c2 is not None:
                    pending = trend[i]   # wait for vol expansion, same trend
            elif pending != 0 and trend[i] == pending and pctile[i] >= late_c2:
                cur = float(pending)
                pending = 0
            elif pending != 0 and trend[i] != pending:
                pending = 0
            pos[i] = cur * (size(i) if size else 1.0)
        perf(pos[warm:], close[warm:], name=name)
        return pos

    for c in (0.1, 0.2, 0.3):
        run_positions(lambda i, c=c: pctile[i] >= c, f'V1 skip pctile<{c}')

    for c in (0.2, 0.3):
        run_positions(lambda i, c=c: pctile[i] >= c or slope[i] > 0, f'V2 skip<{c} & contracting')

    for s in (0.0, 0.05):
        run_positions(lambda i, s=s: slope[i] > s, f'V3 expanding slope>{s}')

    run_positions(lambda i: True, 'V4 size 0.25x if pctile<0.2',
                  size=lambda i: 1.0 if pctile[i] >= 0.2 else 0.25)

    for c1, c2 in ((0.2, 0.4), (0.2, 0.6), (0.3, 0.6)):
        run_positions(lambda i, c1=c1: pctile[i] >= c1, f'V5 late-entry <{c1}->wait>{c2}', late_c2=c2)

    # ---- fate of skipped flips (V1 c=0.2) ----
    c = 0.2
    skipped = [i for i in flip_idx if pctile[i] < c]
    taken = [i for i in flip_idx if pctile[i] >= c]
    good = bad = 0
    for i in skipped:
        nxt = flip_idx[flip_idx > i]
        if len(nxt) == 0:
            continue
        if nxt[0] - i <= WHIP:
            good += 1
        else:
            mv = (close[nxt[0]] / close[i] - 1) * trend[i]
            bad += mv > 0.02
    tk_whip = np.mean([whip[flip_idx.tolist().index(i)] for i in taken])
    sk_whip = np.mean([whip[flip_idx.tolist().index(i)] for i in skipped])
    print(f'\n[fate] V1 c=0.2: skipped n={len(skipped)} (their whipsaw rate {sk_whip * 100:.0f}%)  '
          f'avoided-whip={good}  missed>2%move={bad}  |  taken n={len(taken)} whipsaw={tk_whip * 100:.0f}%')


if __name__ == '__main__':
    main()
