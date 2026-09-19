import { useState } from 'react';
import { ALL_TFS, useStore } from '../stores/useStore';
import CandleChart from '../components/CandleChart';
import SymbolSelector from '../components/SymbolSelector';
import './ResearchPage.css';

export default function ResearchPage() {
  const [activeSection, setActiveSection] = useState('visual');
  const [flips, setFlips] = useState([]);
  const [gateConfig, setGateConfig] = useState({
    gateA: { enabled: true, bodyAtrThreshold: 1.5 },
    gateB: { enabled: true, densityThreshold: 0.15, lookback: 20 },
    gateC: { enabled: true, wickRatioThreshold: 0.6 },
    gateD: { enabled: true, trendAgeThreshold: 50 },
    gateE: { enabled: true, gapRatioThreshold: 0.5 },
  });

  const symbol = useStore((s) => s.symbol);
  const tf = useStore((s) => s.tf);
  const candles = useStore((s) => s.candles[tf]);
  const signals = useStore((s) => s.signals);

  // 从当前图表提取 Flip 事件
  const extractFlips = () => {
    if (!candles || candles.length === 0) {
      alert('当前周期暂无K线数据，请稍候或切换周期');
      return;
    }

    const tfSignals = signals.filter(s => s.tf === tf && s.symbol === symbol);
    if (tfSignals.length === 0) {
      alert('当前周期暂无信号');
      return;
    }

    // 计算 ATR（Average True Range）
    const calculateATR = (candles, period = 14) => {
      const trs = [];
      for (let i = 1; i < candles.length; i++) {
        const high = candles[i].h;
        const low = candles[i].l;
        const prevClose = candles[i - 1].c;
        const tr = Math.max(
          high - low,
          Math.abs(high - prevClose),
          Math.abs(low - prevClose)
        );
        trs.push(tr);
      }

      const atrValues = [];
      for (let i = 0; i < trs.length; i++) {
        if (i < period - 1) {
          atrValues.push(null);
        } else {
          const sum = trs.slice(i - period + 1, i + 1).reduce((a, b) => a + b, 0);
          atrValues.push(sum / period);
        }
      }
      return atrValues;
    };

    const atrValues = calculateATR(candles);

    const extracted = tfSignals.map((sig, idx) => {
      // 找到信号对应的 K 线索引
      const candleIdx = candles.findIndex(c => c.ts === sig.ts);
      if (candleIdx < 0) return null;

      const candle = candles[candleIdx];
      const atr = atrValues[candleIdx - 1] || 1; // 使用前一根的ATR

      // A. Body/ATR 比率
      const body = Math.abs(candle.c - candle.o);
      const bodyAtrRatio = body / atr;

      // B. Flip Density (最近N根K线内的Flip密度)
      const lookback = 20;
      const recentFlips = tfSignals.filter(s => {
        const sIdx = candles.findIndex(c => c.ts === s.ts);
        return sIdx >= candleIdx - lookback && sIdx < candleIdx;
      });
      const flipDensity = recentFlips.length / lookback;

      // C. Wick/Body 比率
      const upperWick = candle.h - Math.max(candle.o, candle.c);
      const lowerWick = Math.min(candle.o, candle.c) - candle.l;
      const totalWick = upperWick + lowerWick;
      const wickRatio = body > 0 ? totalWick / body : 0;

      // D. 趋势年龄（Flip前持续了多少根K线）
      let trendAge = 1;
      const direction = sig.action === 'buy' ? 'down' : 'up'; // Flip前的趋势方向
      for (let i = candleIdx - 1; i >= 0; i--) {
        const prevCandle = candles[i];
        const isDownTrend = prevCandle.c < prevCandle.o;
        if ((direction === 'down' && isDownTrend) || (direction === 'up' && !isDownTrend)) {
          trendAge++;
        } else {
          break;
        }
      }

      // E. Gap/ATR 比率（Flip K线的跳空）
      let gapRatio = 0;
      if (candleIdx > 0) {
        const prevCandle = candles[candleIdx - 1];
        const gap = sig.action === 'buy'
          ? Math.max(0, candle.l - prevCandle.h)  // 向上跳空
          : Math.max(0, prevCandle.l - candle.h); // 向下跳空
        gapRatio = gap / atr;
      }

      return {
        id: `flip_${sig.ts}_${idx}`,
        ts: sig.ts,
        time: new Date(sig.ts).toLocaleString('zh-CN'),
        direction: sig.action === 'buy' ? 'long' : 'short',
        price: sig.price,
        symbol: sig.symbol,
        tf: sig.tf,
        // 特征值
        bodyAtrRatio: parseFloat(bodyAtrRatio.toFixed(2)),
        flipDensity: parseFloat(flipDensity.toFixed(2)),
        wickRatio: parseFloat(wickRatio.toFixed(2)),
        trendAge,
        gapRatio: parseFloat(gapRatio.toFixed(2)),
        atr: parseFloat(atr.toFixed(2)),
        // 原始K线数据（用于后续分析）
        candle: {
          o: candle.o,
          h: candle.h,
          l: candle.l,
          c: candle.c,
        },
        status: 'pending',
        passedGates: [],
      };
    }).filter(Boolean);

    setFlips(prevFlips => [...prevFlips, ...extracted]);
    alert(`成功提取 ${extracted.length} 条 Flip 数据`);
  };

  const sections = [
    { key: 'visual', label: 'K线信号可视化', icon: '📊' },
    { key: 'dataset', label: 'Flip 数据集', icon: '📁' },
    { key: 'gates', label: '质量门控', icon: '🚪' },
    { key: 'confirm', label: '确认机制', icon: '✓' },
    { key: 'stats', label: '统计分析', icon: '📈' },
    { key: 'cross-symbol', label: '跨品种验证', icon: '🔀' },
    { key: 'dynamic-st', label: '动态 SuperTrend', icon: '⚡' },
    { key: 'mtf', label: 'MTF SuperTrend', icon: '🔗' },
    { key: 'backtest', label: '回测验证', icon: '🔬' },
  ];

  return (
    <div style={sty.root}>
      <header style={sty.header}>
        <div style={sty.headerTop}>
          <div>
            <h1 style={sty.title}>超级趋势研究工作台</h1>
            <p style={sty.subtitle}>
              Flip Event → Quality Gates → Confirmation → Trade Signal
            </p>
          </div>
          <div style={sty.headerControls}>
            <span style={{
              width: 6, height: 6, borderRadius: '50%',
              background: connected ? '#00c9a7' : '#e05263',
              marginRight: 8,
            }} title={connected ? '已连接' : '连接断开'} />
            <SymbolSelector />
            <span style={{ color: '#4a5058', margin: '0 8px' }}>|</span>
            <div style={sty.tfSelector}>
              {ALL_TFS.map((t) => (
                <button
                  key={t}
                  onClick={() => setTf(t)}
                  style={{
                    ...sty.tfBtn,
                    ...(tf === t ? sty.tfBtnActive : {}),
                  }}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>
        </div>
      </header>

      <div style={sty.body}>
        <aside style={sty.sidebar}>
          <div style={sty.sidebarTitle}>研究模块</div>
          {sections.map((sec) => (
            <button
              key={sec.key}
              onClick={() => setActiveSection(sec.key)}
              style={{
                ...sty.navBtn,
                ...(activeSection === sec.key ? sty.navBtnActive : {}),
              }}
            >
              <span style={sty.navIcon}>{sec.icon}</span>
              <span style={sty.navLabel}>{sec.label}</span>
            </button>
          ))}
        </aside>

        <main style={sty.content}>
          <SectionContent
            section={activeSection}
            flips={flips}
            gateConfig={gateConfig}
            setGateConfig={setGateConfig}
            onExtractFlips={extractFlips}
          />
        </main>
      </div>
    </div>
  );
}

function SectionContent({ section, flips, gateConfig, setGateConfig, onExtractFlips }) {
  switch (section) {
    case 'visual':
      return <VisualSection flips={flips} gateConfig={gateConfig} />;
    case 'dataset':
      return <DatasetSection flips={flips} onExtract={onExtractFlips} />;
    case 'gates':
      return <GatesSection config={gateConfig} setConfig={setGateConfig} flips={flips} />;
    case 'confirm':
      return <ConfirmSection flips={flips} />;
    case 'stats':
      return <StatsSection flips={flips} />;
    case 'cross-symbol':
      return <CrossSymbolSection flips={flips} />;
    case 'dynamic-st':
      return <DynamicSTSection />;
    case 'mtf':
      return <MTFSection />;
    case 'backtest':
      return <BacktestSection />;
    default:
      return <VisualSection flips={flips} gateConfig={gateConfig} />;
  }
}

// ===== Visual Section =====
function VisualSection({ flips, gateConfig }) {
  const signals = useStore((s) => s.signals);
  const tf = useStore((s) => s.tf);
  const symbol = useStore((s) => s.symbol);
  const tfSignals = signals.filter(s => s.tf === tf && s.symbol === symbol);

  // 应用 Gate 过滤
  const flipsWithGateInfo = flips.map(flip => ({
    ...flip,
    gateInfo: applyGateFilters(flip, gateConfig),
  }));
  const filteredFlips = flipsWithGateInfo.filter(f => f.gateInfo.passed);
  const confirmedFlips = filteredFlips.filter(f => f.status === 'confirmed');

  return (
    <div style={sty.section}>
      <h2 style={sty.sectionTitle}>K线信号可视化</h2>
      <p style={sty.sectionIntro}>
        直观展示策略的买卖点信号,验证 Gate 过滤效果。
      </p>

      <Card title="实时K线图表">
        <div style={sty.chartContainer}>
          <CandleChart />
        </div>
        <div style={sty.chartHint}>
          💡 图表展示当前币种和周期的 SuperTrend 信号，可在页面顶部切换品种和时间周期
        </div>
      </Card>

      <Card title="信号统计">
        <div style={sty.statsGrid}>
          <StatBox label="总信号数" value={tfSignals.length} color="#8b93a0" />
          <StatBox
            label="买入信号"
            value={tfSignals.filter(s => s.action === 'buy').length}
            color="#00c9a7"
          />
          <StatBox
            label="卖出信号"
            value={tfSignals.filter(s => s.action === 'sell').length}
            color="#e05263"
          />
          <StatBox label="当前周期" value={tf} color="#4e8aff" />
        </div>
      </Card>

      <Card title="最近信号列表">
        <SignalListTable signals={tfSignals.slice(0, 10)} />
      </Card>

      <Card title="Gate 过滤对比">
        <div style={sty.filterCompare}>
          <div style={sty.filterBox}>
            <div style={sty.filterLabel}>原始信号</div>
            <div style={sty.filterValue}>{flips.length}</div>
            <div style={sty.filterDesc}>所有 SuperTrend Flip</div>
          </div>
          <div style={sty.filterArrow}>→</div>
          <div style={sty.filterBox}>
            <div style={sty.filterLabel}>Gate 过滤后</div>
            <div style={sty.filterValue}>{filteredFlips.length}</div>
            <div style={sty.filterDesc}>
              高质量信号 ({flips.length > 0 ? ((filteredFlips.length / flips.length) * 100).toFixed(0) : 0}%)
            </div>
          </div>
          <div style={sty.filterArrow}>→</div>
          <div style={sty.filterBox}>
            <div style={sty.filterLabel}>确认后</div>
            <div style={sty.filterValue}>{confirmedFlips.length}</div>
            <div style={sty.filterDesc}>可交易信号</div>
          </div>
        </div>
        {flips.length === 0 && (
          <div style={sty.hint}>
            💡 请先在「Flip 数据集」页面提取数据
          </div>
        )}
      </Card>

      {flips.length > 0 && (
        <Card title="Flip 列表（带 Gate 状态）">
          <FlipTableWithGates flips={flipsWithGateInfo} />
        </Card>
      )}
    </div>
  );
}

// ===== Dataset Section =====
function DatasetSection({ flips, onExtract }) {
  const signals = useStore((s) => s.signals);
  const tf = useStore((s) => s.tf);

  const handleExport = () => {
    const dataStr = JSON.stringify(flips, null, 2);
    const dataBlob = new Blob([dataStr], { type: 'application/json' });
    const url = URL.createObjectURL(dataBlob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `flips_${Date.now()}.json`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const handleImport = () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = (e) => {
      const file = e.target.files[0];
      if (file) {
        const reader = new FileReader();
        reader.onload = (evt) => {
          try {
            const imported = JSON.parse(evt.target.result);
            // TODO: 合并导入的数据到 flips
            alert(`成功导入 ${imported.length} 条数据`);
          } catch (err) {
            alert('导入失败：' + err.message);
          }
        };
        reader.readAsText(file);
      }
    };
    input.click();
  };

  return (
    <div style={sty.section}>
      <h2 style={sty.sectionTitle}>Flip 数据集构建</h2>
      <p style={sty.sectionIntro}>
        第一步：从历史数据中提取所有 SuperTrend Flip 事件，为每个 Flip 计算特征。
      </p>

      <Card title="数据集概览">
        <div style={sty.statsGrid}>
          <StatBox label="总 Flip 数" value={flips.length} color="#00c9a7" />
          <StatBox label="看涨 Flip" value={flips.filter(f => f.direction === 'long').length} color="#4e8aff" />
          <StatBox label="看跌 Flip" value={flips.filter(f => f.direction === 'short').length} color="#e05263" />
          <StatBox label="当前信号" value={signals.filter(s => s.tf === tf).length} color="#8b93a0" />
        </div>
      </Card>

      <Card title="Flip 特征结构">
        <FeatureTree />
      </Card>

      <Card title="采集控制">
        <div style={sty.actionRow}>
          <button style={sty.btnPrimary} onClick={onExtract}>📥 从当前图表采集 Flip</button>
          <button style={sty.btnSecondary} onClick={handleImport}>📂 导入历史数据</button>
          <button style={sty.btnSecondary} onClick={handleExport} disabled={flips.length === 0}>💾 导出数据集</button>
        </div>
        <div style={sty.hint}>
          💡 提示：切换到「首页」加载不同币种和时间周期，然后返回这里采集 Flip 数据
        </div>
      </Card>

      {flips.length > 0 && (
        <Card title="最近的 Flip 事件">
          <FlipTable flips={flips.slice(0, 10)} />
        </Card>
      )}
    </div>
  );
}

// 计算 Flip 统计数据
function calculateFlipStats(flips) {
  const bodyAtrValues = flips.map(f => f.bodyAtrRatio);
  const densityValues = flips.map(f => f.flipDensity);
  const wickRatioValues = flips.map(f => f.wickRatio);
  const trendAgeValues = flips.map(f => f.trendAge);
  const gapRatioValues = flips.map(f => f.gapRatio);

  const calculateDistribution = (values, binCount = 10) => {
    const min = Math.min(...values);
    const max = Math.max(...values);
    const binSize = (max - min) / binCount;

    const bins = Array(binCount).fill(0).map((_, i) => ({
      start: min + i * binSize,
      end: min + (i + 1) * binSize,
      count: 0,
    }));

    values.forEach(v => {
      const binIndex = Math.min(Math.floor((v - min) / binSize), binCount - 1);
      bins[binIndex].count++;
    });

    return bins;
  };

  const median = (values) => {
    const sorted = [...values].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
  };

  const avg = (values) => values.reduce((a, b) => a + b, 0) / values.length;

  return {
    bodyAtrDistribution: calculateDistribution(bodyAtrValues),
    bodyAtrAvg: avg(bodyAtrValues),
    bodyAtrMedian: median(bodyAtrValues),
    bodyAtrMax: Math.max(...bodyAtrValues),

    densityDistribution: calculateDistribution(densityValues),
    densityAvg: avg(densityValues),
    densityMedian: median(densityValues),
    densityMax: Math.max(...densityValues),

    wickRatioDistribution: calculateDistribution(wickRatioValues),
    wickRatioAvg: avg(wickRatioValues),
    wickRatioMedian: median(wickRatioValues),
    wickRatioMax: Math.max(...wickRatioValues),

    trendAgeDistribution: calculateDistribution(trendAgeValues, 8),
    trendAgeAvg: avg(trendAgeValues),
    trendAgeMedian: median(trendAgeValues),
    trendAgeMax: Math.max(...trendAgeValues),

    gapRatioDistribution: calculateDistribution(gapRatioValues),
    gapRatioAvg: avg(gapRatioValues),
    gapRatioMedian: median(gapRatioValues),
    gapRatioMax: Math.max(...gapRatioValues),
  };
}

// 特征分布图组件
function FeatureDistributionChart({ data, feature, color }) {
  const maxCount = Math.max(...data.map(d => d.count));

  return (
    <div style={sty.chartContainer}>
      <div style={sty.histogram}>
        {data.map((bin, i) => (
          <div key={i} style={sty.histogramBar}>
            <div
              style={{
                ...sty.histogramBarFill,
                height: `${(bin.count / maxCount) * 100}%`,
                background: color,
              }}
              title={`${bin.start.toFixed(2)} - ${bin.end.toFixed(2)}: ${bin.count} 个`}
            />
            <div style={sty.histogramBarLabel}>
              {bin.start.toFixed(1)}
            </div>
          </div>
        ))}
      </div>
      <div style={sty.chartXAxis}>{feature} 值</div>
    </div>
  );
}

// 相关性矩阵组件
function CorrelationMatrix({ flips }) {
  const features = [
    { key: 'bodyAtrRatio', label: 'Body/ATR' },
    { key: 'flipDensity', label: 'Density' },
    { key: 'wickRatio', label: 'Wick Ratio' },
    { key: 'trendAge', label: '趋势年龄' },
    { key: 'gapRatio', label: 'Gap/ATR' },
  ];

  const calculateCorrelation = (key1, key2) => {
    const values1 = flips.map(f => f[key1]);
    const values2 = flips.map(f => f[key2]);

    const mean1 = values1.reduce((a, b) => a + b, 0) / values1.length;
    const mean2 = values2.reduce((a, b) => a + b, 0) / values2.length;

    let numerator = 0;
    let sum1 = 0;
    let sum2 = 0;

    for (let i = 0; i < values1.length; i++) {
      const diff1 = values1[i] - mean1;
      const diff2 = values2[i] - mean2;
      numerator += diff1 * diff2;
      sum1 += diff1 * diff1;
      sum2 += diff2 * diff2;
    }

    return numerator / Math.sqrt(sum1 * sum2);
  };

  return (
    <div style={sty.correlationMatrix}>
      <div style={sty.correlationRow}>
        <div style={sty.correlationCell}></div>
        {features.map(f => (
          <div key={f.key} style={sty.correlationHeaderCell}>{f.label}</div>
        ))}
      </div>
      {features.map((f1, i) => (
        <div key={f1.key} style={sty.correlationRow}>
          <div style={sty.correlationHeaderCell}>{f1.label}</div>
          {features.map((f2, j) => {
            const corr = i === j ? 1 : calculateCorrelation(f1.key, f2.key);
            const absCorr = Math.abs(corr);
            const bgColor = corr > 0
              ? `rgba(0, 201, 167, ${absCorr})`
              : `rgba(224, 82, 99, ${absCorr})`;

            return (
              <div
                key={f2.key}
                style={{
                  ...sty.correlationCell,
                  background: bgColor,
                  color: absCorr > 0.5 ? '#fff' : '#e8eaed',
                }}
                title={`${f1.label} vs ${f2.label}: ${corr.toFixed(2)}`}
              >
                {corr.toFixed(2)}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

// Gate 过滤逻辑（供各处复用）
// 返回 { passed: boolean, results: {gateA: boolean, ...}, failedGates: string[] }
function applyGateFilters(flip, config) {
  const results = {
    gateA: !config.gateA.enabled || flip.bodyAtrRatio <= config.gateA.bodyAtrThreshold,
    gateB: !config.gateB.enabled || flip.flipDensity <= config.gateB.densityThreshold,
    gateC: !config.gateC.enabled || flip.wickRatio <= config.gateC.wickRatioThreshold,
    gateD: !config.gateD.enabled || flip.trendAge <= config.gateD.trendAgeThreshold,
    gateE: !config.gateE.enabled || flip.gapRatio <= config.gateE.gapRatioThreshold,
  };

  const failedGates = [];
  if (config.gateA.enabled && !results.gateA) {
    failedGates.push(`Gate A: Body/ATR=${flip.bodyAtrRatio.toFixed(2)} > ${config.gateA.bodyAtrThreshold} (追高风险)`);
  }
  if (config.gateB.enabled && !results.gateB) {
    failedGates.push(`Gate B: Density=${flip.flipDensity.toFixed(2)} > ${config.gateB.densityThreshold} (震荡)`);
  }
  if (config.gateC.enabled && !results.gateC) {
    failedGates.push(`Gate C: Wick/Body=${flip.wickRatio.toFixed(2)} > ${config.gateC.wickRatioThreshold} (犹豫)`);
  }
  if (config.gateD.enabled && !results.gateD) {
    failedGates.push(`Gate D: 趋势年龄=${flip.trendAge} > ${config.gateD.trendAgeThreshold} (成熟)`);
  }
  if (config.gateE.enabled && !results.gateE) {
    failedGates.push(`Gate E: Gap/ATR=${flip.gapRatio.toFixed(2)} > ${config.gateE.gapRatioThreshold} (跳空)`);
  }

  const passed = Object.values(results).every(r => r);

  return {
    passed,
    results,
    failedGates,
    passedCount: Object.values(results).filter(r => r).length,
    totalGates: Object.keys(results).filter(k => config[k].enabled).length,
  };
}

// ===== Gates Section =====
function GatesSection({ config, setConfig, flips }) {
  const passedCount = flips.filter(flip => applyGateFilters(flip, config)).length;
  const rejectRate = flips.length > 0 ? ((flips.length - passedCount) / flips.length * 100).toFixed(1) : 0;

  return (
    <div style={sty.section}>
      <h2 style={sty.sectionTitle}>质量门控配置</h2>
      <p style={sty.sectionIntro}>
        通过 5 道 Gate 过滤低质量 Flip，每道 Gate 针对一个具体问题。
      </p>

      <Card title="过滤效果">
        <div style={sty.statsGrid}>
          <StatBox label="原始 Flip" value={flips.length} color="#8b93a0" />
          <StatBox label="通过 Gate" value={passedCount} color="#00c9a7" />
          <StatBox label="拒绝率" value={`${rejectRate}%`} color="#e05263" />
        </div>
      </Card>

      <GateControl
        gate="A"
        title="Body/ATR 异常检测"
        problem="突发大 K 线追高"
        color="#e05263"
        enabled={config.gateA.enabled}
        onToggle={(v) => setConfig({ ...config, gateA: { ...config.gateA, enabled: v } })}
      >
        <SliderControl
          label="Body/ATR 阈值"
          value={config.gateA.bodyAtrThreshold}
          min={0.5}
          max={3}
          step={0.1}
          onChange={(v) => setConfig({ ...config, gateA: { ...config.gateA, bodyAtrThreshold: v } })}
        />
        <div style={sty.gateDesc}>
          当 Flip K 线的 body/ATR &gt; {config.gateA.bodyAtrThreshold} 时拒绝（追高风险）
        </div>
      </GateControl>

      <GateControl
        gate="B"
        title="Flip Density 检测"
        problem="震荡行情反复翻转"
        color="#f5a623"
        enabled={config.gateB.enabled}
        onToggle={(v) => setConfig({ ...config, gateB: { ...config.gateB, enabled: v } })}
      >
        <SliderControl
          label="密度阈值"
          value={config.gateB.densityThreshold}
          min={0.05}
          max={0.3}
          step={0.05}
          onChange={(v) => setConfig({ ...config, gateB: { ...config.gateB, densityThreshold: v } })}
        />
        <SliderControl
          label="回看周期"
          value={config.gateB.lookback}
          min={10}
          max={50}
          step={5}
          onChange={(v) => setConfig({ ...config, gateB: { ...config.gateB, lookback: v } })}
        />
        <div style={sty.gateDesc}>
          最近 {config.gateB.lookback} 根 K 线内 Flip 次数 / {config.gateB.lookback} &gt; {config.gateB.densityThreshold} 时拒绝
        </div>
      </GateControl>

      <GateControl
        gate="C"
        title="Wick 穿刺检测"
        problem="影线假突破"
        color="#a78bfa"
        enabled={config.gateC.enabled}
        onToggle={(v) => setConfig({ ...config, gateC: { ...config.gateC, enabled: v } })}
      >
        <SliderControl
          label="Wick/Body 比例阈值"
          value={config.gateC.wickRatioThreshold}
          min={0.3}
          max={1.5}
          step={0.1}
          onChange={(v) => setConfig({ ...config, gateC: { ...config.gateC, wickRatioThreshold: v } })}
        />
        <div style={sty.gateDesc}>
          当影线 / body &gt; {config.gateC.wickRatioThreshold} 时拒绝（犹豫不决）
        </div>
      </GateControl>

      <GateControl
        gate="D"
        title="趋势成熟度检测"
        problem="趋势末期翻转"
        color="#4e8aff"
        enabled={config.gateD.enabled}
        onToggle={(v) => setConfig({ ...config, gateD: { ...config.gateD, enabled: v } })}
      >
        <SliderControl
          label="趋势年龄阈值（K 线数）"
          value={config.gateD.trendAgeThreshold}
          min={20}
          max={100}
          step={5}
          onChange={(v) => setConfig({ ...config, gateD: { ...config.gateD, trendAgeThreshold: v } })}
        />
        <div style={sty.gateDesc}>
          前序趋势持续 &gt; {config.gateD.trendAgeThreshold} 根时拒绝（成熟趋势易反转）
        </div>
      </GateControl>

      <GateControl
        gate="E"
        title="Gap 检测"
        problem="跳空缺口"
        color="#00c9a7"
        enabled={config.gateE.enabled}
        onToggle={(v) => setConfig({ ...config, gateE: { ...config.gateE, enabled: v } })}
      >
        <SliderControl
          label="Gap/ATR 阈值"
          value={config.gateE.gapRatioThreshold}
          min={0.2}
          max={1.5}
          step={0.1}
          onChange={(v) => setConfig({ ...config, gateE: { ...config.gateE, gapRatioThreshold: v } })}
        />
        <div style={sty.gateDesc}>
          Gap / ATR &gt; {config.gateE.gapRatioThreshold} 时拒绝（跳空易回补）
        </div>
      </GateControl>
    </div>
  );
}

// ===== Confirm Section =====
function ConfirmSection() {
  return (
    <div style={sty.section}>
      <h2 style={sty.sectionTitle}>确认机制</h2>
      <p style={sty.sectionIntro}>
        通过 Gate 的 Flip 进入「候选」状态，需要后续 K 线确认有效性。
      </p>

      <Card title="确认评分规则">
        <ConfirmRuleTable />
      </Card>

      <Card title="Flip 生命周期追踪">
        <div style={sty.lifecycleDemo}>
          <LifecycleStage stage="t+0" label="Flip 发生" status="event" />
          <LifecycleArrow />
          <LifecycleStage stage="t+1" label="快速确认" status="pending" />
          <LifecycleArrow />
          <LifecycleStage stage="t+2" label="趋势延续" status="pending" />
          <LifecycleArrow />
          <LifecycleStage stage="t+3" label="稳定确认" status="pending" />
          <LifecycleArrow />
          <LifecycleStage stage="决策" label="Confirmed / Failed" status="result" />
        </div>
      </Card>

      <Card title="确认统计">
        <div style={sty.statsGrid}>
          <StatBox label="待确认" value="0" color="#f5a623" />
          <StatBox label="已确认" value="0" color="#00c9a7" />
          <StatBox label="已失败" value="0" color="#e05263" />
          <StatBox label="确认率" value="-%"  color="#4e8aff" />
        </div>
      </Card>
    </div>
  );
}

// ===== Stats Section =====
function StatsSection({ flips }) {
  if (flips.length === 0) {
    return (
      <div style={sty.section}>
        <h2 style={sty.sectionTitle}>统计分析</h2>
        <p style={sty.sectionIntro}>
          对 Flip 数据集进行统计分析，发现哪些特征影响成功率。
        </p>
        <Card title="数据准备">
          <div style={sty.placeholder}>
            <div style={{ fontSize: 48, marginBottom: 16 }}>📊</div>
            <div style={{ fontSize: 14, color: '#8b93a0' }}>
              请先在「Flip 数据集」页面提取数据
            </div>
          </div>
        </Card>
      </div>
    );
  }

  // 计算统计数据
  const stats = calculateFlipStats(flips);

  return (
    <div style={sty.section}>
      <h2 style={sty.sectionTitle}>统计分析</h2>
      <p style={sty.sectionIntro}>
        对 {flips.length} 条 Flip 数据进行统计分析，发现特征分布规律。
      </p>

      <Card title="数据集概览">
        <div style={sty.statsGrid}>
          <StatBox label="总样本" value={flips.length} color="#8b93a0" />
          <StatBox label="看涨 Flip" value={flips.filter(f => f.direction === 'long').length} color="#00c9a7" />
          <StatBox label="看跌 Flip" value={flips.filter(f => f.direction === 'short').length} color="#e05263" />
          <StatBox label="分析维度" value="5" color="#4e8aff" />
        </div>
      </Card>

      <Card title="Body/ATR 分布">
        <FeatureDistributionChart
          data={stats.bodyAtrDistribution}
          feature="Body/ATR"
          color="#e05263"
        />
        <div style={sty.statsSummary}>
          <span>平均: {stats.bodyAtrAvg.toFixed(2)}</span>
          <span>中位数: {stats.bodyAtrMedian.toFixed(2)}</span>
          <span>最大: {stats.bodyAtrMax.toFixed(2)}</span>
        </div>
      </Card>

      <Card title="Flip Density 分布">
        <FeatureDistributionChart
          data={stats.densityDistribution}
          feature="Flip Density"
          color="#f5a623"
        />
        <div style={sty.statsSummary}>
          <span>平均: {stats.densityAvg.toFixed(3)}</span>
          <span>中位数: {stats.densityMedian.toFixed(3)}</span>
          <span>最大: {stats.densityMax.toFixed(3)}</span>
        </div>
      </Card>

      <Card title="Wick Ratio 分布">
        <FeatureDistributionChart
          data={stats.wickRatioDistribution}
          feature="Wick Ratio"
          color="#a78bfa"
        />
        <div style={sty.statsSummary}>
          <span>平均: {stats.wickRatioAvg.toFixed(2)}</span>
          <span>中位数: {stats.wickRatioMedian.toFixed(2)}</span>
          <span>最大: {stats.wickRatioMax.toFixed(2)}</span>
        </div>
      </Card>

      <Card title="趋势年龄分布">
        <FeatureDistributionChart
          data={stats.trendAgeDistribution}
          feature="趋势年龄"
          color="#4e8aff"
        />
        <div style={sty.statsSummary}>
          <span>平均: {stats.trendAgeAvg.toFixed(0)} 根K线</span>
          <span>中位数: {stats.trendAgeMedian.toFixed(0)} 根</span>
          <span>最大: {stats.trendAgeMax} 根</span>
        </div>
      </Card>

      <Card title="Gap/ATR 分布">
        <FeatureDistributionChart
          data={stats.gapRatioDistribution}
          feature="Gap/ATR"
          color="#00c9a7"
        />
        <div style={sty.statsSummary}>
          <span>平均: {stats.gapRatioAvg.toFixed(2)}</span>
          <span>中位数: {stats.gapRatioMedian.toFixed(2)}</span>
          <span>最大: {stats.gapRatioMax.toFixed(2)}</span>
        </div>
      </Card>

      <Card title="特征相关性">
        <CorrelationMatrix flips={flips} />
      </Card>
    </div>
  );
}

// ===== Cross Symbol Section =====
function CrossSymbolSection({ flips }) {
  const symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'];

  // 按品种分组 Flip
  const flipsBySymbol = symbols.reduce((acc, sym) => {
    acc[sym] = flips.filter(f => f.symbol === sym);
    return acc;
  }, {});

  // 计算每个品种的统计数据
  const symbolStats = symbols.map(sym => {
    const symbolFlips = flipsBySymbol[sym];
    if (symbolFlips.length === 0) return null;

    const avg = (values) => values.reduce((a, b) => a + b, 0) / values.length;

    return {
      symbol: sym,
      count: symbolFlips.length,
      longCount: symbolFlips.filter(f => f.direction === 'long').length,
      shortCount: symbolFlips.filter(f => f.direction === 'short').length,
      avgBodyAtr: avg(symbolFlips.map(f => f.bodyAtrRatio)),
      avgDensity: avg(symbolFlips.map(f => f.flipDensity)),
      avgWickRatio: avg(symbolFlips.map(f => f.wickRatio)),
      avgTrendAge: avg(symbolFlips.map(f => f.trendAge)),
      avgGapRatio: avg(symbolFlips.map(f => f.gapRatio)),
    };
  }).filter(Boolean);

  const hasData = symbolStats.length > 0;

  return (
    <div style={sty.section}>
      <h2 style={sty.sectionTitle}>跨品种验证</h2>
      <p style={sty.sectionIntro}>
        对比 BTC、ETH、SOL 的 Flip 特征分布，验证 Gate 策略的通用性。
      </p>

      {!hasData && (
        <Card title="数据采集">
          <div style={sty.placeholder}>
            <div style={{ fontSize: 48, marginBottom: 16 }}>🔀</div>
            <div style={{ fontSize: 14, color: '#8b93a0', marginBottom: 12 }}>
              请先在不同品种下采集 Flip 数据
            </div>
            <div style={{ fontSize: 12, color: '#5a6270' }}>
              1. 切换到 BTC，采集数据<br />
              2. 切换到 ETH，采集数据<br />
              3. 切换到 SOL，采集数据
            </div>
          </div>
        </Card>
      )}

      {hasData && (
        <>
          <Card title="品种对比概览">
            <div style={sty.crossSymbolGrid}>
              {symbolStats.map(stat => (
                <div key={stat.symbol} style={sty.symbolCard}>
                  <div style={sty.symbolCardHeader}>
                    <div style={sty.symbolName}>{stat.symbol.replace('USDT', '')}</div>
                    <div style={sty.symbolCount}>{stat.count} Flips</div>
                  </div>
                  <div style={sty.symbolStats}>
                    <div style={sty.symbolStatRow}>
                      <span style={sty.symbolStatLabel}>看涨/看跌</span>
                      <span style={sty.symbolStatValue}>
                        {stat.longCount} / {stat.shortCount}
                      </span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </Card>

          <Card title="特征对比">
            <FeatureComparisonTable stats={symbolStats} />
          </Card>

          <Card title="特征差异分析">
            <div style={sty.analysisText}>
              <h4 style={sty.analysisTitle}>📊 观察到的差异</h4>
              {symbolStats.length >= 2 && (
                <ul style={sty.analysisList}>
                  <li>
                    <strong>Body/ATR</strong>: {symbolStats[0].symbol.replace('USDT', '')}
                    ({symbolStats[0].avgBodyAtr.toFixed(2)}) vs {symbolStats[1].symbol.replace('USDT', '')}
                    ({symbolStats[1].avgBodyAtr.toFixed(2)})
                    {Math.abs(symbolStats[0].avgBodyAtr - symbolStats[1].avgBodyAtr) > 0.3 &&
                      <span style={{color: '#f5a623'}}> ⚠️ 差异较大</span>
                    }
                  </li>
                  <li>
                    <strong>Flip Density</strong>: 不同品种的震荡程度差异
                  </li>
                  <li>
                    <strong>趋势年龄</strong>: 反映不同品种的趋势持续性特点
                  </li>
                </ul>
              )}
              <h4 style={sty.analysisTitle}>💡 策略建议</h4>
              <ul style={sty.analysisList}>
                <li>如果特征分布相似，可以使用统一的 Gate 阈值</li>
                <li>如果差异明显，建议为每个品种定制 Gate 参数</li>
                <li>波动率高的品种（如 SOL）可能需要更宽松的 Body/ATR 阈值</li>
              </ul>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

// ===== Dynamic SuperTrend Section =====
function DynamicSTSection() {
  const [config, setConfig] = useState({
    atrMultiplierMin: 2.5,
    atrMultiplierMax: 4.0,
    volatilityPeriod: 14,
    trendThreshold: 0.6,
  });

  return (
    <div style={sty.section}>
      <h2 style={sty.sectionTitle}>动态 SuperTrend</h2>
      <p style={sty.sectionIntro}>
        根据市场状态动态调整 ATR 倍数，在趋势行情中放宽，在震荡行情中收紧。
      </p>

      <Card title="策略原理">
        <div style={sty.principleBox}>
          <div style={sty.principleItem}>
            <div style={sty.principleIcon}>📈</div>
            <div>
              <div style={sty.principleTitle}>趋势市场</div>
              <div style={sty.principleDesc}>
                使用较大的 ATR 倍数（如 3.5-4.0），减少假 Flip
              </div>
            </div>
          </div>
          <div style={sty.principleItem}>
            <div style={sty.principleIcon}>📊</div>
            <div>
              <div style={sty.principleTitle}>震荡市场</div>
              <div style={sty.principleDesc}>
                使用较小的 ATR 倍数（如 2.5-3.0），快速跟踪波动
              </div>
            </div>
          </div>
          <div style={sty.principleItem}>
            <div style={sty.principleIcon}>⚡</div>
            <div>
              <div style={sty.principleTitle}>自适应逻辑</div>
              <div style={sty.principleDesc}>
                基于价格波动率、ADX 指标或 Flip Density 判断市场状态
              </div>
            </div>
          </div>
        </div>
      </Card>

      <Card title="参数配置">
        <div style={sty.formRow}>
          <label style={sty.label}>最小 ATR 倍数（震荡）</label>
          <input
            type="number"
            value={config.atrMultiplierMin}
            onChange={(e) => setConfig({...config, atrMultiplierMin: parseFloat(e.target.value)})}
            style={sty.input}
            step="0.1"
          />
        </div>
        <div style={sty.formRow}>
          <label style={sty.label}>最大 ATR 倍数（趋势）</label>
          <input
            type="number"
            value={config.atrMultiplierMax}
            onChange={(e) => setConfig({...config, atrMultiplierMax: parseFloat(e.target.value)})}
            style={sty.input}
            step="0.1"
          />
        </div>
        <div style={sty.formRow}>
          <label style={sty.label}>波动率观察周期</label>
          <input
            type="number"
            value={config.volatilityPeriod}
            onChange={(e) => setConfig({...config, volatilityPeriod: parseInt(e.target.value)})}
            style={sty.input}
          />
          <span style={sty.unit}>根K线</span>
        </div>
        <div style={sty.formRow}>
          <label style={sty.label}>趋势判断阈值</label>
          <input
            type="number"
            value={config.trendThreshold}
            onChange={(e) => setConfig({...config, trendThreshold: parseFloat(e.target.value)})}
            style={sty.input}
            step="0.05"
          />
        </div>
      </Card>

      <Card title="市场状态识别算法">
        <div style={sty.codeBlock}>
          <pre style={sty.code}>
{`// 方法 1: 基于 Flip Density
if (flipDensity > 0.15) {
  atrMultiplier = ${config.atrMultiplierMin};  // 震荡，收紧
} else {
  atrMultiplier = ${config.atrMultiplierMax};  // 趋势，放宽
}

// 方法 2: 基于波动率变化
volatility = ATR / Price;
if (volatility > recentAvgVolatility * 1.2) {
  atrMultiplier = ${config.atrMultiplierMax};  // 高波动，放宽
} else {
  atrMultiplier = ${config.atrMultiplierMin};
}

// 方法 3: 基于趋势强度（ADX 或方向一致性）
trendStrength = countSameTrendBars / ${config.volatilityPeriod};
atrMultiplier = lerp(
  ${config.atrMultiplierMin},
  ${config.atrMultiplierMax},
  trendStrength
);`}
          </pre>
        </div>
      </Card>

      <Card title="实现步骤">
        <div style={sty.stepsList}>
          <div style={sty.step}>
            <div style={sty.stepNumber}>1</div>
            <div style={sty.stepContent}>
              <div style={sty.stepTitle}>添加市场状态计算</div>
              <div style={sty.stepDesc}>
                在后端 SuperTrend 计算中增加市场状态判断逻辑
              </div>
            </div>
          </div>
          <div style={sty.step}>
            <div style={sty.stepNumber}>2</div>
            <div style={sty.stepContent}>
              <div style={sty.stepTitle}>动态调整 ATR 倍数</div>
              <div style={sty.stepDesc}>
                根据市场状态在 [{config.atrMultiplierMin}, {config.atrMultiplierMax}] 区间内插值
              </div>
            </div>
          </div>
          <div style={sty.step}>
            <div style={sty.stepNumber}>3</div>
            <div style={sty.stepContent}>
              <div style={sty.stepTitle}>回测验证</div>
              <div style={sty.stepDesc}>
                对比固定 ATR 倍数 vs 动态 ATR 倍数的表现
              </div>
            </div>
          </div>
        </div>
      </Card>
    </div>
  );
}

// ===== MTF (Multi-Timeframe) Section =====
function MTFSection() {
  return (
    <div style={sty.section}>
      <h2 style={sty.sectionTitle}>MTF SuperTrend</h2>
      <p style={sty.sectionIntro}>
        多周期联合信号，只在高低周期 SuperTrend 共振时开仓，提升信号质量。
      </p>

      <Card title="策略原理">
        <div style={sty.mtfDiagram}>
          <div style={sty.mtfLayer}>
            <div style={sty.mtfLayerTitle}>高周期（4h / 1d）</div>
            <div style={sty.mtfLayerDesc}>判断大方向趋势</div>
            <div style={sty.mtfLayerExample}>
              ✓ SuperTrend = Bullish → 只做多<br />
              ✓ SuperTrend = Bearish → 只做空
            </div>
          </div>
          <div style={sty.mtfArrow}>⬇️</div>
          <div style={sty.mtfLayer}>
            <div style={sty.mtfLayerTitle}>低周期（15m / 1h）</div>
            <div style={sty.mtfLayerDesc}>寻找入场时机</div>
            <div style={sty.mtfLayerExample}>
              当低周期 Flip 方向 == 高周期方向时开仓
            </div>
          </div>
        </div>
      </Card>

      <Card title="实现方案">
        <div style={sty.mtfStrategies}>
          <div style={sty.strategyCard}>
            <div style={sty.strategyTitle}>方案 A: 严格共振</div>
            <div style={sty.strategyDesc}>
              必须同时满足：<br />
              • 高周期 SuperTrend 方向一致<br />
              • 低周期发生 Flip<br />
              • Flip 通过所有 Gate
            </div>
            <div style={sty.strategyProsCons}>
              <div style={{color: '#00c9a7'}}>✓ 信号质量极高</div>
              <div style={{color: '#e05263'}}>✗ 信号数量少</div>
            </div>
          </div>

          <div style={sty.strategyCard}>
            <div style={sty.strategyTitle}>方案 B: 软共振</div>
            <div style={sty.strategyDesc}>
              高周期方向作为过滤条件：<br />
              • 高周期看多时，只接受低周期做多信号<br />
              • 高周期看空时，只接受低周期做空信号<br />
              • 不要求高周期同时 Flip
            </div>
            <div style={sty.strategyProsCons}>
              <div style={{color: '#00c9a7'}}>✓ 信号数量适中</div>
              <div style={{color: '#f5a623'}}>⚠ 需防止趋势末期入场</div>
            </div>
          </div>

          <div style={sty.strategyCard}>
            <div style={sty.strategyTitle}>方案 C: 加权共振</div>
            <div style={sty.strategyDesc}>
              为不同周期的信号赋予权重：<br />
              • 1d = 40%，4h = 30%，1h = 20%，15m = 10%<br />
              • 总分 &gt; 60% 时开仓
            </div>
            <div style={sty.strategyProsCons}>
              <div style={{color: '#00c9a7'}}>✓ 灵活性高</div>
              <div style={{color: '#8b93a0'}}>? 需要大量回测调参</div>
            </div>
          </div>
        </div>
      </Card>

      <Card title="周期组合建议">
        <table style={sty.table}>
          <thead>
            <tr>
              <th>交易风格</th>
              <th>高周期</th>
              <th>低周期</th>
              <th>适用场景</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>日内波段</td>
              <td>4h</td>
              <td>15m</td>
              <td>日内趋势跟踪</td>
            </tr>
            <tr>
              <td>短线</td>
              <td>1h</td>
              <td>5m</td>
              <td>快速进出</td>
            </tr>
            <tr>
              <td>中期持仓</td>
              <td>1d</td>
              <td>1h</td>
              <td>跟随大趋势</td>
            </tr>
          </tbody>
        </table>
      </Card>

      <Card title="实现步骤">
        <div style={sty.stepsList}>
          <div style={sty.step}>
            <div style={sty.stepNumber}>1</div>
            <div style={sty.stepContent}>
              <div style={sty.stepTitle}>多周期数据订阅</div>
              <div style={sty.stepDesc}>
                同时订阅高低周期的 K 线和 SuperTrend 数据
              </div>
            </div>
          </div>
          <div style={sty.step}>
            <div style={sty.stepNumber}>2</div>
            <div style={sty.stepContent}>
              <div style={sty.stepTitle}>共振逻辑实现</div>
              <div style={sty.stepDesc}>
                在低周期 Flip 时，检查高周期 SuperTrend 方向
              </div>
            </div>
          </div>
          <div style={sty.step}>
            <div style={sty.stepNumber}>3</div>
            <div style={sty.stepContent}>
              <div style={sty.stepTitle}>回测对比</div>
              <div style={sty.stepDesc}>
                对比单周期 vs MTF 的胜率、盈亏比、最大回撤
              </div>
            </div>
          </div>
        </div>
      </Card>
    </div>
  );
}

// ===== Backtest Section =====
function BacktestSection() {
  return (
    <div style={sty.section}>
      <h2 style={sty.sectionTitle}>回测验证</h2>
      <p style={sty.sectionIntro}>
        对比「原始 SuperTrend」vs「Gate 过滤」vs「Gate + 确认」的表现。
      </p>

      <Card title="回测配置">
        <div style={sty.formRow}>
          <label style={sty.label}>初始资金</label>
          <input type="number" defaultValue="10000" style={sty.input} />
        </div>
        <div style={sty.formRow}>
          <label style={sty.label}>每笔仓位</label>
          <input type="number" defaultValue="20" style={sty.input} />
          <span style={sty.unit}>%</span>
        </div>
        <div style={sty.formRow}>
          <label style={sty.label}>止损</label>
          <input type="number" defaultValue="1.5" style={sty.input} />
          <span style={sty.unit}>ATR</span>
        </div>
        <div style={sty.formRow}>
          <label style={sty.label}>止盈</label>
          <input type="number" defaultValue="3" style={sty.input} />
          <span style={sty.unit}>ATR</span>
        </div>
        <button style={sty.btnPrimary}>🚀 运行回测</button>
      </Card>

      <Card title="回测结果对比">
        <BacktestComparisonTable />
      </Card>
    </div>
  );
}

// ===== Reusable Components =====

function Card({ title, children }) {
  return (
    <div style={sty.card}>
      <h3 style={sty.cardTitle}>{title}</h3>
      <div style={sty.cardBody}>{children}</div>
    </div>
  );
}

function StatBox({ label, value, color }) {
  return (
    <div style={sty.statBox}>
      <div style={{ ...sty.statValue, color }}>{value}</div>
      <div style={sty.statLabel}>{label}</div>
    </div>
  );
}

function FeatureTree() {
  const features = [
    { cat: 'A. Flip 本身', items: ['body_atr_ratio', 'wick_ratio', 'gap_size', 'close_st_distance'] },
    { cat: 'B. Flip 前环境', items: ['prev_trend_duration', 'flip_density', 'atr_percentile'] },
    { cat: 'C. Flip 后确认', items: ['t1_distance', 't2_distance', 'mfe_t3', 'mae_t3'] },
    { cat: 'D. 最终结果', items: ['forward_return', 'is_profitable', 'next_flip_bars'] },
  ];

  return (
    <div style={sty.featureTree}>
      {features.map((cat, i) => (
        <div key={i} style={sty.featureCat}>
          <div style={sty.featureCatTitle}>{cat.cat}</div>
          <div style={sty.featureItems}>
            {cat.items.map((item, j) => (
              <span key={j} style={sty.featureItem}>{item}</span>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function FlipTableWithGates({ flips }) {
  if (flips.length === 0) {
    return <div style={sty.placeholder}>暂无数据</div>;
  }

  return (
    <div style={sty.tableWrap}>
      <table style={sty.table}>
        <thead>
          <tr>
            <th>时间</th>
            <th>方向</th>
            <th>价格</th>
            <th>Body/ATR</th>
            <th>Density</th>
            <th>Wick Ratio</th>
            <th>趋势年龄</th>
            <th>Gap/ATR</th>
            <th>Gate 状态</th>
          </tr>
        </thead>
        <tbody>
          {flips.slice(0, 20).map((flip, i) => (
            <tr key={i}>
              <td style={{ fontSize: 11 }}>{flip.time}</td>
              <td>
                <span style={{
                  ...sty.badge,
                  background: flip.direction === 'long' ? '#00c9a714' : '#e0526314',
                  color: flip.direction === 'long' ? '#00c9a7' : '#e05263',
                }}>
                  {flip.direction === 'long' ? '↑ 多' : '↓ 空'}
                </span>
              </td>
              <td>{flip.price?.toFixed(2)}</td>
              <td>{flip.bodyAtrRatio}</td>
              <td>{flip.flipDensity}</td>
              <td>{flip.wickRatio}</td>
              <td>{flip.trendAge}</td>
              <td>{flip.gapRatio}</td>
              <td>
                {flip.gateInfo?.passed ? (
                  <span style={{ ...sty.badge, background: '#00c9a714', color: '#00c9a7' }}>
                    ✓ 通过 ({flip.gateInfo.passedCount}/{flip.gateInfo.totalGates})
                  </span>
                ) : (
                  <div>
                    <span style={{ ...sty.badge, background: '#e0526314', color: '#e05263', marginBottom: 4 }}>
                      ✗ 拒绝 ({flip.gateInfo?.passedCount}/{flip.gateInfo?.totalGates})
                    </span>
                    {flip.gateInfo?.failedGates && flip.gateInfo.failedGates.length > 0 && (
                      <div style={{ fontSize: 10, color: '#8b93a0', marginTop: 4 }}>
                        {flip.gateInfo.failedGates.map((reason, idx) => (
                          <div key={idx} style={{ marginTop: 2 }}>• {reason}</div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {flips.length > 20 && (
        <div style={sty.hint}>仅显示前 20 条，共 {flips.length} 条数据</div>
      )}
    </div>
  );
}

function SignalListTable({ signals }) {
  return (
    <div style={sty.tableWrap}>
      <table style={sty.table}>
        <thead>
          <tr>
            <th>时间</th>
            <th>类型</th>
            <th>价格</th>
            <th>周期</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          {signals.length === 0 ? (
            <tr>
              <td colSpan="5" style={{ textAlign: 'center', color: '#5a6270', padding: '20px' }}>
                暂无信号数据
              </td>
            </tr>
          ) : (
            signals.map((sig, i) => (
              <tr key={i}>
                <td>{new Date(sig.ts).toLocaleString('zh-CN')}</td>
                <td>
                  <span style={{ color: sig.action === 'buy' ? '#00c9a7' : '#e05263' }}>
                    {sig.action === 'buy' ? '↑ 买入' : '↓ 卖出'}
                  </span>
                </td>
                <td>{sig.price.toFixed(2)}</td>
                <td><code>{sig.tf}</code></td>
                <td><span style={sty.badge}>原始信号</span></td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

function FlipTable({ flips }) {
  return (
    <div style={sty.tableWrap}>
      <table style={sty.table}>
        <thead>
          <tr>
            <th>时间</th>
            <th>方向</th>
            <th>Body/ATR</th>
            <th>Density</th>
            <th>Wick Ratio</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          {flips.map((flip, i) => (
            <tr key={i}>
              <td>{flip.time || '-'}</td>
              <td>
                <span style={{ color: flip.direction === 'long' ? '#00c9a7' : '#e05263' }}>
                  {flip.direction === 'long' ? '↑ 看涨' : '↓ 看跌'}
                </span>
              </td>
              <td>{flip.bodyAtrRatio?.toFixed(2) || '-'}</td>
              <td>{flip.flipDensity?.toFixed(3) || '-'}</td>
              <td>{flip.wickRatio?.toFixed(2) || '-'}</td>
              <td><span style={sty.badge}>待分析</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function GateControl({ gate, title, problem, color, enabled, onToggle, children }) {
  return (
    <div style={{ ...sty.gateControl, borderLeftColor: color, opacity: enabled ? 1 : 0.5 }}>
      <div style={sty.gateControlHeader}>
        <div style={sty.gateControlLeft}>
          <span style={{ ...sty.gateBadge, background: color }}>Gate {gate}</span>
          <span style={sty.gateControlTitle}>{title}</span>
        </div>
        <label style={sty.switch}>
          <input type="checkbox" checked={enabled} onChange={(e) => onToggle(e.target.checked)} />
          <span className="switchSlider"></span>
        </label>
      </div>
      <div style={sty.gateProblem}>针对：{problem}</div>
      {enabled && <div style={sty.gateControlBody}>{children}</div>}
    </div>
  );
}

function SliderControl({ label, value, min, max, step, onChange }) {
  return (
    <div style={sty.sliderControl}>
      <div style={sty.sliderLabel}>
        <span>{label}</span>
        <span style={sty.sliderValue}>{value}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        style={sty.slider}
      />
    </div>
  );
}

function ConfirmRuleTable() {
  const rules = [
    { stage: 't+1', condition: 'close 远离 ST > 0.3 ATR', score: '+1' },
    { stage: 't+1', condition: 'body 方向一致', score: '+1' },
    { stage: 't+2', condition: '价格继续远离 ST', score: '+1' },
    { stage: 't+2', condition: '累计 MFE > 1 ATR', score: '+1' },
    { stage: 't+3', condition: '仍在 ST 同侧', score: '+1' },
    { stage: 't+3', condition: '无二次 Flip 迹象', score: '+1' },
  ];

  return (
    <div style={sty.tableWrap}>
      <table style={sty.table}>
        <thead>
          <tr>
            <th>阶段</th>
            <th>确认条件</th>
            <th>得分</th>
          </tr>
        </thead>
        <tbody>
          {rules.map((rule, i) => (
            <tr key={i}>
              <td><code>{rule.stage}</code></td>
              <td>{rule.condition}</td>
              <td style={{ color: '#00c9a7', fontWeight: 600 }}>{rule.score}</td>
            </tr>
          ))}
          <tr style={{ borderTop: '2px solid var(--border)' }}>
            <td colSpan="2" style={{ fontWeight: 600 }}>决策规则</td>
            <td style={{ fontSize: 12, color: '#8b93a0' }}>≥6: Confirmed, 3-5: Pending, &lt;3: Failed</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function LifecycleStage({ stage, label, status }) {
  const colors = {
    event: '#8b93a0',
    pending: '#f5a623',
    result: '#00c9a7',
  };
  return (
    <div style={{ ...sty.lifecycleStage, borderColor: colors[status] }}>
      <div style={{ ...sty.lifecycleStageName, color: colors[status] }}>{stage}</div>
      <div style={sty.lifecycleStageLabel}>{label}</div>
    </div>
  );
}

function LifecycleArrow() {
  return <div style={sty.lifecycleArrow}>→</div>;
}

function FeatureComparisonTable({ stats }) {
  const features = [
    { key: 'avgBodyAtr', label: 'Body/ATR 平均值' },
    { key: 'avgDensity', label: 'Flip Density 平均值' },
    { key: 'avgWickRatio', label: 'Wick Ratio 平均值' },
    { key: 'avgTrendAge', label: '趋势年龄 平均值' },
    { key: 'avgGapRatio', label: 'Gap/ATR 平均值' },
  ];

  return (
    <div style={sty.tableWrap}>
      <table style={sty.table}>
        <thead>
          <tr>
            <th>特征</th>
            {stats.map(stat => (
              <th key={stat.symbol}>{stat.symbol.replace('USDT', '')}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {features.map((feature, i) => (
            <tr key={i}>
              <td style={{ fontWeight: 600 }}>{feature.label}</td>
              {stats.map(stat => (
                <td key={stat.symbol}>
                  {stat[feature.key]?.toFixed(2) || '-'}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BacktestComparisonTable() {
  const results = [
    { strategy: '原始 SuperTrend', trades: '-', winRate: '-', totalReturn: '-', maxDrawdown: '-' },
    { strategy: 'Gate 过滤', trades: '-', winRate: '-', totalReturn: '-', maxDrawdown: '-' },
    { strategy: 'Gate + 确认', trades: '-', winRate: '-', totalReturn: '-', maxDrawdown: '-' },
  ];

  return (
    <div style={sty.tableWrap}>
      <table style={sty.table}>
        <thead>
          <tr>
            <th>策略</th>
            <th>交易次数</th>
            <th>胜率</th>
            <th>总收益</th>
            <th>最大回撤</th>
          </tr>
        </thead>
        <tbody>
          {results.map((r, i) => (
            <tr key={i}>
              <td style={{ fontWeight: 600 }}>{r.strategy}</td>
              <td>{r.trades}</td>
              <td>{r.winRate}</td>
              <td>{r.totalReturn}</td>
              <td>{r.maxDrawdown}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={sty.hint}>
        💡 运行回测后将显示详细对比数据
      </div>
    </div>
  );
}

const sty = {
  root: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    overflow: 'hidden',
    background: 'var(--bg)',
  },
  header: {
    padding: '16px 24px',
    borderBottom: '1px solid var(--border)',
    background: 'var(--card)',
  },
  headerTop: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 16,
  },
  headerControls: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  tfSelector: {
    display: 'flex',
    gap: 4,
  },
  tfBtn: {
    padding: '4px 8px',
    background: 'transparent',
    border: '1px solid var(--border)',
    borderRadius: 4,
    color: '#8b93a0',
    fontSize: 11,
    fontWeight: 600,
    cursor: 'pointer',
    transition: 'all .15s',
  },
  tfBtnActive: {
    background: '#00c9a714',
    borderColor: '#00c9a7',
    color: '#00c9a7',
  },
  title: {
    fontSize: 24,
    fontWeight: 600,
    color: '#e8eaed',
    margin: 0,
  },
  subtitle: {
    fontSize: 13,
    color: '#8b93a0',
    margin: '8px 0 0',
    fontFamily: 'var(--font-mono)',
  },
  body: {
    flex: 1,
    display: 'flex',
    minHeight: 0,
  },
  sidebar: {
    width: 220,
    flexShrink: 0,
    background: 'var(--card)',
    borderRight: '1px solid var(--border)',
    padding: '16px 0',
    overflowY: 'auto',
  },
  sidebarTitle: {
    fontSize: 11,
    fontWeight: 700,
    color: '#5a6270',
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
    padding: '0 16px 12px',
  },
  navBtn: {
    width: '100%',
    background: 'transparent',
    border: 'none',
    padding: '10px 16px',
    textAlign: 'left',
    cursor: 'pointer',
    transition: 'all .15s',
    color: '#8b93a0',
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  navBtnActive: {
    background: '#00c9a714',
    color: '#00c9a7',
  },
  navIcon: {
    fontSize: 16,
    width: 20,
  },
  navLabel: {
    fontSize: 13,
    fontWeight: 500,
  },
  content: {
    flex: 1,
    padding: 32,
    overflowY: 'auto',
  },
  section: {
    maxWidth: 900,
    margin: '0 auto',
  },
  sectionTitle: {
    fontSize: 28,
    fontWeight: 600,
    color: '#e8eaed',
    margin: '0 0 12px',
  },
  sectionIntro: {
    fontSize: 14,
    color: '#8b93a0',
    lineHeight: 1.7,
    margin: '0 0 32px',
  },
  flowDiagram: {
    display: 'flex',
    alignItems: 'center',
    gap: 16,
    margin: '32px 0',
    padding: '24px',
    background: 'var(--card)',
    borderRadius: 12,
    border: '1px solid var(--border)',
  },
  flowStep: {
    flex: 1,
    padding: '16px',
    borderRadius: 8,
    border: '2px solid',
    background: '#ffffff03',
  },
  flowStepLabel: {
    fontSize: 13,
    fontWeight: 600,
    marginBottom: 4,
  },
  flowStepDesc: {
    fontSize: 11,
    color: '#8b93a0',
  },
  flowArrow: {
    fontSize: 20,
    color: '#5a6270',
  },
  card: {
    background: 'var(--card)',
    border: '1px solid var(--border)',
    borderRadius: 12,
    overflow: 'hidden',
    marginBottom: 24,
  },
  cardTitle: {
    fontSize: 16,
    fontWeight: 600,
    color: '#e8eaed',
    padding: '16px 20px',
    margin: 0,
    borderBottom: '1px solid var(--border)',
  },
  cardBody: {
    padding: 20,
  },
  problemSolution: {
    padding: '16px 0',
    borderBottom: '1px solid var(--border)',
  },
  problemRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    marginBottom: 8,
  },
  problemLabel: {
    padding: '3px 10px',
    borderRadius: 4,
    fontSize: 11,
    fontWeight: 700,
  },
  problemText: {
    fontSize: 13,
    color: '#e8eaed',
    fontWeight: 500,
  },
  solutionRow: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 10,
    paddingLeft: 12,
  },
  solutionIcon: {
    fontSize: 14,
  },
  solutionText: {
    fontSize: 13,
    color: '#c5c9cf',
    lineHeight: 1.6,
    flex: 1,
  },
  layer: {
    display: 'flex',
    alignItems: 'center',
    gap: 16,
    padding: '16px 0',
    borderBottom: '1px solid var(--border)',
  },
  layerNum: {
    width: 32,
    height: 32,
    borderRadius: '50%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    color: '#fff',
    fontSize: 14,
    fontWeight: 700,
  },
  layerContent: {
    flex: 1,
  },
  layerTitle: {
    fontSize: 14,
    fontWeight: 600,
    color: '#e8eaed',
    marginBottom: 4,
  },
  layerDesc: {
    fontSize: 13,
    color: '#8b93a0',
  },
  gateCard: {
    background: 'var(--card)',
    border: '1px solid var(--border)',
    borderLeft: '4px solid',
    borderRadius: 8,
    padding: 20,
    marginBottom: 20,
  },
  gateHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    marginBottom: 16,
  },
  gateBadge: {
    padding: '4px 10px',
    borderRadius: 4,
    fontSize: 11,
    fontWeight: 700,
    color: '#fff',
  },
  gateTitle: {
    fontSize: 16,
    fontWeight: 600,
    color: '#e8eaed',
  },
  gateProblem: {
    fontSize: 13,
    color: '#c5c9cf',
    marginBottom: 12,
  },
  gateLogic: {
    marginBottom: 12,
  },
  gateLabel: {
    fontSize: 12,
    fontWeight: 600,
    color: '#00c9a7',
    marginBottom: 8,
    display: 'block',
  },
  gateList: {
    margin: 0,
    padding: '0 0 0 20px',
    fontSize: 12,
    color: '#c5c9cf',
    lineHeight: 1.8,
    fontFamily: 'var(--font-mono)',
  },
  gateReason: {
    fontSize: 12,
    color: '#8b93a0',
    fontStyle: 'italic',
    paddingTop: 12,
    borderTop: '1px solid var(--border)',
  },
  confirmDim: {
    padding: '16px 0',
    borderBottom: '1px solid var(--border)',
  },
  confirmDimTitle: {
    fontSize: 14,
    fontWeight: 600,
    marginBottom: 12,
  },
  confirmList: {
    margin: 0,
    padding: '0 0 0 20px',
    fontSize: 13,
    color: '#c5c9cf',
    lineHeight: 1.8,
  },
  confirmItem: {},
  confirmType: {
    padding: '16px',
    background: '#ffffff03',
    borderRadius: 8,
    marginBottom: 12,
  },
  confirmTypeBadge: {
    display: 'inline-block',
    padding: '4px 12px',
    borderRadius: 4,
    fontSize: 11,
    fontWeight: 700,
    marginBottom: 8,
  },
  confirmTypeChar: {
    fontSize: 13,
    color: '#c5c9cf',
    marginBottom: 4,
  },
  confirmTypeSpeed: {
    fontSize: 13,
    color: '#c5c9cf',
  },
  codeBlock: {
    background: '#0a0c0f',
    borderRadius: 8,
    padding: 16,
    overflow: 'auto',
  },
  code: {
    margin: 0,
    fontSize: 12,
    color: '#00c9a7',
    fontFamily: 'var(--font-mono)',
    lineHeight: 1.6,
  },
  featureCat: {
    marginBottom: 32,
  },
  featureCatHeader: {
    marginBottom: 16,
  },
  featureCatTitle: {
    fontSize: 18,
    fontWeight: 600,
    marginBottom: 4,
  },
  featureCatDesc: {
    fontSize: 13,
    color: '#8b93a0',
  },
  featureTable: {
    width: '100%',
    borderCollapse: 'collapse',
    fontSize: 13,
  },
  featureTableHeader: {
    textAlign: 'left',
    padding: '10px 12px',
    borderBottom: '1px solid var(--border)',
    color: '#8b93a0',
    fontSize: 12,
    fontWeight: 600,
  },
  featureTableCell: {
    padding: '10px 12px',
    borderBottom: '1px solid var(--border)',
    color: '#c5c9cf',
  },
  phaseCard: {
    background: 'var(--card)',
    border: '1px solid var(--border)',
    borderLeft: '4px solid',
    borderRadius: 8,
    padding: 20,
    marginBottom: 24,
  },
  phaseCardHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    marginBottom: 16,
  },
  phaseNum: {
    padding: '4px 10px',
    borderRadius: 4,
    fontSize: 11,
    fontWeight: 700,
    color: '#fff',
  },
  phaseCardTitle: {
    fontSize: 16,
    fontWeight: 600,
    color: '#e8eaed',
    flex: 1,
  },
  phaseWeeks: {
    fontSize: 12,
    color: '#8b93a0',
    fontStyle: 'italic',
  },
  phaseTasksLabel: {
    fontSize: 12,
    fontWeight: 600,
    color: '#00c9a7',
    marginBottom: 8,
  },
  phaseTasks: {
    margin: 0,
    padding: '0 0 0 20px',
    fontSize: 13,
    color: '#c5c9cf',
    lineHeight: 1.8,
  },
  phaseTask: {},
  phaseOutput: {
    marginTop: 16,
    paddingTop: 16,
    borderTop: '1px solid var(--border)',
    fontSize: 13,
    color: '#c5c9cf',
  },
  phaseOutputLabel: {
    fontSize: 12,
    fontWeight: 600,
    color: '#4e8aff',
    marginRight: 8,
  },
  statsGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
    gap: 16,
  },
  statBox: {
    padding: 16,
    background: '#ffffff03',
    borderRadius: 8,
    textAlign: 'center',
  },
  statValue: {
    fontSize: 24,
    fontWeight: 700,
    marginBottom: 4,
  },
  statLabel: {
    fontSize: 12,
    color: '#8b93a0',
  },
  featureTree: {
    display: 'flex',
    flexDirection: 'column',
    gap: 16,
  },
  featureItems: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 8,
    marginTop: 8,
  },
  featureItem: {
    padding: '4px 10px',
    background: '#ffffff05',
    borderRadius: 4,
    fontSize: 11,
    color: '#8b93a0',
    fontFamily: 'var(--font-mono)',
  },
  actionRow: {
    display: 'flex',
    gap: 12,
    marginBottom: 16,
  },
  btnPrimary: {
    padding: '10px 20px',
    background: '#00c9a7',
    color: '#fff',
    border: 'none',
    borderRadius: 8,
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
    transition: 'all .15s',
  },
  btnSecondary: {
    padding: '10px 20px',
    background: 'transparent',
    color: '#8b93a0',
    border: '1px solid var(--border)',
    borderRadius: 8,
    fontSize: 13,
    fontWeight: 500,
    cursor: 'pointer',
    transition: 'all .15s',
  },
  hint: {
    fontSize: 12,
    color: '#8b93a0',
    padding: 12,
    background: '#ffffff03',
    borderRadius: 6,
    marginTop: 16,
  },
  tableWrap: {
    overflowX: 'auto',
  },
  table: {
    width: '100%',
    borderCollapse: 'collapse',
    fontSize: 13,
  },
  badge: {
    padding: '2px 8px',
    background: '#f5a62320',
    color: '#f5a623',
    borderRadius: 4,
    fontSize: 11,
    fontWeight: 600,
  },
  gateControl: {
    background: 'var(--card)',
    border: '1px solid var(--border)',
    borderLeft: '4px solid',
    borderRadius: 8,
    padding: 20,
    marginBottom: 16,
    transition: 'opacity .2s',
  },
  gateControlHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 12,
  },
  gateControlLeft: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
  },
  gateControlTitle: {
    fontSize: 15,
    fontWeight: 600,
    color: '#e8eaed',
  },
  gateControlBody: {
    marginTop: 16,
    paddingTop: 16,
    borderTop: '1px solid var(--border)',
  },
  gateDesc: {
    fontSize: 12,
    color: '#8b93a0',
    marginTop: 12,
    fontStyle: 'italic',
  },
  switch: {
    position: 'relative',
    display: 'inline-block',
    width: 44,
    height: 24,
    cursor: 'pointer',
  },
  switchSlider: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    background: '#3a3f47',
    borderRadius: 24,
    transition: '.2s',
  },
  sliderControl: {
    marginBottom: 16,
  },
  sliderLabel: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8,
    fontSize: 13,
    color: '#c5c9cf',
  },
  sliderValue: {
    fontWeight: 600,
    color: '#00c9a7',
    fontFamily: 'var(--font-mono)',
  },
  slider: {
    width: '100%',
    height: 6,
    borderRadius: 3,
    background: '#3a3f47',
    outline: 'none',
    cursor: 'pointer',
  },
  placeholder: {
    padding: '40px 20px',
    textAlign: 'center',
    fontSize: 14,
    color: '#5a6270',
    background: '#ffffff03',
    borderRadius: 8,
    border: '2px dashed var(--border)',
  },
  lifecycleDemo: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    padding: 20,
    background: '#ffffff03',
    borderRadius: 8,
    overflowX: 'auto',
  },
  lifecycleStage: {
    padding: '12px 16px',
    borderRadius: 6,
    border: '2px solid',
    background: '#ffffff05',
    minWidth: 120,
  },
  lifecycleStageName: {
    fontSize: 12,
    fontWeight: 700,
    marginBottom: 4,
  },
  lifecycleStageLabel: {
    fontSize: 11,
    color: '#8b93a0',
  },
  lifecycleArrow: {
    fontSize: 18,
    color: '#5a6270',
  },
  formRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    marginBottom: 16,
  },
  label: {
    fontSize: 13,
    color: '#c5c9cf',
    minWidth: 100,
  },
  input: {
    flex: 1,
    padding: '8px 12px',
    background: '#ffffff05',
    border: '1px solid var(--border)',
    borderRadius: 6,
    color: '#e8eaed',
    fontSize: 13,
  },
  unit: {
    fontSize: 12,
    color: '#8b93a0',
  },
  chartContainer: {
    width: '100%',
    padding: '20px 0',
  },
  histogram: {
    display: 'flex',
    alignItems: 'flex-end',
    justifyContent: 'space-between',
    height: 200,
    gap: 4,
    padding: '0 20px',
  },
  histogramBar: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 8,
  },
  histogramBarFill: {
    width: '100%',
    borderRadius: '4px 4px 0 0',
    transition: 'all .2s',
    cursor: 'pointer',
    minHeight: 2,
  },
  histogramBarLabel: {
    fontSize: 10,
    color: '#5a6270',
    transform: 'rotate(-45deg)',
    whiteSpace: 'nowrap',
  },
  chartXAxis: {
    textAlign: 'center',
    fontSize: 11,
    color: '#8b93a0',
    marginTop: 20,
    fontWeight: 600,
  },
  statsGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
    gap: 16,
  },
  statBox: {
    padding: '16px 20px',
    background: '#ffffff05',
    borderRadius: 8,
    border: '1px solid var(--border)',
  },
  statBoxLabel: {
    fontSize: 12,
    color: '#8b93a0',
    marginBottom: 8,
  },
  statBoxValue: {
    fontSize: 28,
    fontWeight: 700,
    fontFamily: 'var(--font-mono)',
  },
  statsSummary: {
    display: 'flex',
    gap: 24,
    marginTop: 16,
    padding: '12px 20px',
    background: '#ffffff03',
    borderRadius: 6,
    fontSize: 12,
    color: '#8b93a0',
  },
  correlationMatrix: {
    overflowX: 'auto',
  },
  correlationRow: {
    display: 'flex',
    gap: 2,
  },
  correlationCell: {
    width: 80,
    height: 40,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 12,
    fontWeight: 600,
    fontFamily: 'var(--font-mono)',
    border: '1px solid var(--border)',
  },
  correlationHeaderCell: {
    width: 80,
    height: 40,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 11,
    fontWeight: 600,
    color: '#8b93a0',
    background: '#ffffff05',
    border: '1px solid var(--border)',
  },
  chartHint: {
    fontSize: 12,
    color: '#8b93a0',
    marginTop: 12,
    padding: 12,
    background: '#ffffff03',
    borderRadius: 6,
  },
  filterCompare: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-around',
    padding: '24px 0',
    gap: 16,
  },
  filterBox: {
    flex: 1,
    textAlign: 'center',
    padding: 20,
    background: '#ffffff05',
    borderRadius: 8,
    border: '1px solid var(--border)',
  },
  filterLabel: {
    fontSize: 12,
    color: '#8b93a0',
    marginBottom: 8,
    fontWeight: 600,
  },
  filterValue: {
    fontSize: 32,
    fontWeight: 700,
    color: '#00c9a7',
    marginBottom: 8,
  },
  filterDesc: {
    fontSize: 11,
    color: '#5a6270',
  },
  filterArrow: {
    fontSize: 24,
    color: '#5a6270',
    fontWeight: 300,
  },
  // Cross Symbol Section styles
  crossSymbolGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
    gap: 16,
    marginBottom: 24,
  },
  symbolCard: {
    padding: 20,
    background: '#ffffff05',
    border: '1px solid var(--border)',
    borderRadius: 8,
  },
  symbolCardHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
  },
  symbolName: {
    fontSize: 18,
    fontWeight: 700,
    color: '#00c9a7',
  },
  symbolCount: {
    fontSize: 12,
    color: '#8b93a0',
  },
  symbolStats: {
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  symbolStatRow: {
    display: 'flex',
    justifyContent: 'space-between',
    fontSize: 12,
  },
  symbolStatLabel: {
    color: '#8b93a0',
  },
  symbolStatValue: {
    color: '#e8eaed',
    fontWeight: 600,
  },
  analysisText: {
    color: '#c5c9cf',
    lineHeight: 1.7,
  },
  analysisTitle: {
    fontSize: 14,
    fontWeight: 600,
    color: '#e8eaed',
    marginTop: 16,
    marginBottom: 8,
  },
  analysisList: {
    margin: '8px 0',
    paddingLeft: 20,
    fontSize: 13,
  },
  principleBox: {
    display: 'flex',
    flexDirection: 'column',
    gap: 16,
  },
  principleItem: {
    display: 'flex',
    gap: 16,
    padding: 16,
    background: '#ffffff05',
    borderRadius: 8,
    border: '1px solid var(--border)',
  },
  principleIcon: {
    fontSize: 32,
  },
  principleTitle: {
    fontSize: 14,
    fontWeight: 600,
    color: '#e8eaed',
    marginBottom: 4,
  },
  principleDesc: {
    fontSize: 12,
    color: '#8b93a0',
    lineHeight: 1.6,
  },
  stepsList: {
    display: 'flex',
    flexDirection: 'column',
    gap: 16,
  },
  step: {
    display: 'flex',
    gap: 16,
    padding: 16,
    background: '#ffffff05',
    borderRadius: 8,
    border: '1px solid var(--border)',
  },
  stepNumber: {
    width: 32,
    height: 32,
    borderRadius: '50%',
    background: '#00c9a7',
    color: '#fff',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 14,
    fontWeight: 700,
    flexShrink: 0,
  },
  stepContent: {
    flex: 1,
  },
  stepTitle: {
    fontSize: 14,
    fontWeight: 600,
    color: '#e8eaed',
    marginBottom: 4,
  },
  stepDesc: {
    fontSize: 12,
    color: '#8b93a0',
    lineHeight: 1.6,
  },
  mtfDiagram: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 16,
    padding: 24,
    background: '#ffffff03',
    borderRadius: 8,
  },
  mtfLayer: {
    width: '100%',
    maxWidth: 500,
    padding: 20,
    background: '#ffffff05',
    border: '2px solid var(--border)',
    borderRadius: 8,
    textAlign: 'center',
  },
  mtfLayerTitle: {
    fontSize: 16,
    fontWeight: 700,
    color: '#00c9a7',
    marginBottom: 8,
  },
  mtfLayerDesc: {
    fontSize: 13,
    color: '#8b93a0',
    marginBottom: 12,
  },
  mtfLayerExample: {
    fontSize: 12,
    color: '#c5c9cf',
    fontFamily: 'var(--font-mono)',
    lineHeight: 1.8,
    textAlign: 'left',
  },
  mtfArrow: {
    fontSize: 24,
    color: '#5a6270',
  },
  mtfStrategies: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
    gap: 16,
  },
  strategyCard: {
    padding: 20,
    background: '#ffffff05',
    border: '1px solid var(--border)',
    borderRadius: 8,
  },
  strategyTitle: {
    fontSize: 14,
    fontWeight: 700,
    color: '#e8eaed',
    marginBottom: 12,
  },
  strategyDesc: {
    fontSize: 12,
    color: '#8b93a0',
    lineHeight: 1.7,
    marginBottom: 12,
  },
  strategyProsCons: {
    fontSize: 11,
    lineHeight: 1.8,
  },
};
