/**
 * 纯指标计算库（前端，不依赖后端）。
 *
 * 设计目标：把「趋势 / 动量 / 波动 / 量能」各角色拆成独立函数，
 * 再用 computeCombo 把它们组合成一个可解释的多指标打分信号，
 * 用于替代单一 supertrend 作为下单依据。
 *
 * 全部输入为普通数组（按时间升序），输出与输入等长（前若干根用 null 占位，
 * 因为指标需要 warmup）。ts 时间戳不参与计算，仅在 computeCombo 里回填到信号。
 */

// ───────────────────────── 基础序列 ─────────────────────────

/** 指数移动平均。首根直接取价，之后按 k=2/(n+1) 递推。 */
export function ema(values, period) {
  const k = 2 / (period + 1);
  const out = new Array(values.length).fill(null);
  let prev = null;
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (v == null) { out[i] = prev; continue; }
    prev = prev == null ? v : v * k + prev * (1 - k);
    out[i] = prev;
  }
  return out;
}

/** 简单移动平均。 */
export function sma(values, period) {
  const out = new Array(values.length).fill(null);
  if (period <= 0) return out;
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= period) sum -= values[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

/** Wilder 平滑（RMA）。首根直接取，之后按 1/n 加权递推。 */
export function rma(values, period) {
  const out = new Array(values.length).fill(null);
  if (period <= 0) return out;
  const a = 1 / period;
  let prev = null;
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (prev == null) prev = v;
    else prev = a * v + (1 - a) * prev;
    out[i] = prev;
  }
  return out;
}

// ───────────────────────── 单指标 ─────────────────────────

/** RSI（Wilder 版本）。返回 0~100，极端区通常看作 >70 超买 / <30 超卖。 */
export function rsi(closes, period = 14) {
  const n = closes.length;
  const out = new Array(n).fill(null);
  if (n < period + 1) return out;
  const gains = new Array(n - 1);
  const losses = new Array(n - 1);
  for (let i = 1; i < n; i++) {
    const ch = closes[i] - closes[i - 1];
    gains[i - 1] = Math.max(ch, 0);
    losses[i - 1] = Math.max(-ch, 0);
  }
  let avgG = 0, avgL = 0;
  for (let i = 0; i < period; i++) { avgG += gains[i]; avgL += losses[i]; }
  avgG /= period; avgL /= period;
  out[period] = avgL === 0 ? 100 : 100 - 100 / (1 + avgG / avgL);
  for (let i = period; i < gains.length; i++) {
    avgG = (avgG * (period - 1) + gains[i]) / period;
    avgL = (avgL * (period - 1) + losses[i]) / period;
    out[i + 1] = avgL === 0 ? 100 : 100 - 100 / (1 + avgG / avgL);
  }
  return out;
}

/** ATR（平均真实波幅，RMA 平滑 TR）。用于动态止损与波动过滤。 */
export function atr(highs, lows, closes, period = 14) {
  const n = highs.length;
  const out = new Array(n).fill(null);
  if (n < 2) return out;
  const tr = new Array(n);
  tr[0] = highs[0] - lows[0];
  for (let i = 1; i < n; i++) {
    tr[i] = Math.max(
      highs[i] - lows[i],
      Math.abs(highs[i] - closes[i - 1]),
      Math.abs(lows[i] - closes[i - 1]),
    );
  }
  return rma(tr, period);
}

/**
 * ADX + 方向指标。返回 { adx, plusDI, minusDI }，三者等长。
 * ADX 衡量趋势「强度」（与方向无关），一般 >20~25 视为有趋势，<20 视为震荡。
 * 需要约 2*period 根 warmup，之前置 null。
 */
export function adx(highs, lows, closes, period = 14) {
  const n = highs.length;
  const out = { adx: new Array(n).fill(null), plusDI: new Array(n).fill(null), minusDI: new Array(n).fill(null) };
  if (n < 2) return out;

  const plusDM = new Array(n).fill(0);
  const minusDM = new Array(n).fill(0);
  const tr = new Array(n);
  tr[0] = highs[0] - lows[0];
  for (let i = 1; i < n; i++) {
    const up = highs[i] - highs[i - 1];
    const down = lows[i - 1] - lows[i];
    plusDM[i] = up > down && up > 0 ? up : 0;
    minusDM[i] = down > up && down > 0 ? down : 0;
    tr[i] = Math.max(
      highs[i] - lows[i],
      Math.abs(highs[i] - closes[i - 1]),
      Math.abs(lows[i] - closes[i - 1]),
    );
  }
  const trS = rma(tr, period);
  const pDMS = rma(plusDM, period);
  const mDMS = rma(minusDM, period);

  const plusDI = new Array(n);
  const minusDI = new Array(n);
  const dx = new Array(n);
  for (let i = 0; i < n; i++) {
    plusDI[i] = trS[i] ? (100 * pDMS[i]) / trS[i] : 0;
    minusDI[i] = trS[i] ? (100 * mDMS[i]) / trS[i] : 0;
    const s = plusDI[i] + minusDI[i];
    dx[i] = s ? (100 * Math.abs(plusDI[i] - minusDI[i])) / s : 0;
  }
  const adxS = rma(dx, period);
  const warm = 2 * period;
  for (let i = 0; i < n; i++) {
    out.adx[i] = i >= warm ? adxS[i] : null;
    out.plusDI[i] = i >= warm ? plusDI[i] : null;
    out.minusDI[i] = i >= warm ? minusDI[i] : null;
  }
  return out;
}

// ───────────────────────── 组合打分信号 ─────────────────────────

export const COMBO_DEFAULTS = {
  fastLen: 20,
  slowLen: 60,
  rsiLen: 14,
  atrLen: 14,
  adxLen: 14,
  volLen: 20,
  adxMin: 20,        // ADX 过滤：低于此值视为震荡，不开趋势仓
  buyThreshold: 0.35,   // 综合打分上穿此值且为空仓 → 开多
  sellThreshold: -0.35, // 综合打分下穿此值且为空仓 → 开空
  rsiLongMin: 45,    // 多头入场：RSI 不进极端区（偏强但不超买）
  rsiLongMax: 72,
  rsiShortMin: 28,   // 空头入场：RSI 不进极端区
  rsiShortMax: 55,
  atrPctMin: 0.004,  // 波动过滤：ATR/收盘价 过小视为流动性不足，不加分
  slMult: 2,          // 止损距离 = slMult × ATR（入场根）
  tpMult: 2,          // 止盈距离 = tpMult × ATR（与止损等距 → 盈亏比 1:1）
};

/**
 * 稳健起步组合：趋势(EMA20/60 + ADX) + 动量(RSI) + 波动(ATR) + 量能(VOL>MA20)。
 *
 * 打分（score，约 -0.85 ~ +0.85）：
 *   trend   : ADX>=adxMin 时，EMA多头 +0.30 / 空头 -0.30，金叉/死叉再 ±0.10
 *   momentum: (RSI-50)/50 ∈ [-1,1] 乘 0.25
 *   volume  : 量能确认 +0.15，否则 -0.10
 *   volat   : ATR/价 >= atrPctMin 时 +0.10
 *
 * 触发（状态机，避免连续刷点）：
 *   空仓 + score>=buyThreshold + 趋势多 + ADX过滤 + RSI安全区 + 量能确认 → 开多
 *   空仓 + score<=sellThreshold + 趋势空 + ADX过滤 + RSI安全区 + 量能确认 → 开空
 *   持多 → 趋势转空 / 死叉 / score<=-0.05 → 平多
 *   持空 → 趋势转多 / 金叉 / score>=0.05 → 平空
 *
 * @returns {{emaFast:number[],emaSlow:number[],rsi:number[],atr:number[],
 *   adx:number[],volMA:number[],scores:(number|null)[],
 *   signals:{i:number,ts:number,type:'buy'|'sell',kind:'entry'|'exit',score:number}[],
 *   positions:{entryI:number,exitI:number|null,dir:'buy'|'sell',entryPrice:number,
 *     stop:number,tp:number}[]}}
 */
export function computeCombo(candles, opt = {}) {
  const o = { ...COMBO_DEFAULTS, ...opt };
  const n = candles.length;
  const closes = candles.map((c) => c.c);
  const highs = candles.map((c) => c.h);
  const lows = candles.map((c) => c.l);
  const vols = candles.map((c) => c.vol);

  const emaFast = ema(closes, o.fastLen);
  const emaSlow = ema(closes, o.slowLen);
  const rsiArr = rsi(closes, o.rsiLen);
  const atrArr = atr(highs, lows, closes, o.atrLen);
  const { adx: adxArr } = adx(highs, lows, closes, o.adxLen);
  const volMA = sma(vols, o.volLen);

  const scores = new Array(n).fill(null);
  const signals = [];
  const positions = [];   // 每笔持仓区间 + ATR 止损/止盈价
  let pos = 0;            // 0 空仓, 1 持多, -1 持空
  let cur = null;         // 当前持仓

  const W_TREND = 0.30, W_X = 0.10, W_MOM = 0.25, W_VOL = 0.15, W_VOLAT = 0.10;
  const WARMUP = Math.max(o.slowLen, 2 * o.adxLen) + 10;

  for (let i = 1; i < n; i++) {
    if (i < WARMUP) continue;
    const ef = emaFast[i], es = emaSlow[i];
    const r = rsiArr[i], a = atrArr[i], adxv = adxArr[i], vma = volMA[i], v = vols[i];
    if (ef == null || es == null || r == null || a == null || adxv == null || vma == null) continue;

    const trendUp = ef > es;
    const trendDown = ef < es;
    const golden = ef > es && emaFast[i - 1] <= emaSlow[i - 1];
    const death = ef < es && emaFast[i - 1] >= emaSlow[i - 1];
    const adxOK = adxv >= o.adxMin;
    const volOK = v > vma;
    const atrPct = a / closes[i];

    let score = 0;
    if (adxOK) {
      if (trendUp) score += W_TREND;
      else if (trendDown) score -= W_TREND;
      if (golden) score += W_X;
      if (death) score -= W_X;
    }
    score += ((r - 50) / 50) * W_MOM;
    score += volOK ? W_VOL : -0.10;
    if (atrPct >= o.atrPctMin) score += W_VOLAT;
    scores[i] = score;

    const rsiLongOK = r >= o.rsiLongMin && r <= o.rsiLongMax;
    const rsiShortOK = r >= o.rsiShortMin && r <= o.rsiShortMax;

    if (pos === 0) {
      if (score >= o.buyThreshold && trendUp && adxOK && rsiLongOK && volOK) {
        signals.push({ i, ts: candles[i].ts, type: 'buy', kind: 'entry', score });
        const ep = closes[i];
        cur = { entryI: i, exitI: null, dir: 'buy', entryPrice: ep,
          stop: ep - o.slMult * a, tp: ep + o.tpMult * a };
        positions.push(cur);
        pos = 1;
      } else if (score <= o.sellThreshold && trendDown && adxOK && rsiShortOK && volOK) {
        signals.push({ i, ts: candles[i].ts, type: 'sell', kind: 'entry', score });
        const ep = closes[i];
        cur = { entryI: i, exitI: null, dir: 'sell', entryPrice: ep,
          stop: ep + o.slMult * a, tp: ep - o.tpMult * a };
        positions.push(cur);
        pos = -1;
      }
    } else if (pos === 1) {
      if (trendDown || death || score <= -0.05) {
        signals.push({ i, ts: candles[i].ts, type: 'sell', kind: 'exit', score });
        cur.exitI = i;
        cur = null;
        pos = 0;
      }
    } else if (pos === -1) {
      if (trendUp || golden || score >= 0.05) {
        signals.push({ i, ts: candles[i].ts, type: 'buy', kind: 'exit', score });
        cur.exitI = i;
        cur = null;
        pos = 0;
      }
    }
  }

  return { emaFast, emaSlow, rsi: rsiArr, atr: atrArr, adx: adxArr, volMA, scores, signals, positions };
}

/**
 * 大周期方向判定（多周期共振用）：每根返回 +1(多头趋势) / -1(空头趋势) / 0(震荡)。
 * 规则与组合打分里的趋势分支一致：ADX>=adxMin 时看 EMA 快慢线方向，否则视为无趋势。
 */
export function trendRegime(combo, opt = {}) {
  const o = { ...COMBO_DEFAULTS, ...opt };
  const { emaFast, emaSlow, adx } = combo;
  const n = emaFast.length;
  const out = new Array(n).fill(0);
  for (let i = 0; i < n; i++) {
    const ef = emaFast[i], es = emaSlow[i], a = adx[i];
    if (ef == null || es == null || a == null) { out[i] = 0; continue; }
    if (a >= o.adxMin) out[i] = ef > es ? 1 : (ef < es ? -1 : 0);
    else out[i] = 0;
  }
  return out;
}
