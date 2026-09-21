// 策略模型页 —— Market Regime Engine 可视化
// 展示：4H K线 + 支撑/压力区间 + 模型生成的买/卖点 + 市场状态指标
// 数据来自 store.candles[tf]（与主页同源的 WS 实时数据），纯前端计算，不动其它功能。

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createChart, CrosshairMode } from 'lightweight-charts';
import { useStore, ALL_TFS } from '../stores/useStore';
import SymbolSelector from '../components/SymbolSelector';
import { ZonePrimitive } from '../components/zonePrimitive';
import { computeRegime } from '../utils/regimeModel';

// OKX 毫秒 → 图表秒时间戳，+8h 显示北京时间（与 CandleChart 一致）
const toT = (ms) => ms / 1000 + 8 * 3600;

const TF_CHOICES = ['15m', '1h', '4h', '1d'];
const fmt = (v) => (v == null ? '—' : Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 }));

const STATE_META = {
  trend: { label: 'Trend 趋势跟随', color: '#4e8aff' },
  uptrend_consol: { label: '上涨整理·回踩低吸', color: '#3ad29f' },
  neutral_range: { label: '横盘·高抛低吸', color: '#00c9a7' },
  downtrend_consol: { label: '下跌整理·反弹做空', color: '#b46bff' },
  transition: { label: 'Transition 压缩末端', color: '#f5a623' },
  breakout: { label: 'Breakout 突破', color: '#e05263' },
};

export default function ModelPage() {
  const symbol = useStore((s) => s.symbol);
  const [tf, setTf] = useState('4h');
  const candles = useStore((s) => s.candles[tf]);

  const [showZones, setShowZones] = useState(true);
  const [showSignals, setShowSignals] = useState(true);

  const boxRef = useRef(null);
  const chartRef = useRef(null);
  const ref = useRef({});
  const fitted = useRef(null);

  const result = useMemo(() => computeRegime(candles || [], { tf }), [candles, tf]);

  // ── 建图（一次）──
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const chart = createChart(el, {
      width: el.clientWidth,
      height: el.clientHeight,
      layout: { background: { color: 'transparent' }, textColor: '#6c7480', fontSize: 11 },
      grid: { vertLines: { color: '#1a1a1a' }, horzLines: { color: '#1a1a1a' } },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#00c9a755', labelBackgroundColor: '#00c9a7' },
        horzLine: { color: '#00c9a755', labelBackgroundColor: '#00c9a7' },
      },
      rightPriceScale: { borderColor: '#262626', scaleMargins: { top: 0.06, bottom: 0.22 } },
      timeScale: { borderColor: '#262626', timeVisible: true, secondsVisible: false },
    });
    const candle = chart.addCandlestickSeries({
      upColor: '#00c9a7', downColor: '#8b0000',
      borderUpColor: '#00c9a7', borderDownColor: '#8b0000',
      wickUpColor: '#00c9a7', wickDownColor: '#8b0000',
      priceLineColor: '#ffffff44',
    });
    const vol = chart.addHistogramSeries({ priceScaleId: 'vol' });
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });

    const zoneLayer = new ZonePrimitive();
    candle.attachPrimitive(zoneLayer);

    ref.current = { candle, vol, zoneLayer };
    chartRef.current = chart;

    const ro = new ResizeObserver(() =>
      chart.applyOptions({ width: el.clientWidth, height: el.clientHeight }),
    );
    ro.observe(el);
    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      ref.current = {};
    };
  }, []);

  // ── K线 + 量 ──
  const sorted = useMemo(() => {
    if (!candles?.length) return [];
    const m = new Map();
    for (const c of candles) m.set(c.ts, c);
    return [...m.values()].sort((a, b) => a.ts - b.ts);
  }, [candles]);

  useEffect(() => {
    const { candle, vol } = ref.current;
    if (!candle || !sorted.length) return;
    candle.setData(
      sorted.map((c) => ({
        time: toT(c.ts), open: c.o, high: c.h, low: c.l, close: c.c,
      })),
    );
    vol.setData(
      sorted.map((c) => ({
        time: toT(c.ts), value: c.vol,
        color: c.c >= c.o ? '#00c9a733' : '#8b000055',
      })),
    );
    if (fitted.current !== tf) {
      fitted.current = tf;
      const nlen = sorted.length;
      chartRef.current?.timeScale().setVisibleLogicalRange({
        from: Math.max(0, nlen - 250),
        to: nlen + 6,
      });
    }
  }, [sorted, tf]);

  // ── 区间带 ──
  useEffect(() => {
    const { zoneLayer } = ref.current;
    if (!zoneLayer) return;
    const zones = [];
    const R = result?.resistance_zones || [];
    const S = result?.support_zones || [];
    if (showZones && R[0]) {
      zones.push({
        priceTop: R[0].high, priceBottom: R[0].low,
        color: 'rgba(224,82,99,0.12)', border: 'rgba(224,82,99,0.55)',
        label: `压力 ${fmt(R[0].center)} ·${R[0].strength} 拒${R[0].rejections}`, labelColor: '#e05263',
      });
      if (R[1]) zones.push({
        priceTop: R[1].high, priceBottom: R[1].low,
        color: 'rgba(224,82,99,0.06)', border: 'rgba(224,82,99,0.3)',
        label: `压力② ${fmt(R[1].center)} ·${R[1].strength} 拒${R[1].rejections}`, labelColor: '#e0526399',
      });
    }
    if (showZones && S[0]) {
      zones.push({
        priceTop: S[0].high, priceBottom: S[0].low,
        color: 'rgba(0,201,167,0.12)', border: 'rgba(0,201,167,0.55)',
        label: `支撑 ${fmt(S[0].center)} ·${S[0].strength} 拒${S[0].rejections}`, labelColor: '#00c9a7',
      });
      if (S[1]) zones.push({
        priceTop: S[1].high, priceBottom: S[1].low,
        color: 'rgba(0,201,167,0.06)', border: 'rgba(0,201,167,0.3)',
        label: `支撑② ${fmt(S[1].center)} ·${S[1].strength} 拒${S[1].rejections}`, labelColor: '#00c9a799',
      });
    }
    zoneLayer.setZones(zones);
  }, [result, showZones]);

  // ── 买卖信号标记 ──
  useEffect(() => {
    const { candle } = ref.current;
    if (!candle) return;
    if (!showSignals || !result?.signals?.length) {
      candle.setMarkers([]);
      return;
    }
    const markers = result.signals
      .map((s) => ({
        time: toT(s.ts),
        position: s.type === 'buy' ? 'belowBar' : 'aboveBar',
        color: s.type === 'buy' ? '#00c9a7' : '#e05263',
        shape: s.type === 'buy' ? 'arrowUp' : 'arrowDown',
        text: s.type === 'buy' ? `买 ${s.reason}` : `卖 ${s.reason}`,
        size: 1.3,
      }))
      .sort((a, b) => a.time - b.time);
    candle.setMarkers(markers);
  }, [result, showSignals]);

  const st = STATE_META[result?.market_state] || STATE_META.neutral_range;

  return (
    <div style={sty.root}>
      <div style={sty.bar}>
        <SymbolSelector />
        <div style={sty.tfGroup}>
          {TF_CHOICES.map((t) => (
            <button key={t} onClick={() => setTf(t)}
              style={{ ...sty.tfBtn, ...(tf === t ? { color: '#00c9a7', borderColor: '#00c9a7' } : {}) }}>
              {t}
            </button>
          ))}
        </div>
        <div style={{ flex: 1 }} />
        <div style={sty.tfGroup}>
          <button onClick={() => setShowZones((v) => !v)}
            style={{ ...sty.tfBtn, ...(showZones ? { color: '#00c9a7', borderColor: '#00c9a7' } : {}) }}>
            压力/支撑区间
          </button>
          <button onClick={() => setShowSignals((v) => !v)}
            style={{ ...sty.tfBtn, ...(showSignals ? { color: '#00c9a7', borderColor: '#00c9a7' } : {}) }}>
            买卖信号
          </button>
        </div>
      </div>

      {/* 指标卡 */}
      <div style={sty.metrics}>
        <Metric label="市场状态" value={st.label} color={st.color} />
        <Metric label="趋势概率" value={result?.ok ? `${(result.trend_probability * 100).toFixed(0)}%` : '—'}
          color={result?.trend_probability >= 0.5 ? '#4e8aff' : '#6c7480'} />
        <Metric label="震荡概率" value={result?.ok ? `${(result.range_probability * 100).toFixed(0)}%` : '—'}
          color={result?.range_probability > 0.6 ? '#00c9a7' : '#6c7480'} />
        <Metric label="过渡概率" value={result?.ok ? `${(result.transition_probability * 100).toFixed(0)}%` : '—'}
          color={result?.transition_probability >= 0.4 ? '#f5a623' : '#6c7480'} />
        <Metric label="震荡衰竭分" value={result?.ok ? `${result.exhaustion_score}` : '—'}
          color={result?.exhaustion_score >= 70 ? '#e05263' : result?.exhaustion_score >= 40 ? '#f5a623' : '#6c7480'} />
        <Metric label="ST方向" value={result?.ok ? (result.metrics?.st_trend === 1 ? '多 ▲' : result.metrics?.st_trend === -1 ? '空 ▼' : '—') : '—'}
          color={result?.metrics?.st_trend === 1 ? '#00c9a7' : result?.metrics?.st_trend === -1 ? '#e05263' : '#6c7480'} />
        <Metric label="净漂移" value={result?.ok && result.metrics?.drift != null ? `${result.metrics.drift.toFixed(2)}` : '—'}
          color={result?.metrics?.drift > 1.2 ? '#3ad29f' : result?.metrics?.drift < -1.2 ? '#b46bff' : '#6c7480'} />
        <Metric label="支撑区" value={result?.support ? `${fmt(result.support.center)} ·${result.support.strength} 拒${result.support.rejections}` : '—'}
          color="#00c9a7" />
        <Metric label="压力区" value={result?.resistance ? `${fmt(result.resistance.center)} ·${result.resistance.strength} 拒${result.resistance.rejections}` : '—'}
          color="#e05263" />
        <Metric label="信号数" value={result?.signals?.length ?? '—'} />
        {result?.breakout_watch && (
          <span style={{ ...sty.badge, borderColor: '#e05263', color: '#e05263' }}>突破监听中</span>
        )}
      </div>

      {/* Market Permission Layer（取代 No Trade Zone） */}
      {result?.ok && result?.permission && (
        <div style={sty.perm}>
          <span style={sty.permTitle}>Market Permission Layer</span>
          <PermChip label="条件" value={permCondText(result.permission.cond)} tone="cond" />
          <PermChip label="Trend" value={agentText(result.permission.trend, 'trend')} tone={agentTone(result.permission.trend, 'trend')} score={result.permission.trend_score} />
          <PermChip label="Range" value={agentText(result.permission.range, 'range')} tone={agentTone(result.permission.range, 'range')} />
          <PermChip label="Breakout" value={agentText(result.permission.breakout, 'breakout')} tone={agentTone(result.permission.breakout, 'breakout')} score={result.permission.breakout_score} />
        </div>
      )}

      <div style={sty.chartWrap}>
        <div ref={boxRef} style={{ width: '100%', height: '100%' }} />
        {!result?.ok && (
          <div style={sty.hint}>
            等待 {tf} K线数据…（{symbol}）
            {result?.reason ? ` · ${result.reason}` : ''}
          </div>
        )}
      </div>

      <div style={sty.legend}>
        <b>Market Regime Engine V3</b>（纯前端，仅展示，不开仓）：
        ① Regime Detection 三概率（趋势=ER0.5+ADX0.5｜震荡=(1-ER)0.5+ADX低0.25+压缩0.25｜过渡=趋势衰减+模棱两可；<b>趋势不再含 MA 斜率</b>，避免慢涨被误判为趋势）·
        ② S/R 区（Pivot+ATR聚类；强度=触碰30%·反弹25%·最近15%·量10%·假突破拒绝20%，输出 low/high/center）·
        ③ Exhaustion（边界测试25%·ATR扩张20%·量恢复15%·区间扩大10%·MA重指向15%·ER提升15%）·
        ④ 状态机 6 态（关键：range 分支按 <b>drift/MA30</b> 细分）：
        <b>趋势</b>=SuperTrend跟随｜<b>上涨整理</b>=回踩低吸（禁止高抛）｜<b>横盘</b>=高抛低吸｜<b>下跌整理</b>=反弹做空（禁止低吸）｜<b>压缩末端</b>=监听突破｜<b>突破</b>=放量+ATR扩张+ST方向一致顺势介入
      </div>
    </div>
  );
}

// ── Market Permission Layer 辅助 ──
function permCondText(cond) {
  const on = ['A', 'B', 'C', 'D'].filter((k) => cond[k]);
  return on.length ? on.join('+') : 'ok';
}
function agentText(a, kind) {
  if (kind === 'trend') {
    if (!a.allow) return '禁止';
    if (a.down) return `降权×${a.weight}`;
    return '允许';
  }
  if (kind === 'range') {
    if (!a.allow) return '禁止';
    if (a.no_chase) return '禁追趋势';
    return '允许';
  }
  // breakout
  if (a.wait) return '等待';
  return '允许';
}
function agentTone(a, kind) {
  if (kind === 'trend') {
    if (!a.allow) return 'bad';
    if (a.down) return 'warn';
    return 'good';
  }
  if (kind === 'range') {
    if (!a.allow) return 'bad';
    if (a.no_chase) return 'warn';
    return 'good';
  }
  if (a.wait) return 'warn';
  return 'good';
}
const PERM_TONE = {
  good: { c: '#3ad29f', b: 'rgba(58,210,159,0.5)' },
  warn: { c: '#f5a623', b: 'rgba(245,166,35,0.5)' },
  bad: { c: '#e05263', b: 'rgba(224,82,99,0.5)' },
  cond: { c: '#b46bff', b: 'rgba(180,107,255,0.5)' },
};
function PermChip({ label, value, tone, score }) {
  const t = PERM_TONE[tone] || PERM_TONE.good;
  return (
    <span style={{ ...sty.permChip, borderColor: t.b, color: t.c }}>
      <b style={sty.permChipL}>{label}</b>
      {value}
      {score != null && <span style={sty.permScore}> {score}</span>}
    </span>
  );
}

function Metric({ label, value, color = '#e9ecef' }) {
  return (
    <div style={sty.metricCard}>
      <span style={sty.metricLabel}>{label}</span>
      <span style={{ ...sty.metricValue, color }}>{value}</span>
    </div>
  );
}

const sty = {
  root: { display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, background: 'var(--bg)' },
  bar: {
    display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px',
    borderBottom: '1px solid var(--border)', flexShrink: 0,
  },
  tfGroup: { display: 'flex', gap: 4 },
  tfBtn: {
    background: '#161616', border: '1px solid #2c2c2c', borderRadius: 5,
    color: '#8b93a0', cursor: 'pointer', fontSize: 11, fontWeight: 700, padding: '5px 9px',
  },
  metrics: {
    display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center',
    padding: '8px 12px', flexShrink: 0,
  },
  metricCard: {
    display: 'flex', flexDirection: 'column', gap: 2,
    background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 6,
    padding: '5px 10px', minWidth: 84,
  },
  metricLabel: { fontSize: 9.5, color: '#5a6270', fontWeight: 700 },
  metricValue: { fontSize: 13, fontWeight: 800, fontFamily: 'var(--font-mono)' },
  badge: {
    fontSize: 10, fontWeight: 800, borderWidth: 1, borderStyle: 'solid',
    borderRadius: 4, padding: '4px 8px',
  },
  perm: {
    display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center',
    padding: '6px 12px', flexShrink: 0, borderBottom: '1px solid var(--border)',
    background: 'var(--card)',
  },
  permTitle: { fontSize: 10.5, fontWeight: 800, color: '#6c7480', marginRight: 4 },
  permChip: {
    fontSize: 10.5, fontWeight: 800, borderWidth: 1, borderStyle: 'solid',
    borderRadius: 4, padding: '3px 8px', fontFamily: 'var(--font-mono)',
  },
  permChipL: {
    fontWeight: 800, marginRight: 5, fontSize: 10, opacity: 0.85,
  },
  permScore: { fontSize: 9.5, opacity: 0.7, marginLeft: 1 },
  chartWrap: { flex: 1, minHeight: 0, position: 'relative', padding: 6 },
  hint: {
    position: 'absolute', top: '50%', left: 0, right: 0, textAlign: 'center',
    color: '#5a6270', fontSize: 12, fontFamily: 'var(--font-mono)',
  },
  legend: {
    flexShrink: 0, padding: '8px 12px', fontSize: 10.5, lineHeight: 1.7,
    color: '#6c7480', borderTop: '1px solid var(--border)',
    background: 'var(--card)',
  },
};
