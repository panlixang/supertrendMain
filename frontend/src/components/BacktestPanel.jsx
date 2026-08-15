/**
 * 回测 + 参数寻优
 *
 * 默认规则等价于 Pine 的 strategy.entry('BUY'/'SELL')：翻转即反手、永远满仓。
 * 在此之上可以逐项打开实盘才有的东西，把回测对齐到实盘真正在跑的逻辑：
 *   只做多 / Bias 过滤   脚本本身没有
 *   ER 闸门              同 regime.evaluate，震荡市只提醒不开仓
 *   实盘止盈止损         同 position.py：1.5% 平 70% → 止损抬保本 → 跟随超趋线
 *   固定保证金 × 杠杆     同 executor：每笔名义 = 保证金 × 杠杆，而非满仓复利
 *
 * 全关时结果与改造前完全一致（后端有 48 组回归用例保证）。
 */
import { useState } from 'react';
import { ALL_TFS, useStore } from '../stores/useStore';
import { API, fmt } from '../utils/format';

const ER_GRID = [0.05, 0.1, 0.12, 0.15, 0.2, 0.25, 0.3];

export default function BacktestPanel() {
  const curTf = useStore((s) => s.tf);
  const params = useStore((s) => s.params);

  const [cfg, setCfg] = useState({
    tf: curTf, bars: 800, init_cash: 10000, fee_rate: 0.0005,
    allow_short: true, bias_filter: false,
    er_min: '', use_exit_rules: false,
    sizing: 'equity', margin_usdt: 10, leverage: 10,
    er_weak_min: '', quick_enabled: false,
  });
  const [res, setRes] = useState(null);
  const [sweep, setSweep] = useState(null);
  const [erSweep, setErSweep] = useState(null);
  const [busy, setBusy] = useState('');

  const set = (k, v) => setCfg((c) => ({ ...c, [k]: v }));

  // 空字符串 = 不启用闸门，后端收到 null 就走原路径
  const erVal = () => (cfg.er_min === '' || cfg.er_min === null ? null : +cfg.er_min);
  const weakVal = () => (cfg.er_weak_min === '' || cfg.er_weak_min === null ? null : +cfg.er_weak_min);
  const engineCfg = () => ({
    er_min: erVal(),
    use_exit_rules: cfg.use_exit_rules,
    sizing: cfg.sizing,
    margin_usdt: +cfg.margin_usdt,
    leverage: +cfg.leverage,
    er_weak_min: weakVal(),
    quick_enabled: cfg.quick_enabled && weakVal() != null,
  });

  async function run() {
    setBusy('bt'); setRes(null);
    try {
      const r = await fetch(`${API}/api/backtest`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...cfg, ...engineCfg() }),
      });
      setRes(await r.json());
    } catch {
      setRes({ error: '网络错误' });
    } finally { setBusy(''); }
  }

  async function runSweep() {
    setBusy('sw'); setSweep(null);
    try {
      const r = await fetch(`${API}/api/sweep`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tf: cfg.tf, bars: cfg.bars, fee_rate: cfg.fee_rate,
          allow_short: cfg.allow_short, bias_filter: cfg.bias_filter,
          period_min: 7, period_max: 21, period_step: 2,
          mult_min: 1, mult_max: 10, mult_step: 1,
          ...engineCfg(),
        }),
      });
      setSweep(await r.json());
    } catch {
      setSweep({ error: '网络错误' });
    } finally { setBusy(''); }
  }

  async function runErSweep() {
    setBusy('er'); setErSweep(null);
    try {
      const r = await fetch(`${API}/api/sweep-er`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tf: cfg.tf, bars: cfg.bars, fee_rate: cfg.fee_rate,
          allow_short: cfg.allow_short, bias_filter: cfg.bias_filter,
          er_list: ER_GRID,
          use_exit_rules: cfg.use_exit_rules, sizing: cfg.sizing,
          margin_usdt: +cfg.margin_usdt, leverage: +cfg.leverage,
        }),
      });
      setErSweep(await r.json());
    } catch {
      setErSweep({ error: '网络错误' });
    } finally { setBusy(''); }
  }

  async function applyParams(p) {
    await fetch(`${API}/api/params`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ periods: p.periods, multiplier: p.multiplier }),
    });
  }

  return (
    <div style={sty.wrap}>
      <div style={sty.grid2}>
        <Field label="周期">
          <select value={cfg.tf} onChange={(e) => set('tf', e.target.value)} style={sty.input}>
            {ALL_TFS.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </Field>
        <Field label="K线根数">
          <input type="number" min={50} max={10000} step={100} value={cfg.bars}
                 onChange={(e) => set('bars', +e.target.value)} style={sty.input} />
        </Field>
        <Field label="初始资金">
          <input type="number" value={cfg.init_cash}
                 onChange={(e) => set('init_cash', +e.target.value)} style={sty.input} />
        </Field>
        <Field label="手续费率">
          <input type="number" step={0.0001} value={cfg.fee_rate}
                 onChange={(e) => set('fee_rate', +e.target.value)} style={sty.input} />
        </Field>
      </div>

      <div style={{ display: 'flex', gap: 5 }}>
        <Check on={cfg.allow_short} onClick={() => set('allow_short', !cfg.allow_short)}
               label="允许做空" />
        <Check on={cfg.bias_filter} onClick={() => set('bias_filter', !cfg.bias_filter)}
               label="MTF Bias 过滤" />
      </div>

      <div style={sty.sect}>对齐实盘</div>

      <div style={sty.grid2}>
        <Field label="ER 闸门（留空=不启用）">
          <input type="number" step={0.01} min={0} max={1} placeholder="如 0.15"
                 value={cfg.er_min}
                 onChange={(e) => set('er_min', e.target.value)} style={sty.input} />
        </Field>
        <Field label="仓位规模">
          <select value={cfg.sizing} onChange={(e) => set('sizing', e.target.value)}
                  style={sty.input}>
            <option value="equity">满仓复利 1x</option>
            <option value="fixed">固定保证金 × 杠杆</option>
          </select>
        </Field>
      </div>

      <div style={sty.grid2}>
        <Field label="弱档下界（留空=不启用弱档）">
          <input type="number" step={0.01} min={0} max={1} placeholder="如 0.12"
                 value={cfg.er_weak_min}
                 onChange={(e) => set('er_weak_min', e.target.value)} style={sty.input} />
        </Field>
        <div style={{ display: 'flex', alignItems: 'flex-end' }}>
          <Check on={cfg.quick_enabled} onClick={() => set('quick_enabled', !cfg.quick_enabled)}
                 label="弱档下单（快进快出）" />
        </div>
      </div>

      {cfg.sizing === 'fixed' && (
        <div style={sty.grid2}>
          <Field label="每笔保证金 U">
            <input type="number" min={0.1} step={1} value={cfg.margin_usdt}
                   onChange={(e) => set('margin_usdt', +e.target.value)} style={sty.input} />
          </Field>
          <Field label="杠杆倍数">
            <input type="number" min={1} max={125} value={cfg.leverage}
                   onChange={(e) => set('leverage', +e.target.value)} style={sty.input} />
          </Field>
        </div>
      )}

      <Check on={cfg.use_exit_rules} onClick={() => set('use_exit_rules', !cfg.use_exit_rules)}
             label="按实盘止盈止损（用「挂单」页那套规则）" />

      <div style={{ fontSize: 9.5, color: '#4a5058' }}>
        当前参数 <b style={{ color: '#8b93a0', fontFamily: 'var(--font-mono)' }}>
          {params.periods} × {params.multiplier}
        </b>（在「参数」页修改）
      </div>

      <div style={{ display: 'flex', gap: 6 }}>
        <button onClick={run} disabled={!!busy} style={{ ...sty.btn, flex: 1 }}>
          {busy === 'bt' ? '回测中…' : '运行回测'}
        </button>
        <button onClick={runSweep} disabled={!!busy} style={{ ...sty.btn, ...sty.btnAlt }}>
          {busy === 'sw' ? '寻优中…' : '参数寻优'}
        </button>
      </div>

      <button onClick={runErSweep} disabled={!!busy} style={{ ...sty.btn, ...sty.btnAlt }}>
        {busy === 'er' ? '对比中…' : 'ER 闸门阈值对比'}
      </button>

      {res?.error && <div style={sty.err}>{res.error}</div>}

      {res && !res.error && (
        <div style={sty.result}>
          <div style={sty.statGrid}>
            <Stat k="策略收益" v={fmt.pct(res.return_pct)} big
                  c={res.return_pct >= 0 ? '#00c9a7' : '#e05263'} />
            <Stat k="买入持有" v={fmt.pct(res.hold_pct)}
                  c={res.hold_pct >= 0 ? '#00c9a7' : '#e05263'} />
            <Stat k="超额 α" v={fmt.pct(res.alpha_pct)}
                  c={res.alpha_pct >= 0 ? '#00c9a7' : '#e05263'} />
            <Stat k="最大回撤" v={`-${res.max_dd_pct}%`} c="#e05263" />
            <Stat k="胜率" v={`${res.win_rate}%`} sub={`${res.wins}/${res.trades}`} />
            <Stat k="盈亏比" v={res.profit_factor ?? '∞'} />
            <Stat k="交易次数" v={res.trades} sub={`均持 ${res.avg_bars} 根`} />
            <Stat k="期末资金" v={fmt.vol(res.final)} />
          </div>

          {res.equity?.length > 1 && <Equity data={res.equity} up={res.return_pct >= 0} />}

          <div style={sty.gateBar}>
            <GateStat k="被闸门拦下" v={res.er_blocked} on={res.er_min != null} />
            <GateStat k="止盈" v={res.tp1_count} on={!!res.exit_rules} />
            <GateStat k="止损" v={res.stop_count} on={!!res.exit_rules} />
            <GateStat k="反向平仓" v={res.reverse_count} on />
            {res.quick_trades > 0 && <GateStat k="弱档笔数" v={res.quick_trades} on />}
            {res.liq_count > 0 && <GateStat k="爆仓" v={res.liq_count} on warn />}
            {res.skipped_insufficient > 0 &&
              <GateStat k="保证金不足" v={res.skipped_insufficient} on warn />}
          </div>

          <div style={sty.subTitle}>最近成交（{res.trade_list?.length || 0}）</div>
          <div style={{ maxHeight: 180, overflowY: 'auto' }}>
            {(res.trade_list || []).slice().reverse().map((t, i) => (
              <div key={i} style={sty.trade}>
                <span style={{
                  fontSize: 9, fontWeight: 800, width: 30,
                  color: t.side === 'long' ? '#00c9a7' : '#e05263',
                }}>
                  {t.side === 'long' ? '多' : '空'}{t.open ? '*' : ''}
                </span>
                <span style={sty.tradeTs}>{fmt.datetime(t.entry_ts)}</span>
                <span style={{ fontSize: 9.5, color: '#5a6270', fontFamily: 'var(--font-mono)' }}>
                  {fmt.price(t.entry)} → {fmt.price(t.exit)}
                </span>
                <span style={{ flex: 1 }} />
                {t.reason && (
                  <span style={{
                    fontSize: 8.5, color: t.reason === '止盈' ? '#00c9a7'
                      : t.reason === '爆仓' ? '#e05263' : '#4a5058',
                  }}>
                    {t.reason}{t.exits?.length > 1 ? `(${t.exits.length})` : ''}
                  </span>
                )}
                <span style={{
                  fontSize: 10.5, fontWeight: 700, fontFamily: 'var(--font-mono)',
                  color: t.pnl_pct >= 0 ? '#00c9a7' : '#e05263',
                }}>
                  {t.pnl_pct > 0 ? '+' : ''}{t.pnl_pct}%
                </span>
              </div>
            ))}
          </div>
          <div style={{ fontSize: 9, color: '#3f4650', marginTop: 4 }}>
            * = 区间末尾仍持仓，按最后收盘价结算
          </div>
        </div>
      )}

      {sweep?.error && <div style={sty.err}>{sweep.error}</div>}

      {sweep && !sweep.error && (
        <div style={sty.result}>
          <div style={sty.subTitle}>
            参数寻优 · {sweep.tf} · {sweep.bars} 根 · 共 {sweep.count} 组
          </div>
          <div style={{ ...sty.sweepRow, color: '#00c9a7', fontWeight: 700, borderBottom: '1px solid #262626' }}>
            <span style={{ width: 58 }}>参数</span>
            <span style={{ width: 56, textAlign: 'right' }}>收益</span>
            <span style={{ width: 48, textAlign: 'right' }}>回撤</span>
            <span style={{ width: 36, textAlign: 'right' }}>次数</span>
            <span style={{ width: 40, textAlign: 'right' }}>胜率</span>
            <span style={{ width: 30 }} />
          </div>
          <div style={{ maxHeight: 260, overflowY: 'auto' }}>
            {sweep.rows.map((r, i) => (
              <div key={i} style={sty.sweepRow}>
                <span style={{ width: 58, fontFamily: 'var(--font-mono)', color: '#c8ccd4' }}>
                  {r.periods}×{r.multiplier}
                </span>
                <span style={{
                  width: 56, textAlign: 'right', fontFamily: 'var(--font-mono)',
                  color: r.return_pct >= 0 ? '#00c9a7' : '#e05263',
                }}>
                  {r.return_pct > 0 ? '+' : ''}{r.return_pct}%
                </span>
                <span style={{ width: 48, textAlign: 'right', color: '#8b93a0', fontFamily: 'var(--font-mono)' }}>
                  {r.max_dd_pct}%
                </span>
                <span style={{ width: 36, textAlign: 'right', color: '#5a6270' }}>{r.trades}</span>
                <span style={{ width: 40, textAlign: 'right', color: '#5a6270' }}>{r.win_rate}%</span>
                <button onClick={() => applyParams(r)} style={sty.useBtn} title="应用到全局参数">用</button>
              </div>
            ))}
          </div>
          <div style={{ fontSize: 9, color: '#3f4650', marginTop: 6, lineHeight: 1.7 }}>
            按历史收益排序。样本内最优 ≠ 未来最优，别只挑第一名 ——
            优先选「收益尚可 + 回撤小 + 交易次数不过少」的一档。
          </div>
        </div>
      )}

      {erSweep?.error && <div style={sty.err}>{erSweep.error}</div>}

      {erSweep && !erSweep.error && (
        <div style={sty.result}>
          <div style={sty.subTitle}>
            ER 闸门对比 · {erSweep.tf} · {erSweep.bars} 根
            {erSweep.live_er_min != null &&
              <span style={{ color: '#4a5058', fontWeight: 400 }}>
                　实盘当前 {erSweep.live_er_min}
              </span>}
          </div>
          <div style={{ ...sty.sweepRow, color: '#00c9a7', fontWeight: 700, borderBottom: '1px solid #262626' }}>
            <span style={{ width: 46 }}>阈值</span>
            <span style={{ width: 54, textAlign: 'right' }}>收益</span>
            <span style={{ width: 46, textAlign: 'right' }}>回撤</span>
            <span style={{ width: 32, textAlign: 'right' }}>笔数</span>
            <span style={{ width: 30, textAlign: 'right' }}>拦</span>
            <span style={{ width: 38, textAlign: 'right' }}>胜率</span>
          </div>
          {erSweep.rows.map((r, i) => {
            const live = r.er_min != null && r.er_min === erSweep.live_er_min;
            return (
              <div key={i} style={{
                ...sty.sweepRow,
                background: live ? '#00c9a70d' : undefined,
              }}>
                <span style={{
                  width: 46, fontFamily: 'var(--font-mono)',
                  color: live ? '#00c9a7' : '#c8ccd4', fontWeight: live ? 700 : 400,
                }}>
                  {r.er_min == null ? '无' : r.er_min.toFixed(2)}
                </span>
                <span style={{
                  width: 54, textAlign: 'right', fontFamily: 'var(--font-mono)',
                  color: r.return_pct >= 0 ? '#00c9a7' : '#e05263',
                }}>
                  {r.return_pct > 0 ? '+' : ''}{r.return_pct}%
                </span>
                <span style={{ width: 46, textAlign: 'right', color: '#8b93a0', fontFamily: 'var(--font-mono)' }}>
                  {r.max_dd_pct}%
                </span>
                <span style={{ width: 32, textAlign: 'right', color: '#5a6270' }}>{r.trades}</span>
                <span style={{ width: 30, textAlign: 'right', color: '#5a6270' }}>{r.blocked}</span>
                <span style={{ width: 38, textAlign: 'right', color: '#5a6270' }}>{r.win_rate}%</span>
              </div>
            );
          })}
          <div style={{ fontSize: 9, color: '#3f4650', marginTop: 6, lineHeight: 1.7 }}>
            第一行「无」= 不设闸门的基准。按阈值升序排，要看的是<b>趋势</b> ——
            收益/回撤随阈值怎么走。单品种通常只有几十笔，
            某一档特别高多半是噪声，换个品种就变了。要下结论请多跑几个品种交叉验证。
          </div>
        </div>
      )}
    </div>
  );
}

function GateStat({ k, v, on, warn }) {
  if (!on) return null;
  return (
    <span style={{ fontSize: 9, color: '#4a5058' }}>
      {k}
      <b style={{
        fontFamily: 'var(--font-mono)', marginLeft: 3,
        color: warn ? '#e05263' : '#8b93a0',
      }}>{v ?? 0}</b>
    </span>
  );
}

function Equity({ data, up }) {
  const vals = data.map((d) => d.equity);
  const min = Math.min(...vals), max = Math.max(...vals);
  const span = max - min || 1;
  const W = 258, H = 54;
  const pts = data
    .map((d, i) => `${(i / (data.length - 1)) * W},${H - ((d.equity - min) / span) * H}`)
    .join(' ');
  const col = up ? '#00c9a7' : '#e05263';
  return (
    <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ display: 'block', margin: '6px 0' }}>
      <polyline points={pts} fill="none" stroke={col} strokeWidth="1.2" />
      <polygon points={`0,${H} ${pts} ${W},${H}`} fill={col} opacity="0.1" />
    </svg>
  );
}

function Field({ label, children }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <span style={{ fontSize: 9.5, color: '#4a5058' }}>{label}</span>
      {children}
    </label>
  );
}

function Stat({ k, v, sub, c, big }) {
  return (
    <div style={{ background: '#ffffff05', borderRadius: 4, padding: '5px 7px' }}>
      <div style={{ fontSize: 9, color: '#4a5058' }}>{k}</div>
      <div style={{
        fontSize: big ? 14 : 12, fontWeight: 700,
        fontFamily: 'var(--font-mono)', color: c || '#e9ecef',
      }}>{v}</div>
      {sub && <div style={{ fontSize: 8.5, color: '#3f4650' }}>{sub}</div>}
    </div>
  );
}

function Check({ on, onClick, label }) {
  return (
    <button onClick={onClick} style={{
      flex: 1, background: on ? '#00c9a71a' : 'transparent',
      border: `1px solid ${on ? '#00c9a766' : '#262626'}`, borderRadius: 3,
      color: on ? '#00c9a7' : '#5a6270', fontSize: 10, fontWeight: 600,
      padding: '5px', cursor: 'pointer',
    }}>
      {on ? '✓ ' : ''}{label}
    </button>
  );
}

const sty = {
  wrap: { padding: '8px 10px 16px', display: 'flex', flexDirection: 'column', gap: 8 },
  grid2: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 },
  input: {
    width: '100%', background: '#161616', border: '1px solid #2c2c2c', borderRadius: 3,
    color: '#e9ecef', fontSize: 11, padding: '4px 6px', fontFamily: 'var(--font-mono)',
  },
  btn: {
    background: '#00c9a7', border: 'none', borderRadius: 4, color: '#04140f',
    fontSize: 11, fontWeight: 800, padding: '7px 10px', cursor: 'pointer',
  },
  btnAlt: { background: 'transparent', border: '1px solid #00c9a755', color: '#00c9a7' },
  err: {
    background: '#8b000033', border: '1px solid #c2185b55', borderRadius: 4,
    padding: '7px 9px', fontSize: 10.5, color: '#e05263',
  },
  result: {
    borderTop: '1px solid #1e1e1e', paddingTop: 8,
    display: 'flex', flexDirection: 'column', gap: 4,
  },
  statGrid: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 5 },
  sect: {
    fontSize: 9.5, fontWeight: 700, color: '#00c9a7',
    borderTop: '1px solid #1e1e1e', paddingTop: 7, marginTop: 1,
  },
  gateBar: {
    display: 'flex', flexWrap: 'wrap', gap: 9, padding: '5px 2px',
    borderTop: '1px solid #141414', marginTop: 2,
  },
  subTitle: {
    fontSize: 10, fontWeight: 700, color: '#00c9a7',
    marginTop: 6, paddingBottom: 3,
  },
  trade: {
    display: 'flex', alignItems: 'center', gap: 6,
    padding: '4px 2px', borderBottom: '1px solid #141414',
  },
  tradeTs: { fontSize: 9, color: '#4a5058', width: 62, fontFamily: 'var(--font-mono)' },
  sweepRow: {
    display: 'flex', alignItems: 'center', gap: 4, fontSize: 10,
    padding: '4px 2px', borderBottom: '1px solid #141414',
  },
  useBtn: {
    width: 26, background: 'transparent', border: '1px solid #2c2c2c',
    borderRadius: 3, color: '#8b93a0', fontSize: 9, cursor: 'pointer', padding: '1px 0',
  },
};
