/**
 * 综合指标信号页（纯指标计算，不依赖 supertrend）。
 *
 *   主图  ：K线(方向周期/大) + EMA20/EMA60 + 成交量 + 多周期共振买卖点 + ATR 止损止盈轨道
 *   副图1 ：RSI(14)  + 30/70 超买超卖线
 *   副图2 ：ADX(14)  + 20 趋势强度过滤线
 *   副图3 ：ATR(14)  （动态止损参考）
 *   副图4 ：信号周期(小)综合分 + 大周期方向阶梯（多/空/震荡）+ 多/空阈值线
 *
 * 多周期共振（硬过滤）：方向周期(大) 定方向、信号周期(小) 找点 ——
 * 仅保留「小周期开仓方向与大小周期方向一致」的持仓，其余逆势信号被过滤（可对照显示）。
 *
 * 指标与买卖点全部在前端用 utils/indicators.js 的 computeCombo / trendRegime 计算，
 * 数据复用 store.candles[tf]（后端已通过 ws 实时灌入各周期）。
 *
 * 回测定位：该硬过滤共振在 BTC 4H/1H 上回测表现最稳（压低回撤 ~9%），但系统性跑输
 * 买入持有，属「风控 / 方向参考」性质，而非收益策略。页面据此标注。
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { createChart, CrosshairMode } from 'lightweight-charts';
import { ALL_TFS, useStore } from '../stores/useStore';
import { computeCombo, trendRegime, COMBO_DEFAULTS } from '../utils/indicators';

// 回测验证过的推荐共振参数（BTC 4H/1H 硬过滤最优区间附近）
const RECOMMENDED = {
  ...COMBO_DEFAULTS,
  buyThreshold: 0.30,
  sellThreshold: -0.30,
  slMult: 2.5,
  tpMult: 2.5,
  adxMin: 20,
};

const C = {
  bull: '#00c9a7',
  bear: '#e05263',
  buy: '#00c9a7',
  sell: '#e05263',
  exit: '#f5a623',
  emaFast: '#f5a623',
  emaSlow: '#4e8aff',
  grid: '#1a1a1a',
  text: '#6c7480',
  rsi: '#a78bfa',
  adx: '#4e8aff',
  atr: '#00c9a7',
  sig: '#7dd3fc',
  blocked: '#5a6470',
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

const SUB_H = 116; // 每个副图高度（px）

export default function ComboIndicatorPage() {
  const setTf = useStore((s) => s.setTf);
  const symbol = useStore((s) => s.symbol);
  const allCandles = useStore((s) => s.candles);

  // 方向周期（大）：独立 state，默认 4H（回测验证的最优方向周期），并同步全局 tf 以保证 ws 订阅
  const initialTf = useRef(useStore.getState().tf);
  const [bigTf, setBigTf] = useState('4h');
  useEffect(() => { setTf(bigTf); }, [bigTf]);
  useEffect(() => () => { setTf(initialTf.current); }, []);

  // 信号周期（小）：默认比方向周期低一档 → 1H
  const [signalTf, setSignalTf] = useState(() => {
    const i = ALL_TFS.indexOf(bigTf);
    return ALL_TFS[i - 1] || ALL_TFS[0];
  });

  const [show, setShow] = useState({
    ema: true, signals: true, rsi: true, adx: true, atr: true, sl: true, sig: true,
    reg: true, blocked: false,
  });
  const [opt, setOpt] = useState(RECOMMENDED);
  const [openParam, setOpenParam] = useState(false);
  const toggle = (k) => setShow((s) => ({ ...s, [k]: !s[k] }));
  const setOptField = (k, v) => setOpt((o) => ({ ...o, [k]: v }));

  const mainBox = useRef(null);
  const rsiBox = useRef(null);
  const adxBox = useRef(null);
  const atrBox = useRef(null);
  const sigBox = useRef(null);
  const R = useRef({});          // 各图表的 series 引用
  const chartsRef = useRef([]);  // 图表实例（用于时间轴联动）
  const syncing = useRef(false);
  const fitted = useRef(null);

  // 主图 = 方向周期（大）K 线
  const candles = allCandles?.[bigTf];

  // 去重 + 排序（lightweight-charts 要求时间严格递增）
  const sorted = useMemo(() => {
    if (!candles?.length) return [];
    const m = new Map();
    for (const c of candles) m.set(c.ts, c);
    return [...m.values()].sort((a, b) => a.ts - b.ts);
  }, [candles]);

  // 在前端计算组合指标 + 买卖点（参数 opt 实时可调）—— 大周期
  const calc = useMemo(() => (sorted.length ? computeCombo(sorted, opt) : null), [sorted, opt]);

  // 信号周期（小周期找点）：独立排序 + 独立组合计算
  const smallSorted = useMemo(() => {
    const sc = allCandles?.[signalTf];
    if (!sc?.length) return [];
    const m = new Map();
    for (const c of sc) m.set(c.ts, c);
    return [...m.values()].sort((a, b) => a.ts - b.ts);
  }, [allCandles, signalTf]);
  const small = useMemo(() => (smallSorted.length ? computeCombo(smallSorted, opt) : null), [smallSorted, opt]);

  // 大周期方向（用于共振过滤）
  const bigRegime = useMemo(() => (calc ? trendRegime(calc, opt) : []), [calc, opt]);

  // 把大周期方向对齐到小周期每根 K（用于在信号副图叠加显示大周期状态）
  const smallRegime = useMemo(() => {
    if (!calc || !smallSorted.length || !sorted.length) return [];
    const out = new Array(smallSorted.length).fill(0);
    for (let k = 0; k < smallSorted.length; k++) {
      const ts = smallSorted[k].ts;
      let lo = 0, hi = sorted.length - 1, j = -1;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (sorted[mid].ts <= ts) { j = mid; lo = mid + 1; } else hi = mid - 1;
      }
      out[k] = j >= 0 ? bigRegime[j] : 0;
    }
    return out;
  }, [calc, smallSorted, sorted, bigRegime]);

  // 多周期共振（硬过滤）：仅保留「小周期方向与大小周期方向一致」的持仓；其余记录为被过滤
  const resonant = useMemo(() => {
    if (!small || !calc || !smallSorted.length || !sorted.length) return { positions: [], signals: [], blocked: [] };
    const out = { positions: [], signals: [], blocked: [] };
    for (const p of small.positions) {
      const entryTs = smallSorted[p.entryI].ts;
      let lo = 0, hi = sorted.length - 1, j = -1;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (sorted[mid].ts <= entryTs) { j = mid; lo = mid + 1; } else hi = mid - 1;
      }
      const reg = j >= 0 ? bigRegime[j] : 0;
      const entrySig = { i: p.entryI, ts: entryTs, type: p.dir, kind: 'entry', score: small.scores[p.entryI] };
      if (!((p.dir === 'buy' && reg === 1) || (p.dir === 'sell' && reg === -1))) {
        out.blocked.push({ ...entrySig, reg });
        continue;
      }
      out.positions.push(p);
      out.signals.push(entrySig);
      if (p.exitI != null) {
        out.signals.push({ i: p.exitI, ts: smallSorted[p.exitI].ts, type: p.dir === 'buy' ? 'sell' : 'buy', kind: 'exit', score: small.scores[p.exitI] });
      }
    }
    return out;
  }, [small, calc, smallSorted, sorted, bigRegime]);

  // ── 创建图表（仅一次） ──
  useEffect(() => {
    const mk = (el, withTime) => createChart(el, {
      width: el.clientWidth,
      height: el.clientHeight,
      layout: { background: { color: 'transparent' }, textColor: C.text, fontSize: 11 },
      grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#00c9a755', labelBackgroundColor: '#00c9a7' },
        horzLine: { color: '#00c9a755', labelBackgroundColor: '#00c9a7' },
      },
      rightPriceScale: { borderColor: '#262626' },
      timeScale: {
        borderColor: '#262626', timeVisible: true, secondsVisible: false,
        visible: withTime,
      },
      localization: { timeFormatter: fmtBJ, dateFormatter: fmtBJDate },
    });

    const main = mk(mainBox.current, true);
    const rsi = mk(rsiBox.current, false);
    const adx = mk(adxBox.current, false);
    const atr = mk(atrBox.current, false);
    const sig = mk(sigBox.current, false);

    const candle = main.addCandlestickSeries({
      upColor: C.bull, downColor: C.bear,
      borderUpColor: C.bull, borderDownColor: C.bear,
      wickUpColor: C.bull, wickDownColor: C.bear,
      priceLineColor: '#ffffff44',
    });
    const vol = main.addHistogramSeries({ priceScaleId: 'vol' });
    main.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    const emaFast = main.addLineSeries({ color: C.emaFast, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    const emaSlow = main.addLineSeries({ color: C.emaSlow, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    // ATR 动态止损 / 止盈轨道（虚线，仅在持仓区间绘制）
    const slLine = main.addLineSeries({ color: '#e05263', lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    const tpLine = main.addLineSeries({ color: '#00c9a7', lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });

    const rsiSeries = rsi.addLineSeries({ color: C.rsi, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    rsiSeries.createPriceLine({ price: 70, color: '#4a5058', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '70' });
    rsiSeries.createPriceLine({ price: 30, color: '#4a5058', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '30' });

    const adxSeries = adx.addLineSeries({ color: C.adx, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    adxSeries.createPriceLine({ price: opt.adxMin, color: '#4a5058', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '过滤' });

    const atrSeries = atr.addLineSeries({ color: C.atr, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });

    // 信号周期打分（小周期综合分）+ 大周期方向阶梯 + 阈值线
    const sigSeries = sig.addLineSeries({ color: C.sig, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    sigSeries.createPriceLine({ price: 0, color: '#3a4048', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '0' });
    sigSeries.createPriceLine({ price: opt.buyThreshold, color: C.bull, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '多' });
    sigSeries.createPriceLine({ price: opt.sellThreshold, color: C.sell, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '空' });
    const regSeries = sig.addLineSeries({
      color: '#6c7480', lineWidth: 1, lineStyle: 2,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    });
    regSeries.createPriceLine({ price: 0, color: '#3a4048', lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: '' });

    R.current = { main, candle, vol, emaFast, emaSlow, slLine, tpLine, rsi, rsiSeries, adx, adxSeries, atr, atrSeries, sig, sigSeries, regSeries };
    const list = [main, rsi, adx, atr, sig];
    chartsRef.current = list;

    // 时间轴联动：任意图缩放/平移 → 其他图跟随（用 syncing 防止递归）
    list.forEach((src) => {
      src.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (!range || syncing.current) return;
        syncing.current = true;
        list.forEach((o) => { if (o !== src) o.timeScale().setVisibleLogicalRange(range); });
        syncing.current = false;
      });
    });

    const ro = new ResizeObserver(() => {
      list.forEach((ch, idx) => {
        const el = [mainBox, rsiBox, adxBox, atrBox, sigBox][idx].current;
        if (el) ch.applyOptions({ width: el.clientWidth, height: el.clientHeight });
      });
    });
    [mainBox, rsiBox, adxBox, atrBox, sigBox].forEach((b) => b.current && ro.observe(b.current));

    return () => {
      ro.disconnect();
      list.forEach((ch) => ch.remove());
      chartsRef.current = [];
      R.current = {};
    };
  }, []);

  // 副图显隐切换后，重新给刚显示的图设定尺寸（display:none → block 时 clientWidth 才正确）
  useEffect(() => {
    const list = chartsRef.current;
    if (!list.length) return;
    [mainBox, rsiBox, adxBox, atrBox, sigBox].forEach((b, idx) => {
      const el = b.current;
      if (el && list[idx]) list[idx].applyOptions({ width: el.clientWidth, height: el.clientHeight });
    });
  }, [show.rsi, show.adx, show.atr, show.sig, show.reg]);

  // ── 数据填充 ──
  useEffect(() => {
    const r = R.current;
    if (!r.main || !sorted.length || !calc) return;

    r.candle.setData(sorted.map((c) => ({
      time: toT(c.ts), open: c.o, high: c.h, low: c.l, close: c.c,
    })));
    r.vol.setData(sorted.map((c) => ({
      time: toT(c.ts), value: c.vol,
      color: c.c >= c.o ? '#00c9a733' : '#8b000055',
    })))

    const lineFor = (sd, arr) => {
      const out = [];
      for (let i = 0; i < sd.length; i++) {
        if (arr[i] != null) out.push({ time: toT(sd[i].ts), value: arr[i] });
      }
      return out;
    };

    r.emaFast.setData(show.ema ? lineFor(sorted, calc.emaFast) : []);
    r.emaSlow.setData(show.ema ? lineFor(sorted, calc.emaSlow) : []);

    // ATR 止损/止盈轨道：来自「小周期共振持仓」，仅在该笔持仓区间内画水平线
    const slData = [], tpData = [];
    if (show.sl) {
      for (const p of resonant.positions) {
        const end = p.exitI == null ? smallSorted.length - 1 : p.exitI;
        for (let i = p.entryI; i <= end && i < smallSorted.length; i++) {
          slData.push({ time: toT(smallSorted[i].ts), value: p.stop });
          tpData.push({ time: toT(smallSorted[i].ts), value: p.tp });
        }
      }
    }
    r.slLine.setData(slData);
    r.tpLine.setData(tpData);

    r.rsiSeries.setData(show.rsi ? lineFor(sorted, calc.rsi) : []);
    r.adxSeries.setData(show.adx ? lineFor(sorted, calc.adx) : []);
    r.atrSeries.setData(show.atr ? lineFor(sorted, calc.atr) : []);

    // 信号分曲线 + 大周期方向阶梯（多=+0.92 绿 / 空=-0.92 红 / 震荡=0 灰）
    r.sigSeries.setData(show.sig && small ? lineFor(smallSorted, small.scores) : []);
    r.regSeries.setData(
      show.reg && smallRegime.length
        ? smallSorted.map((c, i) => ({ time: toT(c.ts), value: smallRegime[i] * 0.92 }))
        : [],
    );

    // 买卖点（多周期共振过滤后）：entry 放大、exit 缩小，颜色区分
    const blockedMarkers = (show.blocked ? resonant.blocked : []).map((s) => ({
      time: toT(s.ts),
      position: s.type === 'buy' ? 'belowBar' : 'aboveBar',
      color: C.blocked,
      shape: s.type === 'buy' ? 'arrowUp' : 'arrowDown',
      text: '',
      size: 1,
    }));
    const liveMarkers = (show.signals ? resonant.signals : []).map((s) => ({
      time: toT(s.ts),
      position: s.type === 'buy' ? 'belowBar' : 'aboveBar',
      color: s.kind === 'entry'
        ? (s.type === 'buy' ? C.buy : C.sell)
        : C.exit,
      shape: s.type === 'buy' ? 'arrowUp' : 'arrowDown',
      text: s.kind === 'entry'
        ? (s.type === 'buy' ? 'BUY' : 'SELL')
        : (s.type === 'buy' ? 'COVER' : 'EXIT'),
      size: s.kind === 'entry' ? 2 : 1,
    }));
    r.candle.setMarkers(
      [...liveMarkers, ...blockedMarkers].sort((a, b) => a.time - b.time),
    );

    if (fitted.current !== bigTf) {
      fitted.current = bigTf;
      const n = sorted.length;
      r.main.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 300), to: n + 6 });
    }
  }, [sorted, calc, small, smallSorted, smallRegime, resonant, show.ema, show.signals, show.rsi, show.adx, show.atr, show.sl, show.sig, show.reg, show.blocked, bigTf]);

  const Toggle = ({ k, label, color }) => (
    <button onClick={() => toggle(k)} style={{
      ...pill, borderColor: color, color, opacity: show[k] ? 1 : 0.32,
    }}>{label}</button>
  );

  return (
    <div style={root}>
      {/* 顶部控制条 */}
      <div style={bar}>
        <div style={title}>
          综合指标 · 多周期共振
          <span style={{ color: '#4a5058', marginLeft: 8, fontSize: 11 }}>
            {symbol} · 大{bigTf}/小{signalTf}
          </span>
        </div>

        <div style={{ display: 'flex', gap: 4, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={tfLabel}>大周期·定方向</span>
          {ALL_TFS.map((t) => (
            <button key={t} onClick={() => setBigTf(t)} style={{
              ...tfBtn, color: t === bigTf ? '#00c9a7' : '#6c7480',
              borderColor: t === bigTf ? '#00c9a7' : '#262626',
              background: t === bigTf ? '#00c9a714' : 'transparent',
            }}>{t}</button>
          ))}
        </div>

        <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
          <span style={tfLabel}>小周期·找点</span>
          <select value={signalTf} onChange={(e) => setSignalTf(e.target.value)} style={selectStyle}>
            {ALL_TFS.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>

        <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
          <Toggle k="ema" label="EMA20/60" color={C.emaFast} />
          <Toggle k="signals" label="买卖点" color={C.bull} />
          <Toggle k="sl" label="止损/止盈" color={C.sell} />
          <Toggle k="sig" label="信号分" color={C.sig} />
          <Toggle k="reg" label="大周期方向" color={C.text} />
          <Toggle k="blocked" label="被过滤" color={C.blocked} />
          <Toggle k="rsi" label="RSI" color={C.rsi} />
          <Toggle k="adx" label="ADX" color={C.adx} />
          <Toggle k="atr" label="ATR" color={C.atr} />
          <button onClick={() => setOpenParam((o) => !o)} style={{
            ...pill, borderColor: '#a78bfa', color: '#a78bfa', opacity: openParam ? 1 : 0.32,
          }}>参数</button>
        </div>
      </div>

      {/* 参数面板（实时可调） */}
      {openParam && (
        <div style={paramWrap}>
          {PARAM_GROUPS.map((g) => (
            <div style={paramGroup} key={g.title}>
              <div style={paramGroupTitle}>{g.title}</div>
              <div style={paramFields}>
                {g.fields.map((f) => (
                  <NumField key={f.k} label={f.label} value={opt[f.k]} step={f.step} min={f.min}
                    onChange={(v) => setOptField(f.k, v)} />
                ))}
              </div>
            </div>
          ))}
          <button onClick={() => setOpt(RECOMMENDED)} style={resetBtn}>重置推荐</button>
        </div>
      )}

      {/* 主图 */}
      <div style={{ ...pane, flex: 1, minHeight: 0, position: 'relative' }}>
        <div ref={mainBox} style={{ width: '100%', height: '100%' }} />
        {calc && <InfoCard big={calc} small={small} bigRegime={bigRegime} smallSorted={smallSorted} resonant={resonant} opt={opt} />}
      </div>

      {/* 副图 */}
      {show.rsi && <div style={{ ...pane, height: SUB_H, flexShrink: 0 }}>
        <div ref={rsiBox} style={{ width: '100%', height: '100%' }} />
      </div>}
      {show.adx && <div style={{ ...pane, height: SUB_H, flexShrink: 0 }}>
        <div ref={adxBox} style={{ width: '100%', height: '100%' }} />
      </div>}
      {show.atr && <div style={{ ...pane, height: SUB_H, flexShrink: 0 }}>
        <div ref={atrBox} style={{ width: '100%', height: '100%' }} />
      </div>}
      {show.sig && <div style={{ ...pane, height: SUB_H, flexShrink: 0 }}>
        <div ref={sigBox} style={{ width: '100%', height: '100%' }} />
      </div>}
    </div>
  );
}

function InfoCard({ big, small, bigRegime, smallSorted, resonant, opt }) {
  const bi = big ? big.scores.length - 1 : -1;
  const si = small ? small.scores.length - 1 : -1;
  const bScore = bi >= 0 ? big.scores[bi] : null;
  const sScore = si >= 0 ? small.scores[si] : null;
  const bReg = bi >= 0 ? (bigRegime[bi] ?? 0) : 0;
  let resText = '—', resColor = '#8b93a0';
  if (sScore != null) {
    if (bReg === 1 && sScore >= opt.buyThreshold) { resText = '共振多'; resColor = C.bull; }
    else if (bReg === -1 && sScore <= opt.sellThreshold) { resText = '共振空'; resColor = C.sell; }
    else if (bReg === 0) { resText = '大周期震荡'; resColor = '#8b93a0'; }
    else { resText = '不共振(已过滤)'; resColor = C.exit; }
  }
  const buy = resonant.signals.filter((s) => s.kind === 'entry' && s.type === 'buy').length;
  const sell = resonant.signals.filter((s) => s.kind === 'entry' && s.type === 'sell').length;
  const blocked = resonant.blocked.length;
  const scColor = sScore == null ? '#6c7480'
    : sScore >= opt.buyThreshold ? C.bull
    : sScore <= opt.sellThreshold ? C.sell : '#8b93a0';
  return (
    <div style={info}>
      <div style={{ display: 'flex', gap: 14, alignItems: 'center' }}>
        <span style={{ fontWeight: 800, color: scColor }}>
          信号分 {sScore == null ? '—' : sScore.toFixed(2)}
        </span>
        <span style={{ color: resColor, fontSize: 11, fontWeight: 700 }}>{resText}</span>
      </div>
      <div style={{ ...badge, color: '#f5a623', borderColor: '#f5a62355', background: '#f5a62314' }}>
        硬过滤共振 · 风控/方向参考
      </div>
      <div style={kv}>
        方向分 <b style={{ color: bScore == null ? '#6c7480' : '#e9ecef' }}>{bScore == null ? '—' : bScore.toFixed(2)}</b>
        {'  '}大周期 <b style={{ color: bReg === 1 ? C.bull : bReg === -1 ? C.sell : '#8b93a0' }}>
          {bReg === 1 ? '多' : bReg === -1 ? '空' : '震荡'}</b>
      </div>
      <div style={kv}>
        ADX <b style={{ color: bi >= 0 && big.adx[bi] != null ? (big.adx[bi] >= opt.adxMin ? C.bull : '#8b93a0') : '#6c7480' }}>
          {bi >= 0 && big.adx[bi] != null ? big.adx[bi].toFixed(1) : '—'}</b>
        <span style={{ color: '#3a4048' }}> / 过滤 {opt.adxMin}</span>
      </div>
      <div style={{ ...kv, color: C.bull }}>
        共振开多 {buy} <span style={{ color: C.sell, marginLeft: 10 }}>共振开空 {sell}</span>
        <span style={{ color: C.blocked, marginLeft: 10 }}>过滤 {blocked}</span>
      </div>
    </div>
  );
}

const root = {
  display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden',
  background: 'var(--bg)',
};
const bar = {
  display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap',
  padding: '8px 12px', borderBottom: '1px solid var(--border)',
  background: 'var(--card)', flexShrink: 0,
};
const title = { fontSize: 13, fontWeight: 800, color: '#00c9a7', fontFamily: 'var(--font-mono)' };
const tfBtn = {
  background: 'transparent', borderWidth: 1, borderStyle: 'solid',
  borderRadius: 4, cursor: 'pointer', fontSize: 10.5, fontWeight: 700,
  padding: '3px 7px', transition: 'all .15s',
};
const tfLabel = { fontSize: 10, color: '#8b93a0', fontWeight: 700, marginRight: 2 };
const selectStyle = {
  background: 'var(--surface)', color: '#e9ecef', border: '1px solid #262626',
  borderRadius: 4, fontSize: 10.5, fontWeight: 700, padding: '3px 6px', cursor: 'pointer',
};
const pill = {
  background: '#000000bb', borderWidth: 1, borderStyle: 'solid',
  borderRadius: 3, cursor: 'pointer', fontSize: 10, fontWeight: 700,
  padding: '2px 8px', transition: 'opacity .15s',
};
const pane = {
  background: 'var(--surface)', borderBottom: '1px solid var(--border)',
  position: 'relative',
};
const info = {
  position: 'absolute', top: 8, left: 10, zIndex: 5,
  background: '#000000cc', border: '1px solid #ffffff14', borderRadius: 4,
  padding: '6px 10px', fontSize: 11, display: 'flex', flexDirection: 'column', gap: 3,
  fontFamily: 'var(--font-mono)',
};
const badge = {
  alignSelf: 'flex-start', fontSize: 9.5, fontWeight: 700, borderRadius: 3,
  padding: '1px 6px', borderWidth: 1, borderStyle: 'solid',
};
const kv = { color: '#6c7480', fontSize: 10.5 };

// ── 参数面板 ──
const PARAM_GROUPS = [
  { title: '均线', fields: [
    { k: 'fastLen', label: 'EMA快', step: 1, min: 2 },
    { k: 'slowLen', label: 'EMA慢', step: 1, min: 3 },
  ] },
  { title: 'RSI', fields: [
    { k: 'rsiLen', label: 'RSI周期', step: 1, min: 2 },
    { k: 'rsiLongMin', label: '多下界', step: 1 },
    { k: 'rsiLongMax', label: '多上界', step: 1 },
    { k: 'rsiShortMin', label: '空下界', step: 1 },
    { k: 'rsiShortMax', label: '空上界', step: 1 },
  ] },
  { title: '波动/ATR', fields: [
    { k: 'atrLen', label: 'ATR周期', step: 1, min: 2 },
    { k: 'atrPctMin', label: '波动门槛', step: 0.0005 },
    { k: 'slMult', label: '止损×ATR', step: 0.5, min: 0.5 },
    { k: 'tpMult', label: '止盈×ATR', step: 0.5, min: 0.5 },
  ] },
  { title: 'ADX', fields: [
    { k: 'adxLen', label: 'ADX周期', step: 1, min: 2 },
    { k: 'adxMin', label: '趋势门槛', step: 1, min: 0 },
  ] },
  { title: '量能', fields: [
    { k: 'volLen', label: '量MA', step: 1, min: 2 },
  ] },
  { title: '触发', fields: [
    { k: 'buyThreshold', label: '开多分', step: 0.05 },
    { k: 'sellThreshold', label: '开空分', step: 0.05 },
  ] },
];

function NumField({ label, value, onChange, step = 1, min }) {
  return (
    <label style={field}>
      <span style={fieldLabel}>{label}</span>
      <input
        type="number" value={value ?? ''} step={step} min={min}
        onChange={(e) => {
          const v = e.target.value === '' ? 0 : Number(e.target.value);
          if (!Number.isNaN(v)) onChange(v);
        }}
        style={fieldInput}
      />
    </label>
  );
}

const paramWrap = {
  display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-end',
  padding: '10px 12px', borderBottom: '1px solid var(--border)',
  background: 'var(--card)', flexShrink: 0,
};
const paramGroup = { display: 'flex', flexDirection: 'column', gap: 5 };
const paramGroupTitle = { fontSize: 10, fontWeight: 700, color: '#8b93a0', letterSpacing: 0.5 };
const paramFields = { display: 'flex', gap: 6, flexWrap: 'wrap' };
const field = { display: 'flex', flexDirection: 'column', gap: 2 };
const fieldLabel = { fontSize: 9.5, color: '#6c7480' };
const fieldInput = {
  width: 56, background: 'var(--surface)', color: '#e9ecef',
  border: '1px solid var(--border)', borderRadius: 3, padding: '3px 5px',
  fontSize: 11, fontFamily: 'var(--font-mono)',
};
const resetBtn = {
  background: 'transparent', border: '1px solid #a78bfa', color: '#a78bfa',
  borderRadius: 4, cursor: 'pointer', fontSize: 10.5, fontWeight: 700, padding: '5px 10px',
};
