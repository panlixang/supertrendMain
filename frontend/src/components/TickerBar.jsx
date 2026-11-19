import { ALL_TFS, useStore } from '../stores/useStore';
import { fmt } from '../utils/format';
import SymbolSelector from './SymbolSelector';

export default function TickerBar() {
  const ticker = useStore((s) => s.ticker);
  const connected = useStore((s) => s.connected);
  const tf = useStore((s) => s.tf);
  const setTf = useStore((s) => s.setTf);
  const ind = useStore((s) => s.indicators[tf]);
  const ov = useStore((s) => s.overview);

  const pct = ticker?.open24h ? ((ticker.last - ticker.open24h) / ticker.open24h) * 100 : 0;
  const upDown = pct >= 0 ? '#00c9a7' : '#e05263';
  const st = ind?.state;
  const bias = ov?.bias;

  return (
    <header style={sty.bar}>
      <div style={sty.left}>
        <SymbolSelector />
        <span style={{
          width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
          background: connected ? '#00c9a7' : '#e05263',
        }} title={connected ? '已连接' : '连接断开'} />
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 23, fontWeight: 700, color: upDown }}>
          {fmt.price(ticker?.last)}
        </span>
        <span style={{ fontSize: 12, color: upDown }}>{fmt.pct(pct)}</span>
      </div>

      <div style={sty.stats}>
        {[
          ['24H高', fmt.price(ticker?.high24h)],
          ['24H低', fmt.price(ticker?.low24h)],
          ['24H量', fmt.vol(ticker?.vol24h)],
          ['更新', fmt.time(ticker?.ts)],
        ].map(([k, v]) => (
          <div key={k} style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 9, color: '#4a5058' }}>{k}</div>
            <div style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: '#c8ccd4' }}>{v}</div>
          </div>
        ))}
      </div>

      <div style={sty.badges}>
        {st?.trend != null && (
          <div style={{
            ...sty.badge,
            borderColor: st.seeded ? '#2c2c2c'
                       : st.trend === 1 ? '#00c9a766' : '#c2185b66',
            background: st.seeded ? 'transparent'
                      : st.trend === 1 ? '#00c9a715' : '#8b000030',
          }}>
            <span style={{ fontSize: 8.5, color: '#5a6270' }}>超趋 {tf}</span>
            <span style={{
              fontSize: 12, fontWeight: 800,
              color: st.seeded ? '#6c7480' : st.trend === 1 ? '#00c9a7' : '#e05263',
            }}>
              {st.trend === 1 ? '▲ 多头' : '▼ 空头'}
            </span>
            <span style={{ fontSize: 9, color: '#5a6270', fontFamily: 'var(--font-mono)' }}>
              {st.seeded ? '未翻转，仅初始值' : `线 ${fmt.price(st.line)}`}
            </span>
          </div>
        )}
        {bias && (
          <div style={{ ...sty.badge, borderColor: '#2c2c2c' }}>
            <span style={{ fontSize: 8.5, color: '#5a6270' }}>MTF Bias</span>
            <span style={{
              fontSize: 12, fontWeight: 800,
              color: bias.verdict === 'bull' ? '#00c9a7'
                   : bias.verdict === 'bear' ? '#e05263' : '#f5a623',
            }}>
              {bias.label}
            </span>
            <span style={{ fontSize: 9, color: '#5a6270', fontFamily: 'var(--font-mono)' }}>
              {bias.bulls}多 / {bias.bears}空
            </span>
          </div>
        )}
      </div>

      <div style={sty.tfs}>
        {ALL_TFS.map((t) => (
          <button key={t} onClick={() => setTf(t)}
                  style={{ ...sty.tfBtn, ...(tf === t ? sty.tfOn : {}) }}>
            {t}
          </button>
        ))}
      </div>
    </header>
  );
}

const sty = {
  bar: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    gap: 12, flexWrap: 'wrap', padding: '7px 14px',
    background: 'var(--surface)', borderBottom: '1px solid var(--border)',
  },
  left: { display: 'flex', alignItems: 'center', gap: 9 },
  stats: { display: 'flex', gap: 16 },
  badges: { display: 'flex', gap: 6, flexWrap: 'wrap' },
  badge: {
    display: 'flex', flexDirection: 'column', alignItems: 'flex-start',
    borderWidth: 1, borderStyle: 'solid', borderColor: 'transparent', borderRadius: 5, padding: '3px 9px', lineHeight: 1.25,
  },
  tfs: { display: 'flex', gap: 3, flexWrap: 'wrap' },
  tfBtn: {
    background: '#141414', borderWidth: 1, borderStyle: 'solid', borderColor: '#262626', borderRadius: 4,
    color: '#6c7480', cursor: 'pointer', fontSize: 10.5, fontWeight: 600,
    padding: '4px 7px', transition: 'all .15s',
  },
  tfOn: { background: '#00c9a7', borderColor: '#00c9a7', color: '#04140f', fontWeight: 800 },
};
