/**
 * 买卖点列表 —— buySignal / sellSignal 的历史流水
 * 等级 A/B/C 见 backend/strategy.py：A 顺 Bias 且突破干脆，C 逆 Bias 只提示。
 */
import { useStore } from '../stores/useStore';
import { fmt, gradeColor } from '../utils/format';

export default function SignalList() {
  const all = useStore((s) => s.signals);
  const tf = useStore((s) => s.tf);
  const onlyCur = useStore((s) => s.opts.onlyCurrentTf);
  const toggle = useStore((s) => s.toggleOpt);
  const focus = useStore((s) => s.focusSignal);
  const focusTs = useStore((s) => s.focusTs);

  const list = (onlyCur ? all.filter((s) => s.tf === tf) : all).slice().reverse();

  return (
    <div style={sty.wrap}>
      <div style={sty.bar}>
        <span style={{ fontSize: 10, color: 'var(--muted)' }}>
          共 <b style={{ color: '#e9ecef' }}>{list.length}</b> 个信号
        </span>
        <button
          onClick={() => toggle('onlyCurrentTf')}
          style={{ ...sty.btn, color: onlyCur ? '#00c9a7' : 'var(--muted)',
                   borderColor: onlyCur ? '#00c9a755' : '#262626' }}
        >
          {onlyCur ? `仅 ${tf}` : '全部周期'}
        </button>
      </div>

      {!list.length && (
        <div style={sty.empty}>
          暂无信号
          <div style={{ fontSize: 9.5, marginTop: 6, color: '#3f4650', lineHeight: 1.7 }}>
            ATR 倍数越大信号越少。默认 9.1 在小周期上可能几百根 K 线才翻一次，
            想要更密的信号可到「参数」页调小倍数。
          </div>
        </div>
      )}

      {list.map((s) => {
        const buy = s.type === 'buy';
        const col = buy ? '#00c9a7' : '#c2185b';
        const on = focusTs === s.ts && tf === s.tf;
        return (
          <div key={`${s.tf}-${s.ts}-${s.type}`}
               onClick={() => focus(s.tf, s.ts)}
               title="点击定位到主图"
               style={{
                 ...sty.item, borderLeftColor: col, cursor: 'pointer',
                 background: on ? '#ffffff0e' : '#ffffff05',
               }}>
            <div style={sty.head}>
              <span style={{ ...sty.badge, background: col + '22', color: buy ? '#00c9a7' : '#e05263', borderColor: col + '66' }}>
                {buy ? '▲ BUY' : '▼ SELL'}
              </span>
              <span style={{ ...sty.grade, background: gradeColor(s.grade) + '22',
                             color: gradeColor(s.grade), borderColor: gradeColor(s.grade) + '66' }}>
                {s.grade || '—'}
              </span>
              <span style={sty.tf}>{s.tf}</span>
              <span style={{ flex: 1 }} />
              <span style={{ fontSize: 9.5, color: '#4a5058' }}>{fmt.ago(s.ts)}</span>
            </div>

            <div style={sty.grid}>
              <Cell k="触发价" v={fmt.price(s.price)} />
              <Cell k="超趋线" v={fmt.price(s.line)} sub="初始止损" />
              <Cell
                k="至今"
                v={`${s.pnl_pct > 0 ? '+' : ''}${s.pnl_pct}%`}
                color={s.pnl_pct >= 0 ? '#00c9a7' : '#e05263'}
                sub={`${s.bars_since} 根`}
              />
            </div>

            <div style={sty.footRow}>
              <Dots n={s.score ?? 0} />
              <span style={{ fontSize: 9, color: '#4a5058' }}>
                强度 {s.score ?? 0}/3 · {fmt.datetime(s.ts)}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Cell({ k, v, sub, color }) {
  return (
    <div>
      <div style={{ fontSize: 9, color: '#4a5058' }}>{k}</div>
      <div style={{ fontSize: 11.5, fontFamily: 'var(--font-mono)', color: color || '#e9ecef' }}>
        {v}
      </div>
      {sub && <div style={{ fontSize: 8.5, color: '#3f4650' }}>{sub}</div>}
    </div>
  );
}

function Dots({ n }) {
  return (
    <span style={{ display: 'flex', gap: 3 }}>
      {[0, 1, 2].map((i) => (
        <span key={i} style={{
          width: 5, height: 5, borderRadius: '50%',
          background: i < n ? '#f5a623' : '#262626',
        }} />
      ))}
    </span>
  );
}

const sty = {
  wrap: { padding: '6px 10px 14px', display: 'flex', flexDirection: 'column', gap: 6 },
  bar: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    padding: '2px 0 6px', borderBottom: '1px solid #1e1e1e',
  },
  btn: {
    background: 'transparent', borderWidth: 1, borderStyle: 'solid', borderColor: 'transparent', borderRadius: 3,
    cursor: 'pointer', fontSize: 9.5, fontWeight: 700, padding: '2px 7px',
  },
  empty: { padding: '24px 8px', textAlign: 'center', color: 'var(--muted)', fontSize: 11 },
  item: {
    background: '#ffffff05', borderLeftWidth: 2, borderLeftStyle: 'solid', borderLeftColor: 'transparent', borderRadius: 4,
    padding: '7px 9px', display: 'flex', flexDirection: 'column', gap: 6,
  },
  head: { display: 'flex', alignItems: 'center', gap: 5 },
  badge: {
    fontSize: 10, fontWeight: 800, padding: '1px 6px',
    borderRadius: 3, borderWidth: 1, borderStyle: 'solid', borderColor: 'transparent',
  },
  grade: {
    fontSize: 9, fontWeight: 800, padding: '1px 5px',
    borderRadius: 3, borderWidth: 1, borderStyle: 'solid', borderColor: 'transparent',
  },
  tf: {
    fontSize: 9.5, color: '#8b93a0', fontFamily: 'var(--font-mono)',
    background: '#ffffff08', padding: '1px 5px', borderRadius: 3,
  },
  grid: { display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6 },
  footRow: {
    display: 'flex', alignItems: 'center', gap: 8,
    borderTop: '1px solid #1a1a1a', paddingTop: 5,
  },
};
