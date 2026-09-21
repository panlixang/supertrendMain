// Market Regime Engine —— 对应 模型.md 的四模块（V2，按评审重构）
//
// 输入：K线数组 [{ts, o, h, l, c, vol}]（默认 4H）
// 输出：市场状态 + 支撑/压力区(low/high/center/strength/拒绝次数) + 三概率 + 衰竭分 + 买卖信号
//
// 架构（评审最终版）：
//   Regime Model
//     ├─ Trend        → SuperTrend（ST方向）
//     ├─ Range        → S/R 交易（带过滤）
//     └─ Transition   → Exhaustion 检测 → Breakout Agent
//
// 模块1 Regime Detection → trend/range/transition 三概率（市场非二元）
// 模块2 S/R Zone Engine  → Pivot+ATR聚类，强度含「假突破拒绝次数」
// 模块3 Exhaustion       → 边界测试/ATR扩张/量恢复/区间扩大/MA30重指向/ER提升
// 模块4 State Machine    → 明确切换阈值；突破确认用 ST方向(非MA30代理)
//
// 纯前端实现，不依赖后端；数据来自 store.candles[tf]（WS 实时）。

const ER_N = 20;        // 4H: ER 窗口
const MA_P = 30;        // MA30
const MA_LOOK = 10;     // 当前 vs 10 根前（趋势衰减 / ER 加速度）
const ADX_P = 14;
const ATR_P = 14;
const PIVOT_LR = 3;     // Pivot 左右各 3 根
const ST_FACTOR = 3;    // SuperTrend 倍数
const WARMUP = MA_P + ADX_P + 10;
const DRIFT_K = 20;      // 震荡子分类的漂移窗口（bar 数）

// 震荡子分类阈值（range 分支内按方向细分）
const RANGE_UP_SLOPE = 0.12;
const RANGE_DOWN_SLOPE = -0.12;
const RANGE_UP_DRIFT = 1.2;
const RANGE_DOWN_DRIFT = -1.2;

const avg = (arr) => (arr.length ? arr.reduce((s, x) => s + x, 0) / arr.length : 0);
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

// ── 基础指标 ──────────────────────────────────────────────
function sma(values, p) {
  const n = values.length;
  const out = new Array(n).fill(null);
  let sum = 0;
  for (let i = 0; i < n; i++) {
    sum += values[i];
    if (i >= p) sum -= values[i - p];
    if (i >= p - 1) out[i] = sum / p;
  }
  return out;
}

// Wilder ATR（正确平滑：prev - prev/p + tr/p）
function wilderATR(candles, p = ATR_P) {
  const n = candles.length;
  const out = new Array(n).fill(null);
  let prev = 0;
  for (let i = 1; i < n; i++) {
    const tr = Math.max(
      candles[i].h - candles[i].l,
      Math.abs(candles[i].h - candles[i - 1].c),
      Math.abs(candles[i].l - candles[i - 1].c),
    );
    prev = i === 1 ? tr : prev - prev / p + tr / p;
    if (i >= p) out[i] = prev;
  }
  return out;
}

function efficiencyRatio(candles, n = ER_N) {
  const len = candles.length;
  const out = new Array(len).fill(null);
  const c = candles.map((x) => x.c);
  for (let i = n; i < len; i++) {
    const net = Math.abs(c[i] - c[i - n]);
    let s = 0;
    for (let j = i - n + 1; j <= i; j++) s += Math.abs(c[j] - c[j - 1]);
    out[i] = s > 0 ? net / s : 0;
  }
  return out;
}

function computeADX(candles, p = ADX_P) {
  const n = candles.length;
  const tr = new Array(n).fill(0);
  const pDM = new Array(n).fill(0);
  const mDM = new Array(n).fill(0);
  for (let i = 1; i < n; i++) {
    const h = candles[i].h, l = candles[i].l;
    const up = h - candles[i - 1].h;
    const down = candles[i - 1].l - l;
    pDM[i] = up > down && up > 0 ? up : 0;
    mDM[i] = down > up && down > 0 ? down : 0;
    tr[i] = Math.max(h - l, Math.abs(h - candles[i - 1].c), Math.abs(l - candles[i - 1].c));
  }
  const sTR = new Array(n).fill(0);
  const sPD = new Array(n).fill(0);
  const sMD = new Array(n).fill(0);
  for (let i = 1; i < n; i++) {
    if (i === 1) {
      sTR[i] = tr[i]; sPD[i] = pDM[i]; sMD[i] = mDM[i];
    } else {
      sTR[i] = sTR[i - 1] - sTR[i - 1] / p + tr[i] / p;
      sPD[i] = sPD[i - 1] - sPD[i - 1] / p + pDM[i] / p;
      sMD[i] = sMD[i - 1] - sMD[i - 1] / p + mDM[i] / p;
    }
  }
  const pDI = new Array(n).fill(0);
  const mDI = new Array(n).fill(0);
  const dx = new Array(n).fill(0);
  for (let i = p; i < n; i++) {
    const a = sTR[i];
    pDI[i] = a ? (sPD[i] / a) * 100 : 0;
    mDI[i] = a ? (sMD[i] / a) * 100 : 0;
    const sum = pDI[i] + mDI[i];
    dx[i] = sum ? (Math.abs(pDI[i] - mDI[i]) / sum) * 100 : 0;
  }
  // ADX = Wilder 平滑 DX（第 2p-1 根起）
  const out = new Array(n).fill(null);
  let prev = 0;
  for (let i = 1; i < n; i++) {
    if (i === 2 * p - 1) {
      let s = 0;
      for (let j = p; j <= i; j++) s += dx[j];
      out[i] = s / p; prev = s / p;
    } else if (i > 2 * p - 1) {
      prev = prev - prev / p + dx[i] / p;
      out[i] = prev;
    }
  }
  return out;
}

// 轻量 SuperTrend（用于突破阶段的「ST方向一致」，替代慢速 MA30）
function computeSuperTrend(candles, factor = ST_FACTOR, atrArr, p = ATR_P) {
  const n = candles.length;
  const trend = new Array(n).fill(0);   // 1 多头, -1 空头
  const flip = new Array(n).fill(false);
  const distance = new Array(n).fill(null);
  if (n <= p) return { trend, flip, distance };
  const hl2 = candles.map((c) => (c.h + c.l) / 2);
  let prevUp = hl2[p] + factor * (atrArr[p] || 1);
  let prevLo = hl2[p] - factor * (atrArr[p] || 1);
  let st = prevLo, dir = 1;
  trend[p] = 1;
  distance[p] = (candles[p].c - st) / (atrArr[p] || 1);
  for (let i = p + 1; i < n; i++) {
    const a = atrArr[i] || atrArr[i - 1] || 1;
    const wasLower = st === prevLo; // 先记录上一根 ST 在哪条轨（更新前）
    let u = hl2[i] + factor * a;
    let l = hl2[i] - factor * a;
    // 带宽约束：避免被实体穿越后回拉
    u = candles[i - 1].c <= prevUp ? Math.min(u, prevUp) : u;
    l = candles[i - 1].c >= prevLo ? Math.max(l, prevLo) : l;
    prevUp = u; prevLo = l;
    if (wasLower) {
      // 上一根 ST 在下轨（多头）：收盘跌破下轨才翻空
      if (candles[i].c < l) { st = u; dir = -1; flip[i] = true; }
      else { st = l; dir = 1; }
    } else {
      // 上一根 ST 在上轨（空头）：收盘突破上轨才翻多
      if (candles[i].c > u) { st = l; dir = 1; flip[i] = true; }
      else { st = u; dir = -1; }
    }
    trend[i] = dir;
    distance[i] = (candles[i].c - st) / a;
  }
  return { trend, flip, distance };
}

function rangeOf(candles, i, k) {
  let mn = Infinity, mx = -Infinity;
  for (let j = Math.max(0, i - k + 1); j <= i; j++) {
    mn = Math.min(mn, candles[j].l);
    mx = Math.max(mx, candles[j].h);
  }
  return mx - mn;
}

// ── 模块2：Pivot + 聚类成区 ───────────────────────────────
function findPivots(candles, lr = PIVOT_LR) {
  const n = candles.length;
  const highs = [], lows = [];
  for (let i = lr; i < n - lr; i++) {
    let isH = true, isL = true;
    for (let k = 1; k <= lr; k++) {
      if (candles[i].h < candles[i - k].h || candles[i].h < candles[i + k].h) isH = false;
      if (candles[i].l > candles[i - k].l || candles[i].l > candles[i + k].l) isL = false;
    }
    if (isH) highs.push({ i, price: candles[i].h, vol: candles[i].vol, ts: candles[i].ts });
    if (isL) lows.push({ i, price: candles[i].l, vol: candles[i].vol, ts: candles[i].ts });
  }
  return { highs, lows };
}

function clusterZones(pivots, atrArr) {
  const clusters = [];
  for (const p of pivots) {
    const thr = 0.5 * (atrArr[p.i] || 0);
    if (thr <= 0) {
      clusters.push({ members: [p], center: p.price });
      continue;
    }
    let best = null, bd = Infinity;
    for (const cl of clusters) {
      const d = Math.abs(p.price - cl.center);
      if (d < thr && d < bd) { best = cl; bd = d; }
    }
    if (best) {
      best.members.push(p);
      best.center = avg(best.members.map((m) => m.price));
    } else {
      clusters.push({ members: [p], center: p.price });
    }
  }
  let merged = true;
  while (merged) {
    merged = false;
    for (let a = 0; a < clusters.length; a++) {
      for (let b = a + 1; b < clusters.length; b++) {
        const ta = 0.5 * (atrArr[clusters[a].members[0].i] || 0);
        const tb = 0.5 * (atrArr[clusters[b].members[0].i] || 0);
        if (Math.abs(clusters[a].center - clusters[b].center) < Math.max(ta, tb) + 1e-9) {
          clusters[a].members = clusters[a].members.concat(clusters[b].members);
          clusters[a].center = avg(clusters[a].members.map((m) => m.price));
          clusters.splice(b, 1);
          merged = true;
          break;
        }
      }
      if (merged) break;
    }
  }
  return clusters;
}

// Zone Score（含假突破拒绝次数）
// 权重：触碰30% 反弹25% 最近性15% 成交量10% 假突破拒绝20%
function scoreZone(cluster, type, candles, atrArr) {
  const members = cluster.members;
  const n = candles.length;
  const prices = members.map((m) => m.price);
  const touches = members.length;
  let bounceSum = 0;
  for (const m of members) {
    const a = atrArr[m.i] || 1;
    const end = Math.min(n - 1, m.i + 3);
    if (end > m.i) {
      if (type === 'resistance') {
        let mn = Infinity;
        for (let k = m.i; k <= end; k++) mn = Math.min(mn, candles[k].l);
        bounceSum += Math.max(0, (m.price - mn) / a / 2);
      } else {
        let mx = -Infinity;
        for (let k = m.i; k <= end; k++) mx = Math.max(mx, candles[k].h);
        bounceSum += Math.max(0, (mx - m.price) / a / 2);
      }
    }
  }
  const avgBounce = touches ? bounceSum / touches : 0;
  const avgRecency = avg(members.map((m) => m.i)) / n;
  const baseVol = avg(candles.map((c) => c.vol));
  const avgVol = avg(members.map((m) => m.vol));
  const volRatio = baseVol > 0 ? avgVol / baseVol : 1;

  const high = Math.max(...prices);
  const low = Math.min(...prices);
  // 假突破拒绝：近 60 根内，价格刺穿区边界却收回
  let rejections = 0;
  const from = Math.max(0, n - 60);
  for (let k = from; k < n; k++) {
    if (type === 'resistance') {
      if (candles[k].h > high && candles[k].c <= high) rejections++;
    } else if (candles[k].l < low && candles[k].c >= low) rejections++;
  }

  const strength = 100 * (
    0.30 * clamp(touches / 5, 0, 1) +
    0.25 * clamp(avgBounce, 0, 1) +
    0.15 * clamp(avgRecency, 0, 1) +
    0.10 * clamp(volRatio, 0, 1) +
    0.20 * clamp(rejections / 3, 0, 1)
  );
  return {
    type,
    center: cluster.center,
    high,
    low,
    strength: Math.round(strength * 10) / 10,
    touches,
    rejections,
    lastTs: members[members.length - 1].ts,
  };
}

// ── 模块1：三概率 trend / range / transition ──────────────
function regimeScoresAt(i, ctx) {
  const erPrevIdx = Math.max(MA_LOOK, i - MA_LOOK);
  const er = ctx.er[i] ?? 0.5;
  const erPrev = ctx.er[erPrevIdx] ?? er;
  const adx = ctx.adx[i] ?? 25;
  const adxPrev = ctx.adx[erPrevIdx] ?? adx;
  const slope = ctx.ma30Slope[i] ?? 0;
  const slopePrev = ctx.ma30Slope[erPrevIdx] ?? slope;
  const r20 = ctx.range20[i] ?? 0;
  const r50 = ctx.range50[i] || 1;
  const comp = r50 > 0 ? r20 / r50 : 1;

  const ER_score = clamp(er / 0.4, 0, 1);
  const ADX_score = clamp(adx / 25, 0, 1);
  const MA_score = clamp(Math.abs(slope) / 0.2, 0, 1);
  const ADX_low = clamp((25 - adx) / 25, 0, 1);
  const compression = clamp(1 - comp, 0, 1);

  // 趋势 = 方向效率 + 趋势强度（关键：不依赖 MA 斜率，否则“慢涨震荡”会被误判为趋势）
  const trend = 0.5 * ER_score + 0.5 * ADX_score;
  const range = 0.5 * (1 - ER_score) + 0.25 * ADX_low + 0.25 * compression;

  // transition = 趋势衰减（曾强现弱）+ 模棱两可
  const decay = clamp(
    0.5 * (ER_score - clamp(erPrev / 0.4, 0, 1)) +
    0.5 * (ADX_score - clamp(adxPrev / 25, 0, 1)),
    0, 1,
  );
  const ambiguity = clamp(1 - Math.max(trend, range), 0, 1);
  const transition = clamp(Math.max(decay, ambiguity * 0.8), 0, 1);
  return { trend, range, transition };
}

// ── 模块3：exhaustion_score（含 ER 提升）─────────────────
function exhaustionAt(i, ctx) {
  const candles = ctx.candles;
  const start = Math.max(0, i - 30);
  const win = candles.slice(start, i + 1);
  const hi = Math.max(...win.map((c) => c.h));
  const lo = Math.min(...win.map((c) => c.l));
  let tests = 0;
  for (let k = start; k <= i; k++) {
    if (candles[k].h >= hi * 0.997 || candles[k].l <= lo * 1.003) tests++;
  }
  const sTests = 25 * clamp(tests / 5, 0, 1);                         // 边界测试 25%
  const ar = ctx.atrRatio[i] || 1;
  const sATR = 20 * clamp((ar - 1) / 0.4, 0, 1);                     // ATR扩张 20%
  const vr = (ctx.volRatio[i] || 1) - 1;
  const sVol = 15 * clamp(vr / 0.5, 0, 1);                           // 量恢复 15%
  const r20 = ctx.range20[i] ?? 0;
  const r50 = ctx.range50[i] || 1;
  const rr = r50 > 0 ? r20 / r50 : 1;
  const sRange = 10 * clamp((rr - 1) / 0.3, 0, 1);                   // 区间扩大 10%
  const s = ctx.ma30Slope[i] ?? 0;
  const sMA = 15 * clamp((Math.abs(s) - 0.2) / 0.4, 0, 1);           // MA30重指向 15%
  const erNow = ctx.er[i] ?? 0.3;
  const erPrev = ctx.er[Math.max(MA_LOOK, i - MA_LOOK)] ?? erNow;
  const erAccel = clamp((erNow - erPrev) / 0.2, 0, 1);              // ER提升(方向形成) 15%
  const sER = 15 * erAccel;
  return sTests + sATR + sVol + sRange + sMA + sER;
}

// ── 模块4：状态机 + 突破确认 ───────────────────────────────
function breakoutUp(i, ctx, resistance, volRatio, atrRatio, st) {
  return !!resistance &&
    ctx.candles[i].c > resistance.high &&
    (volRatio[i] || 0) > 1.5 &&
    (atrRatio[i] || 0) > 1.2 &&
    st.trend[i] === 1; // ST 方向与突破一致（非等 ST flip）
}
function breakoutDown(i, ctx, support, volRatio, atrRatio, st) {
  return !!support &&
    ctx.candles[i].c < support.low &&
    (volRatio[i] || 0) > 1.5 &&
    (atrRatio[i] || 0) > 1.2 &&
    st.trend[i] === -1;
}
// 在 range 分支内，按方向把震荡细分为三类（关键：识别“无方向的横盘”还是“趋势中的休息”）
function classifyRangeState(i, ctx) {
  const slope = ctx.ma30Slope ? (ctx.ma30Slope[i] ?? 0) : 0;
  const drift = ctx.drift ? (ctx.drift[i] ?? 0) : 0;
  if (slope > RANGE_UP_SLOPE || drift > RANGE_UP_DRIFT) return 'uptrend_consol';
  if (slope < RANGE_DOWN_SLOPE || drift < RANGE_DOWN_DRIFT) return 'downtrend_consol';
  return 'neutral_range';
}

function stateAt(i, env) {
  const { scores, exhaustion, adx, candles, resistance, support, volRatio, atrRatio, st } = env;
  const sc = scores[i] || { trend: 0, range: 0 };
  const tp = sc.trend, rp = sc.range;
  const ex = exhaustion[i] ?? 0;
  const a = adx[i] ?? 0;
  if (ex >= 70 && (breakoutUp(i, env, resistance, volRatio, atrRatio, st) ||
                   breakoutDown(i, env, support, volRatio, atrRatio, st))) return 'breakout';
  if (tp >= 0.5 && a >= 20) return 'trend';
  if (ex > 50) return 'transition';
  // 非趋势、非过渡/突破 -> 一律按“震荡/整理”处理，由 slope/drift 细分为三类
  return classifyRangeState(i, env);
}

// ── Market Permission Layer (V3): 取代 No Trade Zone ──────────
// 单根实时面板无法精确统计「连续趋势老化(D)」，故 D 仅在后端回测中使用；
// 前端聚焦 A/B/C 三条件 + 权限矩阵 + 双评分（与后端对齐）。
// 核心：不是“禁止交易”，而是“决定哪些策略可运行 / 权重如何 / 入场门槛多高”。
//   A概率接近 → Trend 降权(不禁止)  B弱ADX → 禁 Trend  C低波动 → 非趋势态禁 Trend / 趋势态降权 + Breakout 等待
const NT_AMBIG_GAP = 0.15;
const NT_ADX_MIN = 20;
const NT_ATR_PCT_MIN = 0.008;

function marketPermissionScoreTrend({ adx = 0, er = 0.5, atr_pct = 0.02 }, probs) {
  const gap = probs.trend - Math.max(probs.range, probs.transition);
  const s_adx = clamp(adx / 40, 0, 1) * 30;
  const s_er = clamp(er / 0.4, 0, 1) * 25;
  const s_atr = clamp(atr_pct / 0.02, 0, 1) * 20;
  const s_age = 15; // 单根无老化信息 -> 给满分(不惩罚)
  const s_prob = clamp(gap / 0.15, 0, 1) * 10;
  return Math.round(s_adx + s_er + s_atr + s_age + s_prob);
}
function marketPermissionScoreBreakout({ adx = 0, atrRatio = 1, volRatio = 1 }) {
  const s_atr = clamp((atrRatio - 1) / 0.4, 0, 1) * 40;
  const s_vol = clamp((volRatio - 1) / 0.5, 0, 1) * 30;
  const s_comp = clamp((25 - adx) / 25, 0, 1) * 30; // 低 ADX = 压缩末端 = 突破优势
  return Math.round(s_atr + s_vol + s_comp);
}
export function marketPermission(m, probs, marketState) {
  const gap = probs.trend - Math.max(probs.range, probs.transition);
  const cond = {
    A: gap < NT_AMBIG_GAP,
    B: (m.adx ?? 0) < NT_ADX_MIN,
    C: (m.atr_pct ?? 0.02) < NT_ATR_PCT_MIN,
    D: false, // 老化统计需连续多根, 实时单根不计算(见后端回测)
  };
  const trend = { allow: true, weight: 1.0, down: false };
  const range = { allow: true, weight: 1.0, down: false, no_chase: false };
  const brk = { allow: true, wait: false, weight: 1.0 };
  const reasons = [];
  if (cond.A) { trend.down = true; trend.weight = 0.6; reasons.push('A概率接近→Trend降权'); }
  if (cond.B) { trend.allow = false; reasons.push('B弱ADX→Trend禁止'); }
  if (cond.C) {
    brk.wait = true;
    if (marketState !== 'trend') { trend.allow = false; reasons.push('C低波动→Trend禁止(非趋势态)'); }
    else { trend.down = true; trend.weight = Math.min(trend.weight, 0.6); reasons.push('C低波动→Trend降权(已确认趋势)'); }
  }
  const trend_score = marketPermissionScoreTrend(m, probs);
  const breakout_score = marketPermissionScoreBreakout(m);
  // v1: 评分仅作参考, 不硬关(与后端 TREND_SCORE_GATE=False 对齐)
  return { cond, trend, range, breakout: brk, trend_score, breakout_score, reason: reasons.length ? reasons.join(';') : 'ok' };
}

// ── 主入口 ─────────────────────────────────────────────────
export function computeRegime(candlesRaw, { tf = '4h' } = {}) {
  const candles = (candlesRaw || []).filter((c) => c && c.c).slice(-400);
  const n = candles.length;
  if (n < WARMUP) {
    return { ok: false, reason: `K线不足（需 ≥ ${WARMUP} 根，当前 ${n}）`, tf };
  }
  const close = candles.map((c) => c.c);
  const er = efficiencyRatio(candles);
  const atr = wilderATR(candles);
  const ma30 = sma(close, MA_P);
  const adx = computeADX(candles);
  const st = computeSuperTrend(candles, ST_FACTOR, atr);

  const ma30Slope = new Array(n).fill(null);
  for (let i = MA_P; i < n; i++) {
    const a = ma30[i], b = ma30[i - MA_LOOK];
    ma30Slope[i] = a != null && b != null && atr[i] ? (a - b) / atr[i] : null;
  }
  const range20 = new Array(n).fill(null);
  const range50 = new Array(n).fill(null);
  for (let i = 50; i < n; i++) {
    range20[i] = rangeOf(candles, i, 20);
    range50[i] = rangeOf(candles, i, 50);
  }
  // 价格净漂移（ATR 归一化，DRIFT_K 窗口）：>0 上行 / <0 下行 / ≈0 横盘
  const drift = new Array(n).fill(null);
  for (let i = DRIFT_K; i < n; i++) {
    const a = atr[i] || 1;
    drift[i] = atr[i] ? (close[i] - close[i - DRIFT_K]) / a : null;
  }
  const volSma = sma(candles.map((c) => c.vol), 20);
  const volRatio = new Array(n).fill(null);
  for (let i = 20; i < n; i++) if (volSma[i]) volRatio[i] = candles[i].vol / volSma[i];
  const atrSma = sma(atr.map((v) => v || 0), 20);
  const atrRatio = new Array(n).fill(null);
  for (let i = 20; i < n; i++) if (atrSma[i]) atrRatio[i] = atr[i] / atrSma[i];

  const scores = new Array(n).fill(null);
  const exhaustion = new Array(n).fill(null);
  const sCtx = { er, adx, ma30Slope, range20, range50, drift };
  const eCtx = { candles, atrRatio, volRatio, ma30Slope, range20, range50, er };
  for (let i = WARMUP; i < n; i++) {
    scores[i] = regimeScoresAt(i, sCtx);
    exhaustion[i] = exhaustionAt(i, eCtx);
  }

  // 模块2：区
  const { highs, lows } = findPivots(candles);
  const resistance_zones = clusterZones(highs, atr)
    .map((cl) => scoreZone(cl, 'resistance', candles, atr))
    .sort((a, b) => b.strength - a.strength)
    .slice(0, 3);
  const support_zones = clusterZones(lows, atr)
    .map((cl) => scoreZone(cl, 'support', candles, atr))
    .sort((a, b) => b.strength - a.strength)
    .slice(0, 3);
  const resistance = resistance_zones[0] || null;
  const support = support_zones[0] || null;

  const env = { scores, exhaustion, adx, candles, resistance, support, volRatio, atrRatio, st, ma30Slope, drift };
  const last = n - 1;
  const market_state = stateAt(last, env);

  // 模块4：买卖信号（仅标记，不开仓）
  // 6 状态策略：trend 不发信号；neutral_range 高抛低吸；uptrend_consol 只低吸；
  //            downtrend_consol 只做空；transition/breakout 顺势突破。
  const signals = [];
  let lastBuy = -1e9, lastSell = -1e9;
  let side = 0;            // 信号级持仓方向（0 平 / 1 多 / -1 空），用于抑制同向自翻转
  const COOL = 8;
  for (let i = WARMUP; i < n; i++) {
    const cl = candles[i];
    const stState = stateAt(i, env);
    const ex_i = exhaustion[i] ?? 0;

    if (stState === 'neutral_range' || stState === 'uptrend_consol' || stState === 'downtrend_consol') {
      if (ex_i < 50 && support && resistance) {
        const sSpan = support.high - support.low;
        const posS = sSpan > 0 ? (cl.c - support.low) / sSpan : 0;
        const rSpan = resistance.high - resistance.low;
        const posR = rSpan > 0 ? (cl.c - resistance.low) / rSpan : 0;
        if (stState === 'neutral_range') {
          // 真正横盘：双边均值回归
          if (posS < 0.2 && support.strength > 60 && cl.c > cl.o && i - lastBuy > COOL && side !== 1) {
            signals.push({ ts: cl.ts, type: 'buy', price: cl.l, reason: '横盘-支撑反弹', state: 'neutral_range' });
            lastBuy = i; side = 1;
          }
          if (posR > 0.8 && resistance.strength > 60 && cl.c < cl.o && i - lastSell > COOL && side !== -1) {
            signals.push({ ts: cl.ts, type: 'sell', price: cl.h, reason: '横盘-压力回落', state: 'neutral_range' });
            lastSell = i; side = -1;
          }
        } else if (stState === 'uptrend_consol') {
          // 上涨整理：只回踩低吸，禁止高抛；支撑失守平多（不裸空，等趋势态接管）
          if (posS < 0.25 && support.strength > 55 && cl.c > cl.o && i - lastBuy > COOL && side !== 1) {
            signals.push({ ts: cl.ts, type: 'buy', price: cl.l, reason: '上涨整理-回踩低吸', state: 'uptrend_consol' });
            lastBuy = i; side = 1;
          } else if (side === 1 && cl.c < support.low && i - lastSell > COOL) {
            signals.push({ ts: cl.ts, type: 'sell', price: cl.c, reason: '上涨整理-支撑失守离场', state: 'uptrend_consol' });
            lastSell = i; side = -1;
          }
        } else if (stState === 'downtrend_consol') {
          // 下跌整理：只反弹做空，禁止低吸；压力失守平空（不裸多，等趋势态接管）
          if (posR > 0.75 && resistance.strength > 55 && cl.c < cl.o && i - lastSell > COOL && side !== -1) {
            signals.push({ ts: cl.ts, type: 'sell', price: cl.h, reason: '下跌整理-反弹做空', state: 'downtrend_consol' });
            lastSell = i; side = -1;
          } else if (side === -1 && cl.c > resistance.high && i - lastBuy > COOL) {
            signals.push({ ts: cl.ts, type: 'buy', price: cl.c, reason: '下跌整理-压力失守离场', state: 'downtrend_consol' });
            lastBuy = i; side = 1;
          }
        }
      }
    } else if (stState === 'transition' || stState === 'breakout') {
      // Transition 期间禁止反向 Range 交易；仅监听突破（ER提升+放量+ATR扩张+ST方向）
      if (ex_i >= 70) {
        if (breakoutUp(i, env, resistance, volRatio, atrRatio, st) && i - lastBuy > COOL && side !== 1) {
          signals.push({ ts: cl.ts, type: 'buy', price: cl.c, reason: '突破压力区', state: 'breakout' });
          lastBuy = i; side = 1;
        }
        if (breakoutDown(i, env, support, volRatio, atrRatio, st) && i - lastSell > COOL && side !== -1) {
          signals.push({ ts: cl.ts, type: 'sell', price: cl.c, reason: '跌破支撑区', state: 'breakout' });
          lastSell = i; side = -1;
        }
      }
    }
  }

  const sc = scores[last] || { trend: 0, range: 0, transition: 0 };
  const atrLast = atr[last] || 0;
  const atr_pct = close[last] ? atrLast / close[last] : 0.02;
  const permission = marketPermission(
    { adx: adx[last], er: er[last], atr_pct, atrRatio: atrRatio[last], volRatio: volRatio[last] },
    { trend: sc.trend, range: sc.range, transition: sc.transition },
    market_state,
  );
  return {
    ok: true,
    tf,
    market_state,
    breakout_watch: market_state === 'transition' || market_state === 'breakout',
    permission,
    trend_probability: Math.round(sc.trend * 100) / 100,
    range_probability: Math.round(sc.range * 100) / 100,
    transition_probability: Math.round(sc.transition * 100) / 100,
    exhaustion_score: Math.round((exhaustion[last] ?? 0) * 10) / 10,
    support_zones,
    resistance_zones,
    support,
    resistance,
    signals,
    candlesLen: n,
    metrics: {
      er: er[last],
      ma30_slope: ma30Slope[last],
      drift: drift[last],          // ATR 归一化净漂移（>0 上行 / <0 下行 / ≈0 横盘）
      adx: adx[last],
      atr_ratio: atrRatio[last],
      vol_ratio: volRatio[last],
      st_trend: st.trend[last],   // 1 多 / -1 空 / 0 无
      st_flip: st.flip[last],
      st_distance: st.distance[last],
    },
  };
}
