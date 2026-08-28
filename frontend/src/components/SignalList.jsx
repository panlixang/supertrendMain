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

  const list = (onlyCur ? all.filter((s) => s.tf === tf && !s.hidden) : all.filter((s) => !s.hidden)).slice().reverse();

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
                {buy ? '▲ BUY' : '▼ SELL'}{s.will_trade === false ? ' ✕' : ''}
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

            {/* 显示未下单原因 */}
            {s.will_trade === false && s.gate_reasons && s.gate_reasons.length > 0 && (
              <div style={sty.reasonBox}>
                <span style={sty.reasonLabel}>❌ 未下单原因：</span>
                <span style={sty.reasonText}>
                  {s.gate_reasons.slice(0, 2).join(' · ')}
                </span>
              </div>
            )}

            {/* 显示打分详情 */}
            {s.score_detail && (
              <div style={{ marginTop: 6, padding: '6px 8px', background: '#ffffff08', borderRadius: 3, border: '1px solid #262626' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                  <span style={{ fontSize: 10, color: '#c8ccd4', fontWeight: 700 }}>
                    信号置信度：{s.score_detail.total}分
                  </span>
                  <span style={{ ...sty.badge, fontSize: 8.5, padding: '1px 5px',
                                background: s.score_detail.confidence === 'high' ? '#00c9a722' :
                                           s.score_detail.confidence === 'medium' ? '#f5a62322' :
                                           s.score_detail.confidence === 'low' ? '#4e8aff22' : '#5a627022',
                                color: s.score_detail.confidence === 'high' ? '#00c9a7' :
                                       s.score_detail.confidence === 'medium' ? '#f5a623' :
                                       s.score_detail.confidence === 'low' ? '#4e8aff' : '#8b93a0' }}>
                    {s.score_detail.confidence === 'high' ? '高置信' :
                     s.score_detail.confidence === 'medium' ? '中置信' :
                     s.score_detail.confidence === 'low' ? '低置信' : '噪音'}
                  </span>
                  {s.trade_half && (
                    <span style={{ ...sty.badge, fontSize: 8.5, padding: '1px 5px', background: '#f5a62322', color: '#f5a623' }}>
                      半仓
                    </span>
                  )}
                </div>
                {s.score_detail.breakdown && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                    {Object.entries(s.score_detail.breakdown).map(([key, val]) => (
                      <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        <span style={{ fontSize: 8.5, color: '#5a6270', minWidth: 75 }}>
                          {key === 'signal_quality' ? '信号强度' :
                           key === 'er_momentum' ? 'ER趋势性' :
                           key === 'volatility' ? '波动率' :
                           key === 'mtf_alignment' ? 'MTF共振' :
                           key === 'breakout_boost' ? '突破加成' :
                           key === 'penalties' ? '扣分项' : key}
                        </span>
                        <div style={{ flex: 1, height: 4, background: '#1e1e1e', borderRadius: 2, overflow: 'hidden' }}>
                          <div style={{ width: `${Math.abs(val) * 4}%`, height: '100%',
                                        background: val >= 0 ? '#00c9a7' : '#e05263' }} />
                        </div>
                        <span style={{ fontSize: 8.5, color: val >= 0 ? '#00c9a7' : '#e05263',
                                       fontFamily: 'var(--font-mono)', minWidth: 24, textAlign: 'right' }}>
                          {val > 0 ? '+' : ''}{val}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
                {s.score_detail.suggestion && (
                  <div style={{ fontSize: 8.5, color: '#8b93a0', marginTop: 4, lineHeight: 1.5 }}>
                    💡 {s.score_detail.suggestion}
                  </div>
                )}
              </div>
            )}

            {/* 显示增强功能的额外信息 */}
            {s.filters && Object.keys(s.filters).length > 0 && (
              <div style={sty.filtersBox}>
                {s.filters.momentum && (
                  <span style={sty.filterTag}>
                    🚀 {s.filters.momentum.reason}
                  </span>
                )}
                {s.filters.false_breakout && (
                  <span style={{ ...sty.filterTag, background: '#7a4a5522', color: '#e05263' }}>
                    ⚠️ 假突破
                  </span>
                )}
                {s.filters.adaptive && (
                  <span style={sty.filterTag}>
                    📊 波动率 {s.filters.adaptive.volatility}%
                  </span>
                )}
              </div>
            )}
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
  reasonBox: {
    marginTop: 4,
    padding: '6px 8px',
    background: '#7a4a5515',
    borderRadius: 3,
    borderLeft: '2px solid #e05263',
    fontSize: 9,
    lineHeight: 1.5,
  },
  reasonLabel: {
    color: '#e05263',
    fontWeight: 700,
    marginRight: 4,
  },
  reasonText: {
    color: '#8b93a0',
  },
  filtersBox: {
    marginTop: 4,
    display: 'flex',
    flexWrap: 'wrap',
    gap: 4,
  },
  filterTag: {
    fontSize: 8.5,
    padding: '2px 6px',
    background: '#00c9a722',
    color: '#00c9a7',
    borderRadius: 3,
    fontWeight: 600,
  },
};
