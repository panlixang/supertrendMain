# -*- coding: utf-8 -*-
"""
ATR-derived features vs SuperTrend(10,3) behavior research — BTC.

ST params are FIXED (period=10, mult=3, RMA ATR, src=hl2, Pine-compatible flip logic).
We study how 8 observable features relate to ST behavior:
  F1 ATR absolute      atr
  F2 ATR percent       atr / close * 100
  F3 ATR percentile    rolling percentile rank of F2 (1y window)
  F4 ATR slope         (atr - atr[5]) / atr[5]
  F5 ATR acceleration  slope - slope[5]
  F6 ATR outlier       F2 above rolling 99% quantile (1y window)
  F7 close vs hl2      (close - hl2) / atr   (signed bar-position)
  F8 price-ST dist     (close - st_line) / atr  (signed, ATR units)

ST behavior metrics:
  - flip frequency (per 100 bars)
  - segment duration (bars between flips); whipsaw = duration <= 10 bars
  - post-flip forward return (1d / 3d bars, in signal direction)
  - flip probability within next 24 bars, by feature decile
"""
import numpy as np
import pandas as pd

pd.set_option('display.width', 200)
pd.set_option('display.max_columns', 50)

PERIOD, MULT = 10, 3.0
FLIP_HORIZON = 24          # bars: "flip within next N bars" probability window
FWD_BARS = (24, 72)        # post-flip forward return horizons (1d / 3d in bars)
WHIPSAW_MAX = 10           # segment duration <= this => whipsaw
ROLL_WIN = {'1h': 8760, '4h': 2190, '1d': 365}   # 1-year lookback per TF


def load_csv(path):
    df = pd.read_csv(path)
    df['dt'] = pd.to_datetime(df['open_time'], unit='ms')
    for c in ('open', 'high', 'low', 'close'):
        df[c] = df[c].astype(float)
    return df[['dt', 'open', 'high', 'low', 'close']]


def compute_st(df):
    """Pine-compatible SuperTrend: RMA ATR, src=hl2, ratchet bands."""
    high, low, close = df['high'].values, df['low'].values, df['close'].values
    prev_close = np.roll(close, 1); prev_close[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    atr = pd.Series(tr).ewm(alpha=1 / PERIOD, adjust=False).mean().values

    hl2 = (high + low) / 2.0
    up_raw = hl2 - MULT * atr
    dn_raw = hl2 + MULT * atr
    n = len(df)
    up = np.empty(n); dn = np.empty(n); trend = np.zeros(n, dtype=int)
    up[0], dn[0], trend[0] = up_raw[0], dn_raw[0], 1
    for i in range(1, n):
        up1, dn1 = up[i - 1], dn[i - 1]
        up[i] = max(up_raw[i], up1) if close[i - 1] > up1 else up_raw[i]
        dn[i] = min(dn_raw[i], dn1) if close[i - 1] < dn1 else dn_raw[i]
        if trend[i - 1] == -1 and close[i] > dn1:
            trend[i] = 1
        elif trend[i - 1] == 1 and close[i] < up1:
            trend[i] = -1
        else:
            trend[i] = trend[i - 1]
    st_line = np.where(trend == 1, up, dn)
    return atr, trend, st_line


def build_features(df, atr, trend, st_line, tf):
    d = pd.DataFrame({
        'dt': df['dt'], 'close': df['close'],
        'trend': trend,
        'F1_atr': atr,
        'F2_atr_pct': atr / df['close'].values * 100,
    })
    w = ROLL_WIN[tf]
    d['F3_atr_pctile'] = d['F2_atr_pct'].rolling(w, min_periods=w // 4).rank(pct=True)
    d['F4_atr_slope'] = (d['F1_atr'] - d['F1_atr'].shift(5)) / d['F1_atr'].shift(5)
    d['F5_atr_accel'] = d['F4_atr_slope'] - d['F4_atr_slope'].shift(5)
    q99 = d['F2_atr_pct'].rolling(w, min_periods=w // 4).quantile(0.99)
    d['F6_outlier'] = (d['F2_atr_pct'] > q99).astype(float)
    d['F7_close_hl2'] = (df['close'].values - (df['high'].values + df['low'].values) / 2) / atr
    # signed distance to ACTIVE band (the side price would have to break to flip)
    band = np.where(trend == 1,
                    df['close'].values - st_line,      # uptrend: line below price
                    st_line - df['close'].values)      # downtrend: line above price
    d['F8_dist_band_atr'] = band / atr
    d['F8_dist_band_pct'] = band / df['close'].values * 100
    return d


def segment_stats(d):
    """Per-bar flip flags + per-segment (trend run) duration / whipsaw."""
    flip = (d['trend'].diff().fillna(0) != 0).astype(int)
    d['flip'] = flip
    seg_id = flip.cumsum()
    seg_len = d.groupby(seg_id).transform('size')
    # duration of the segment each bar belongs to
    d['seg_len'] = seg_len
    d['seg_id'] = seg_id
    return d


def decile_table(d, col, target, bins=5, label=None):
    """Mean target by quintile of feature (NaNs dropped)."""
    sub = d[[col, target]].dropna()
    if len(sub) < 200:
        return None
    try:
        q = pd.qcut(sub[col], bins, duplicates='drop')
    except ValueError:
        return None
    t = sub.groupby(q, observed=True)[target].agg(['mean', 'count'])
    t.index = [f'{label or col} {i + 1}' for i in range(len(t))]
    return t


def analyze(tf, df):
    atr, trend, st_line = compute_st(df)
    d = build_features(df, atr, trend, st_line, tf)
    d = segment_stats(d)
    n = len(d)
    n_flips = int(d['flip'].sum())

    print('=' * 100)
    print(f'BTC {tf}  bars={n}  ({d["dt"].iloc[0].date()} ~ {d["dt"].iloc[-1].date()})   ST(10,3)')
    print('=' * 100)

    # ---------- 0. baseline ----------
    print(f'\n[0] Baseline: flips={n_flips}  ({n_flips / n * 100:.2f} per 100 bars)'
          f'  median segment={d["seg_len"].median():.0f} bars'
          f'  whipsaw(<= {WHIPSAW_MAX} bars) rate={ (d.drop_duplicates("seg_id")["seg_len"] <= WHIPSAW_MAX).mean() * 100:.1f}%')

    # ---------- 1. feature overview ----------
    print('\n[1] Feature overview (median / p5 / p95):')
    cols = ['F1_atr', 'F2_atr_pct', 'F3_atr_pctile', 'F4_atr_slope', 'F5_atr_accel', 'F7_close_hl2', 'F8_dist_band_atr']
    for c in cols:
        s = d[c].dropna()
        print(f'    {c:20s} med={s.median():+.4f}  p5={s.quantile(.05):+.4f}  p95={s.quantile(.95):+.4f}')
    print(f'    F6_outlier           incidence={d["F6_outlier"].mean() * 100:.1f}% of bars')

    # ---------- 2. does the feature predict a flip soon? ----------
    # target: flip within next FLIP_HORIZON bars (avoid same-bar lookahead: shift target)
    fut_flip = d['flip'].rolling(FLIP_HORIZON).sum().shift(-FLIP_HORIZON)
    d['tgt_flipnext'] = (fut_flip > 0).astype(float)
    print(f'\n[2] P(flip within next {FLIP_HORIZON} bars) by feature quintile  (base rate={d["tgt_flipnext"].mean() * 100:.1f}%)')
    for c in cols + ['F6_outlier']:
        t = decile_table(d, c, 'tgt_flipnext')
        if t is not None:
            print(f'    {c:20s} ' + '  '.join(f'{v * 100:5.1f}%' for v in t['mean']))

    # ---------- 3. feature state AT flip => signal quality ----------
    flips = d[d['flip'] == 1].copy()
    px = d['close']
    for h in FWD_BARS:
        fwd = px.shift(-h) / px - 1
        # return in signal direction: +1 trend -> raw ret ; -1 trend -> -ret
        flips[f'fwd{h}'] = (fwd.loc[flips.index] * flips['trend']).values
    flips['whipsaw'] = (flips['seg_len'] <= WHIPSAW_MAX).astype(float)
    print(f'\n[3] Signal quality at flip events (n={len(flips)}): '
          f'whipsaw rate={flips["whipsaw"].mean() * 100:.1f}%')
    print(f'    {"feature quintile":22s}' + ''.join(f'  win{h}%  ret{h}%' for h in FWD_BARS))
    for c in cols:
        sub = flips[[c, 'fwd24', 'fwd72', 'whipsaw']].dropna()
        if len(sub) < 100:
            continue
        try:
            q = pd.qcut(sub[c], 5, duplicates='drop')
        except ValueError:
            continue
        sub = sub.assign(_bin=q)
        g = sub.groupby('_bin', observed=True)
        row = f'    {c:22s}'
        for h in FWD_BARS:
            wr = g.apply(lambda x, h=h: (x[f'fwd{h}'] > 0).mean(), include_groups=False)
            mr = g[f'fwd{h}'].mean()
            row += ''.join(f'  {a * 100:6.1f} {b * 100:+6.2f}' for a, b in zip(wr, mr))
        print(row)

    # ---------- 4. whipsaw vs feature state ----------
    print('\n[4] Whipsaw rate by feature quintile (at flip):')
    for c in cols:
        sub = flips[[c, 'whipsaw']].dropna()
        if len(sub) < 100:
            continue
        try:
            q = pd.qcut(sub[c], 5, duplicates='drop')
        except ValueError:
            continue
        g = sub.groupby(q, observed=True)['whipsaw'].mean()
        print(f'    {c:22s} ' + '  '.join(f'{v * 100:5.1f}%' for v in g))

    # ---------- 5. F8 distance dynamics ----------
    print('\n[5] F8 price-band distance in ATR units vs flip probability & segment duration:')
    sub = d[['F8_dist_band_atr', 'tgt_flipnext', 'seg_len']].dropna()
    bins = [0, 0.5, 1, 1.5, 2, 3, 4, 6, 100]
    g = sub.groupby(pd.cut(sub['F8_dist_band_atr'], bins), observed=True).agg(
        p_flip=('tgt_flipnext', 'mean'), med_seglen=('seg_len', 'median'), n=('seg_len', 'size'))
    print(g.to_string(float_format=lambda v: f'{v:8.3f}'))

    # ---------- 6. F7 close vs hl2 as intrabar pressure ----------
    print('\n[6] F7 (close-hl2)/atr sign vs trend continuation (all bars):')
    d['f7_sign'] = np.sign(d['F7_close_hl2'])
    agree = (d['f7_sign'] == d['trend']).mean()
    up_bars = d[d['trend'] == 1]
    dn_bars = d[d['trend'] == -1]
    print(f'    F7 sign agrees with ST trend: {agree * 100:.1f}% of bars '
          f'(uptrend: {(up_bars["f7_sign"] == 1).mean() * 100:.1f}%, '
          f'downtrend: {(dn_bars["f7_sign"] == -1).mean() * 100:.1f}%)')
    t = decile_table(d, 'F7_close_hl2', 'tgt_flipnext')
    if t is not None:
        print('    P(flip<=24b) by F7 quintile: ' + '  '.join(f'{v * 100:5.1f}%' for v in t['mean']))

    # ---------- 7. spearman correlation ----------
    print('\n[7] Spearman corr of features with outcomes:')
    m = d[['F1_atr', 'F2_atr_pct', 'F3_atr_pctile', 'F4_atr_slope', 'F5_atr_accel',
           'F7_close_hl2', 'F8_dist_band_atr', 'tgt_flipnext', 'seg_len']].dropna()
    corr = m.corr(method='spearman')
    out = corr[['tgt_flipnext', 'seg_len']].loc[
        ['F1_atr', 'F2_atr_pct', 'F3_atr_pctile', 'F4_atr_slope', 'F5_atr_accel', 'F7_close_hl2', 'F8_dist_band_atr']]
    print(out.to_string(float_format=lambda v: f'{v:+8.3f}'))

    return d, flips


FILES = [('1h', 'btc_1h_2019_2026.csv'), ('4h', 'btc_4h_2019_2026.csv'), ('1d', 'btc_1d_2019_2026.csv')]

if __name__ == '__main__':
    results = {}
    for tf, f in FILES:
        df = load_csv(f)
        d, flips = analyze(tf, df)
        results[tf] = (d, flips)
    # save per-bar feature table for the 1h dataset for further study
    out = results['1h'][0]
    out.to_csv('btc_1h_st_features.csv', index=False)
    print('\nSaved: btc_1h_st_features.csv (per-bar features + ST state, 1h)')
