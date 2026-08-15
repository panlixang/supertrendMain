import { useState } from 'react';
import BacktestPanel from './components/BacktestPanel';
import BiasTable from './components/BiasTable';
import CandleChart from './components/CandleChart';
import ParamPanel from './components/ParamPanel';
import SignalList from './components/SignalList';
import SignalModal from './components/SignalModal';
import TickerBar from './components/TickerBar';
import Toasts from './components/Toasts';
import TradePanel from './components/TradePanel';
import { useWebSocket } from './hooks/useWebSocket';
import { useStore } from './stores/useStore';

const TABS = [
  { key: 'bias', label: 'Bias', color: '#00c9a7' },
  { key: 'signals', label: '买卖点', color: '#f5a623' },
  { key: 'trade', label: '挂单', color: '#e05263' },
  { key: 'backtest', label: '回测', color: '#4e8aff' },
  { key: 'params', label: '参数', color: '#a78bfa' },
];

export default function App() {
  useWebSocket();
  const [tab, setTab] = useState('bias');
  const signals = useStore((s) => s.signals);
  const tf = useStore((s) => s.tf);
  const onlyCur = useStore((s) => s.opts.onlyCurrentTf);
  const tradeCfg = useStore((s) => s.tradeConfig);
  const sigCount = (onlyCur ? signals.filter((s) => s.tf === tf) : signals).length;

  return (
    <div style={sty.root}>
      <TickerBar />

      <div style={sty.body}>
        <main style={sty.chart}>
          <CandleChart />
        </main>

        <aside style={sty.side}>
          <nav style={sty.tabs}>
            {TABS.map((t) => (
              <button key={t.key} onClick={() => setTab(t.key)} style={{
                ...sty.tab,
                ...(tab === t.key
                  ? { color: t.color, borderBottomColor: t.color, background: '#ffffff06' }
                  : {}),
              }}>                {t.label}
                {t.key === 'signals' && sigCount > 0 && (
                  <span style={sty.count}>{sigCount}</span>
                )}
                {t.key === 'trade' && tradeCfg?.enabled && (
                  <span style={{
                    width: 6, height: 6, borderRadius: '50%',
                    background: tradeCfg.paper ? '#00c9a7' : '#e05263',
                  }} title={tradeCfg.paper ? '自动挂单开启（模拟盘）' : '自动挂单开启（实盘）'} />
                )}
              </button>
            ))}
          </nav>

          <div style={{ flex: 1, overflowY: 'auto' }}>
            {tab === 'bias' && <BiasTable />}
            {tab === 'signals' && <SignalList />}
            {tab === 'trade' && <TradePanel />}
            {tab === 'backtest' && <BacktestPanel />}
            {tab === 'params' && <ParamPanel />}
          </div>
        </aside>
      </div>

      <Toasts />
      <SignalModal />

      <style>{`
        @keyframes slideIn { from { opacity:0; transform:translateX(18px) } to { opacity:1; transform:none } }
        @keyframes fadeIn { from { opacity:0 } to { opacity:1 } }
        @keyframes popIn { from { opacity:0; transform:scale(.94) } to { opacity:1; transform:none } }
        input::placeholder { color:#4a5058 }
        input:focus, select:focus { border-color:#00c9a7 !important; outline:none }
        button:hover:not(:disabled) { filter:brightness(1.15) }
        select option { background:#111 }
      `}</style>
    </div>
  );
}

const sty = {
  root: { display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' },
  body: { flex: 1, display: 'flex', minHeight: 0 },
  chart: {
    flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column',
    background: 'var(--surface)', padding: 6,
  },
  side: {
    width: 310, flexShrink: 0, background: 'var(--card)',
    borderLeft: '1px solid var(--border)', display: 'flex', flexDirection: 'column',
  },
  tabs: { display: 'flex', borderBottom: '1px solid var(--border)', flexShrink: 0 },
  tab: {
    flex: 1, background: 'transparent', border: 'none',
    // 用 longhand 三件套，避免与选中态的 borderBottomColor 混用触发 React 警告
    borderBottomWidth: 2, borderBottomStyle: 'solid', borderBottomColor: 'transparent',
    color: '#6c7480', cursor: 'pointer',
    fontSize: 11, fontWeight: 700, padding: '9px 4px', transition: 'all .15s',
    display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4,
  },
  count: {
    fontSize: 8.5, background: '#ffffff12', borderRadius: 8,
    padding: '0 4px', color: '#8b93a0', fontWeight: 700,
  },
};
