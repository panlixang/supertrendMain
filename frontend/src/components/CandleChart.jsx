/**
 * 主图 —— 对应 Pine 的 PLOTS / FILLS / BAR COLOR 区块
 *
 *   upPlot  = plot(trend ==  1 ? up : na, style = linebr, color = C_Bull)
 *   dnPlot  = plot(trend == -1 ? dn : na, style = linebr, color = C_Bear)
 *   plotshape(buySignal  ? up : na, 'Buy',  shape.labelup,   C_BuyLabel)
 *   plotshape(sellSignal ? dn : na, 'Sell', shape.labeldown, C_SellLabel)
 *   fill(ohlc4, upPlot/dnPlot)   → BandFillPrimitive
 *   barcolor(buy1[1] < sell1[1] ? C_Bull : C_Bear)  → 逐根 K 线上色
 */
import { createChart, CrosshairMode } from 'lightweight-charts';
import { useEffect, useMemo, useRef } from 'react';
import { useStore } from '../stores/useStore';
import { SuperTrendPrimitive } from './superTrendPrimitive';

const C = {
  bull: '#00c9a7',
  bear: '#8b0000',
  buyLabel: '#00c9a7',
  sellLabel: '#c2185b',
  grid: '#1a1a1a',
  text: '#6c7480',
  neutralUp: '#3a4a52',
  neutralDown: '#4a3540',
};

// OKX 毫秒 → 图表秒时间戳，+8h 让坐标轴直接显示北京时间
const toT = (ms) => ms / 1000 + 8 * 3600;

const pad = (n) => String(n).padStart(2, '0');
const fmtBJ = (t) => {
  const d = new Date(t * 1000);
  return `${d.getUTCFullYear()}/${d.getUTCMonth() + 1}/${d.getUTCDate()} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
};
const fmtBJDate = (t) => {
  const d = new Date(t * 1000);
  return `${d.getUTCFullYear()}/${d.getUTCMonth() + 1}/${d.getUTCDate()}`;
};

export default function CandleChart() {
  const boxRef = useRef(null);
  const chartRef = useRef(null);
  const ref = useRef({});
  const fitted = useRef(null);   // 已按哪个周期做过初始视野调整

  const tf = useStore((s) => s.tf);
  const candles = useStore((s) => s.candles[tf]);
  const ind = useStore((s) => s.indicators[tf]);
  const opts = useStore((s) => s.opts);
  const focusTs = useStore((s) => s.focusTs);

  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;

    const chart = createChart(el, {
      width: el.clientWidth,
      height: el.clientHeight,
      layout: { background: { color: 'transparent' }, textColor: C.text, fontSize: 11 },
      grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#00c9a755', labelBackgroundColor: '#00c9a7' },
        horzLine: { color: '#00c9a755', labelBackgroundColor: '#00c9a7' },
      },
      rightPriceScale: { borderColor: '#262626', scaleMargins: { top: 0.06, bottom: 0.24 } },
      timeScale: { borderColor: '#262626', timeVisible: true, secondsVisible: false },
      localization: { timeFormatter: fmtBJ, dateFormatter: fmtBJDate },
    });

    const candle = chart.addCandlestickSeries({
      upColor: C.bull, downColor: C.bear,
      borderUpColor: C.bull, borderDownColor: C.bear,
      wickUpColor: C.bull, wickDownColor: C.bear,
      priceLineColor: '#ffffff44',
    });
    const vol = chart.addHistogramSeries({ priceScaleId: 'vol' });
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });

    // 超趋线本体由 SuperTrendPrimitive 自绘（whitespace 无法让折线断开，见该文件注释）。
    // 这里留一条全透明 line series，只为让价格轴把趋势线纳入自动缩放范围。
    const stAnchor = chart.addLineSeries({
      color: 'transparent', lineWidth: 1,
      priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    const fastMA = chart.addLineSeries({
      color: '#f5a623', lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
    });
    const slowMA = chart.addLineSeries({
      color: '#4e8aff', lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
    });

    const stLayer = new SuperTrendPrimitive();
    candle.attachPrimitive(stLayer);

    ref.current = { candle, vol, stAnchor, fastMA, slowMA, stLayer };
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

  // 去重 + 排序：lightweight-charts 要求时间严格递增
  const sorted = useMemo(() => {
    if (!candles?.length) return [];
    const m = new Map();
    for (const c of candles) m.set(c.ts, c);
    return [...m.values()].sort((a, b) => a.ts - b.ts);
  }, [candles]);

  // K 线 + 成交量（barcolor 逻辑一起处理）
  useEffect(() => {
    const { candle, vol } = ref.current;
    if (!candle || !sorted.length) return;

    // Pine: color1 = buy1[1] < sell1[1] ? C_Bull : C_Bear，即「上一根的趋势」
    const bc = ind?.bar_color;
    const off = bc ? bc.length - sorted.length : 0;
    const dirAt = (i) => (bc ? bc[i + off] : null);
    candle.setData(
      sorted.map((c, i) => {
        const t = toT(c.ts);
        if (!opts.barColoring || dirAt(i) == null) {
          return { time: t, open: c.o, high: c.h, low: c.l, close: c.c };
        }
        const col = dirAt(i) === 1 ? C.bull : C.bear;
        return {
          time: t, open: c.o, high: c.h, low: c.l, close: c.c,
          color: col, borderColor: col, wickColor: col,
        };
      }),
    );
    vol.setData(
      sorted.map((c, i) => {
        const d = dirAt(i);
        const col =
          opts.barColoring && d != null
            ? d === 1 ? '#00c9a733' : '#8b000055'
            : c.c >= c.o ? '#00c9a733' : '#8b000055';
        return { time: toT(c.ts), value: c.vol, color: col };
      }),
    );

    // 首次载入某周期时，把可视区拉宽到最近 300 根 —— 9.1 倍 ATR 下翻转很稀疏，
    // 默认视野只有几十根的话，图上一个信号都看不到
    if (fitted.current !== tf) {
      fitted.current = tf;
      const n = sorted.length;
      chartRef.current?.timeScale().setVisibleLogicalRange({
        from: Math.max(0, n - 300),
        to: n + 6,
      });
    }
  }, [sorted, ind, opts.barColoring, tf]);

  // 超趋线 / 高亮区 / MA / 买卖标签
  useEffect(() => {
    const r = ref.current;
    if (!r.candle || !sorted.length) return;

    const st = ind?.st;
    if (!st?.trend?.length) {
      [r.stAnchor, r.fastMA, r.slowMA].forEach((s) => s.setData([]));
      r.stLayer.setData([]);
      r.candle.setMarkers([]);
      if (r.stPriceLine) {
        r.candle.removePriceLine(r.stPriceLine);
        r.stPriceLine = null;
      }
      return;
    }

    // 指标按后端完整 deque 算，前端 candles 可能短一截 → 从末尾对齐
    const off = st.trend.length - sorted.length;
    const at = (arr, i) => (arr ? arr[i + off] : null);

    const layer = [];
    const anchor = [];
    for (let i = 0; i < sorted.length; i++) {
      const t = toT(sorted[i].ts);
      const dir = at(st.trend, i);
      // 当前趋势对应的那条轨：多头看 up、空头看 dn
      const line = dir === 1 ? at(st.up_plot, i) : dir === -1 ? at(st.dn_plot, i) : null;
      const mid = at(st.ohlc4, i);
      layer.push({ time: t, line, mid, dir });
      if (line != null) anchor.push({ time: t, value: line });
    }
    r.stLayer.setData(layer, opts.highlighting);
    r.stAnchor.setData(anchor);

    // 价格轴上标出当前趋势线位置（= 跟踪止损位），颜色随方向
    if (r.stPriceLine) {
      r.candle.removePriceLine(r.stPriceLine);
      r.stPriceLine = null;
    }
    const lastDir = at(st.trend, sorted.length - 1);
    const lastLine = anchor.length ? anchor[anchor.length - 1].value : null;
    if (lastLine != null && lastDir != null) {
      r.stPriceLine = r.candle.createPriceLine({
        price: lastLine,
        color: lastDir === 1 ? C.bull : '#c0392b',
        lineWidth: 1,
        lineStyle: 2,          // Dashed
        axisLabelVisible: true,
        title: lastDir === 1 ? '超趋↑' : '超趋↓',
      });
    }

    if (opts.showMA) {
      const line = (arr) => {
        const o = arr.length - sorted.length;
        const out = [];
        for (let i = 0; i < sorted.length; i++) {
          const v = arr[i + o];
          if (v != null) out.push({ time: toT(sorted[i].ts), value: v });
        }
        return out;
      };
      r.fastMA.setData(ind.fast_ma ? line(ind.fast_ma) : []);
      r.slowMA.setData(ind.slow_ma ? line(ind.slow_ma) : []);
    } else {
      r.fastMA.setData([]);
      r.slowMA.setData([]);
    }

    // plotshape：Buy 标签打在 up 上，Sell 打在 dn 上
    // hidden=true → 完全不显示（ER 过低且不够干脆的噪声）
    // will_trade===false 且非 hidden → 灰色半透明（被过滤器拦住，仍显示供参考）
    if (opts.showSignals) {
      r.candle.setMarkers(
        (ind.signals || [])
          .filter(s => !s.hidden)
          .map((s) => {
            const blocked = s.will_trade === false;
            const buyColor  = blocked ? '#4a7a6e' : C.buyLabel;
            const sellColor = blocked ? '#7a4a55' : C.sellLabel;
            return {
              time: toT(s.ts),
              position: s.type === 'buy' ? 'belowBar' : 'aboveBar',
              color: s.type === 'buy' ? buyColor : sellColor,
              shape: s.type === 'buy' ? 'arrowUp' : 'arrowDown',
              text: `${s.type === 'buy' ? 'Buy' : 'Sell'}${s.grade ? ' ' + s.grade : ''}${blocked ? ' ✕' : ''}`,
              size: blocked ? 1 : (s.score ?? 0) >= 2 ? 2 : 1.3,
            };
          })
          .sort((a, b) => a.time - b.time),
      );
    } else {
      r.candle.setMarkers([]);
    }
  }, [sorted, ind, opts.highlighting, opts.showSignals, opts.showMA]);

  // 点击信号列表某条 → 把该 K 线滚到视野中间
  useEffect(() => {
    if (!focusTs || !chartRef.current || !sorted.length) return;
    const i = sorted.findIndex((c) => c.ts === focusTs);
    if (i < 0) return;
    chartRef.current.timeScale().setVisibleLogicalRange({
      from: Math.max(0, i - 60),
      to: Math.min(sorted.length + 6, i + 60),
    });
  }, [focusTs, sorted]);

  return (
    <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
      <div ref={boxRef} style={{ width: '100%', height: '100%' }} />
      <ChartOverlay ind={ind} />
    </div>
  );
}

function ChartOverlay({ ind }) {
  const opts = useStore((s) => s.opts);
  const toggle = useStore((s) => s.toggleOpt);
  const params = useStore((s) => s.params);
  const tf = useStore((s) => s.tf);
  const st = ind?.state;

  const TOGGLES = [
    { k: 'showSignals', label: '买卖信号', c: C.bull },
    { k: 'highlighting', label: '高亮区', c: '#8b8b8b' },
    { k: 'barColoring', label: 'K线上色', c: '#8b8b8b' },
    { k: 'showMA', label: `MA${params.fast_len}/${params.slow_len}`, c: '#f5a623' },
  ];

  return (
    <div style={ov.wrap}>
      <div style={ov.row}>
        <span style={ov.title}>
          SUPERTREND {params.periods} × {params.multiplier}
          <span style={{ color: '#4a5058', marginLeft: 6 }}>
            {params.src} · {params.change_atr ? 'RMA' : 'SMA'} · {tf}
          </span>
        </span>
      </div>
      {st?.trend != null && (
        <div style={{ ...ov.card, borderColor: st.trend === 1 ? '#00c9a755' : '#c2185b55' }}>
          <span style={{ color: st.trend === 1 ? C.bull : '#e05263', fontWeight: 800 }}>
            {st.trend === 1 ? '▲ 多头趋势' : '▼ 空头趋势'}
          </span>
          <span style={ov.kv}>
            超趋线 <b style={{ color: '#e9ecef' }}>{st.line?.toLocaleString()}</b>
          </span>
          <span style={ov.kv}>
            距离 <b style={{ color: st.gap_pct >= 0 ? C.bull : '#e05263' }}>
              {st.gap_pct > 0 ? '+' : ''}{st.gap_pct}%
            </b>
            <span style={{ color: '#4a5058' }}> ({st.gap_atr} ATR)</span>
          </span>
          <span style={ov.kv}>
            已持续 <b style={{ color: '#e9ecef' }}>{st.bars}</b> 根 / 浮动{' '}
            <b style={{ color: st.run_pct >= 0 ? C.bull : '#e05263' }}>
              {st.run_pct > 0 ? '+' : ''}{st.run_pct}%
            </b>
          </span>
        </div>
      )}
      <div style={ov.row}>
        {TOGGLES.map(({ k, label, c }) => (
          <button
            key={k}
            onClick={() => toggle(k)}
            style={{ ...ov.pill, borderColor: c, color: c, opacity: opts[k] ? 1 : 0.32 }}
          >
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}

const ov = {
  wrap: {
    position: 'absolute', top: 8, left: 10, zIndex: 5,
    display: 'flex', flexDirection: 'column', gap: 5, alignItems: 'flex-start',
  },
  row: { display: 'flex', gap: 5, flexWrap: 'wrap', alignItems: 'center' },
  title: {
    fontSize: 11, fontWeight: 800, letterSpacing: 0.6, color: '#00c9a7',
    background: '#000000bb', padding: '3px 8px', borderRadius: 3,
    fontFamily: 'var(--font-mono)',
  },
  card: {
    display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap',
    background: '#000000cc', borderWidth: 1, borderStyle: 'solid', borderColor: 'transparent', borderRadius: 4,
    padding: '5px 10px', fontSize: 11,
  },
  kv: { color: '#6c7480', fontFamily: 'var(--font-mono)', fontSize: 10.5 },
  pill: {
    background: '#000000bb', borderWidth: 1, borderStyle: 'solid', borderColor: 'transparent', borderRadius: 3,
    cursor: 'pointer', fontSize: 10, fontWeight: 700, padding: '2px 8px',
    transition: 'opacity .15s',
  },
};
