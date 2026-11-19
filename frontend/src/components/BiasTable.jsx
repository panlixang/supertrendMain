/**
 * MTF BIAS TABLE —— 对应 Pine 右上角那张表
 *   f_bias(tf) = ma(close, fastLen) >= ma(close, slowLen) ? 1 : -1
 * 行序与原脚本一致：5m / 15m / 30m / 1H / 4H / 1D / 1W / 1M。
 * 额外补了每个周期的 SuperTrend 方向 —— 原表只有 MA 偏向，
 * 但真正下单看的是 ST，两者一致时才是干净的顺势位置。
 */
import { useStore } from '../stores/useStore';
import { fmt } from '../utils/format';

const LABEL = {
  '5m': '5m', '15m': '15m', '30m': '30m', '1h': '1H',
  '4h': '4H', '1d': '1D', '1w': '1W', '1M': '1M',
};

export default function BiasTable() {
  const ov = useStore((s) => s.overview);
  const setTf = useStore((s) => s.setTf);
  const curTf = useStore((s) => s.tf);

  if (!ov?.bias) return <div style={sty.empty}>加载中…</div>;

  const { bias, st, advice } = ov;
  const stByTf = Object.fromEntries((st || []).map((r) => [r.tf, r]));

  return (
    <div style={sty.wrap}>
      <div style={{ ...sty.summary, borderColor: verdictColor(bias.verdict) + '66' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ color: verdictColor(bias.verdict), fontWeight: 800, fontSize: 13 }}>
            {bias.label}
          </span>
          <span style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'var(--font-mono)' }}>
            多 {bias.bulls} / 空 {bias.bears}
          </span>
        </div>
        {/* 加权偏向条：-1 全空 … +1 全多 */}
        <div style={sty.barTrack}>
          <div style={sty.barZero} />
          <div
            style={{
              ...sty.barFill,
              background: verdictColor(bias.verdict),
              width: `${Math.abs(bias.score) * 50}%`,
              left: bias.score >= 0 ? '50%' : `${50 - Math.abs(bias.score) * 50}%`,
            }}
          />
        </div>
        <div style={{ fontSize: 10, color: 'var(--muted)', lineHeight: 1.5 }}>
          加权偏向 <b style={{ color: verdictColor(bias.verdict) }}>{bias.score}</b>
          <span style={{ color: '#4a5058' }}>（高周期权重更大）</span>
        </div>
        <div style={sty.advice}>{advice}</div>
      </div>

      <div style={sty.tableHead}>
        <span style={{ width: 38 }}>TF</span>
        <span style={{ flex: 1 }}>
          BIAS <span style={{ color: '#4a5058', fontWeight: 400 }}>
            {bias.ma_type}{bias.fast_len}/{bias.slow_len}
          </span>
        </span>
        <span style={{ width: 74, textAlign: 'right' }}>SUPERTREND</span>
      </div>

      {bias.rows.map((r) => {
        const s = stByTf[r.tf] || {};
        const agree = r.bias != null && s.trend != null && r.bias === s.trend;
        return (
          <button
            key={r.tf}
            onClick={() => setTf(r.tf)}
            style={{
              ...sty.row,
              background: curTf === r.tf ? '#ffffff0a' : 'transparent',
              borderLeftColor: curTf === r.tf ? '#00c9a7' : 'transparent',
            }}
            title={agree ? 'MA 偏向与超趋方向一致' : 'MA 偏向与超趋方向背离，观望'}
          >
            <span style={{ width: 38, color: '#c8ccd4', fontWeight: 700, fontSize: 11 }}>
              {LABEL[r.tf] || r.tf}
            </span>

            <span style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ ...sty.tag, ...tagStyle(r.bias) }}>
                {r.bias === 1 ? '▲ Bull' : r.bias === -1 ? '▼ Bear' : '—'}
              </span>
              {r.spread != null && (
                <span style={{ fontSize: 9.5, color: '#4a5058', fontFamily: 'var(--font-mono)' }}>
                  {r.spread > 0 ? '+' : ''}{r.spread}%
                </span>
              )}
            </span>

            <span style={{ width: 74, textAlign: 'right' }}>
              {s.trend == null ? (
                <span style={{ color: '#4a5058', fontSize: 10 }}>—</span>
              ) : (
                <span style={{ display: 'inline-flex', flexDirection: 'column', alignItems: 'flex-end' }}>
                  <span style={{
                    color: s.seeded ? '#4a5058'
                         : s.trend === 1 ? '#00c9a7' : '#e05263',
                    fontWeight: 700, fontSize: 10.5,
                  }}>
                    {s.trend === 1 ? '↑ 多' : '↓ 空'}
                    {s.seeded && <span style={{ color: '#5a6270' }}>?</span>}
                    {agree && !s.seeded && <span style={{ color: '#f5a623', marginLeft: 3 }}>★</span>}
                  </span>
                  <span style={{ fontSize: 9, color: '#4a5058', fontFamily: 'var(--font-mono)' }}>
                    {s.seeded ? '未翻转' : fmt.price(s.line)}
                  </span>
                </span>
              )}
            </span>
          </button>
        );
      })}

      <div style={sty.foot}>
        ★ = MA 偏向与超趋同向　·　点击行切换主图周期
        <div style={{ marginTop: 3 }}>
          ? = 该周期 K 线内超趋从未翻转，方向仅为初始值，不作数
        </div>
      </div>
    </div>
  );
}

const verdictColor = (v) =>
  v === 'bull' ? '#00c9a7' : v === 'bear' ? '#e05263' : '#f5a623';

const tagStyle = (b) =>
  b === 1
    ? { background: '#00c9a733', color: '#00c9a7', borderColor: '#00c9a755' }
    : b === -1
      ? { background: '#8b000055', color: '#e05263', borderColor: '#c2185b55' }
      : { background: '#ffffff08', color: '#4a5058', borderColor: '#262626' };

const sty = {
  wrap: { padding: '8px 10px 14px', display: 'flex', flexDirection: 'column', gap: 2 },
  empty: { padding: 20, color: 'var(--muted)', fontSize: 11, textAlign: 'center' },
  summary: {
    borderWidth: 1, borderStyle: 'solid', borderColor: 'transparent', borderRadius: 6, padding: '8px 10px',
    display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 8,
    background: '#ffffff05',
  },
  barTrack: {
    position: 'relative', height: 4, background: '#1c1c1c',
    borderRadius: 2, overflow: 'hidden',
  },
  barZero: {
    position: 'absolute', left: '50%', top: 0, bottom: 0,
    width: 1, background: '#3a3a3a',
  },
  barFill: { position: 'absolute', top: 0, bottom: 0, borderRadius: 2, transition: 'all .3s' },
  advice: {
    fontSize: 10, color: '#8b93a0', lineHeight: 1.6,
    borderTop: '1px solid #1e1e1e', paddingTop: 6,
  },
  tableHead: {
    display: 'flex', alignItems: 'center', gap: 6, padding: '5px 6px',
    fontSize: 9.5, fontWeight: 700, color: '#00c9a7', letterSpacing: 0.5,
    borderBottom: '1px solid #262626',
  },
  row: {
    display: 'flex', alignItems: 'center', gap: 6, width: '100%',
    padding: '6px', border: 'none',
    borderLeftWidth: 2, borderLeftStyle: 'solid', borderLeftColor: 'transparent',
    cursor: 'pointer', color: 'inherit', textAlign: 'left',
  },
  tag: {
    fontSize: 10, fontWeight: 700, padding: '2px 6px',
    borderRadius: 3, borderWidth: 1, borderStyle: 'solid', borderColor: 'transparent',
  },
  foot: { fontSize: 9, color: '#3f4650', marginTop: 8, lineHeight: 1.6 },
};
