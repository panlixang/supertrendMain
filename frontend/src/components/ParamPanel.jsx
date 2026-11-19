/**
 * 参数面板 —— 对应 Pine 的 input 区块
 *   ▸ Supertrend Settings : Periods / src / Multiplier / changeATR
 *   ▸ MTF Bias Table      : fastLen / slowLen / maType
 * 改动 POST /api/params，后端全量重算并推 snapshot。
 */
import { useEffect, useState } from 'react';
import { DEFAULT_PARAMS, useStore } from '../stores/useStore';
import { API } from '../utils/format';

const SRC_OPTS = ['hl2', 'close', 'hlc3', 'ohlc4', 'high', 'low'];

// 常见调参档位：默认值是作者在特定品种上调的，换品种基本要重调
const PRESETS = [
  { name: '脚本默认', desc: '原脚本 · 极少信号', p: { periods: 15, multiplier: 9.1 } },
  { name: '经典 ST', desc: 'TV 通用默认', p: { periods: 10, multiplier: 3.0 } },
  { name: '灵敏', desc: '短线 · 信号密', p: { periods: 7, multiplier: 2.0 } },
  { name: '稳健', desc: '波段 · 抗震荡', p: { periods: 14, multiplier: 5.0 } },
];

export default function ParamPanel() {
  const params = useStore((s) => s.params);
  const [form, setForm] = useState(params);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState('');

  useEffect(() => setForm(params), [params]);

  const dirty = JSON.stringify(form) !== JSON.stringify(params);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  async function save(payload) {
    setSaving(true);
    setMsg('');
    try {
      const r = await fetch(`${API}/api/params`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || form),
      });
      const d = await r.json();
      if (d.ok) {
        useStore.getState().setParams(d.params);
        setMsg('已应用，全周期重算中…');
      } else {
        setMsg(d.error || '保存失败');
      }
    } catch {
      setMsg('网络错误');
    } finally {
      setSaving(false);
      setTimeout(() => setMsg(''), 4000);
    }
  }

  return (
    <div style={sty.wrap}>
      <Section title="▸ Supertrend Settings">
        <Row label="ATR Period" hint="Pine: Periods">
          <input type="number" min={1} value={form.periods}
                 onChange={(e) => set('periods', +e.target.value)} style={sty.input} />
        </Row>
        <Row label="ATR Multiplier" hint="倍数越大信号越少">
          <input type="number" step={0.1} min={0.1} value={form.multiplier}
                 onChange={(e) => set('multiplier', +e.target.value)} style={sty.input} />
        </Row>
        <Row label="Source" hint="中轨取价">
          <select value={form.src} onChange={(e) => set('src', e.target.value)} style={sty.input}>
            {SRC_OPTS.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </Row>
        <Row label="Change ATR Method" hint={form.change_atr ? 'ta.atr（RMA）' : 'sma(tr)'}>
          <Toggle on={form.change_atr} onClick={() => set('change_atr', !form.change_atr)} />
        </Row>
      </Section>

      <div style={sty.presets}>
        {PRESETS.map((p) => {
          const on = form.periods === p.p.periods && form.multiplier === p.p.multiplier;
          return (
            <button key={p.name}
                    onClick={() => setForm((f) => ({ ...f, ...p.p }))}
                    style={{ ...sty.preset, borderColor: on ? '#00c9a7' : '#262626',
                             color: on ? '#00c9a7' : '#8b93a0' }}>
              <b style={{ fontSize: 10.5 }}>{p.name}</b>
              <span style={{ fontSize: 8.5, color: '#4a5058' }}>{p.desc}</span>
              <span style={{ fontSize: 9, fontFamily: 'var(--font-mono)', color: '#5a6270' }}>
                {p.p.periods} × {p.p.multiplier}
              </span>
            </button>
          );
        })}
      </div>

      <Section title="▸ MTF Bias Table">
        <Row label="Fast MA Length" hint="Pine: fastLen">
          <input type="number" min={1} value={form.fast_len}
                 onChange={(e) => set('fast_len', +e.target.value)} style={sty.input} />
        </Row>
        <Row label="Slow MA Length" hint="Pine: slowLen">
          <input type="number" min={1} value={form.slow_len}
                 onChange={(e) => set('slow_len', +e.target.value)} style={sty.input} />
        </Row>
        <Row label="MA Type" hint="fast ≥ slow → Bull">
          <select value={form.ma_type} onChange={(e) => set('ma_type', e.target.value)} style={sty.input}>
            <option value="EMA">EMA</option>
            <option value="SMA">SMA</option>
          </select>
        </Row>
      </Section>

      <div style={{ display: 'flex', gap: 6 }}>
        <button disabled={!dirty || saving} onClick={() => save()}
                style={{ ...sty.apply, opacity: dirty && !saving ? 1 : 0.35,
                         cursor: dirty && !saving ? 'pointer' : 'not-allowed' }}>
          {saving ? '应用中…' : dirty ? '应用参数' : '已是当前参数'}
        </button>
        <button onClick={() => { setForm(DEFAULT_PARAMS); save(DEFAULT_PARAMS); }}
                style={sty.reset}>重置</button>
      </div>
      {msg && <div style={sty.msg}>{msg}</div>}

      <div style={sty.note}>
        原脚本默认 <b style={{ color: '#8b93a0' }}>15 × 9.1</b> 是作者在特定品种上调出来的，
        倍数极大 → 只在大级别反转才翻向。换品种或换周期后，建议到「回测」页
        跑一遍参数寻优再定。
      </div>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div style={sty.section}>
      <div style={sty.secTitle}>{title}</div>
      {children}
    </div>
  );
}

function Row({ label, hint, children }) {
  return (
    <div style={sty.row}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 11, color: '#c8ccd4' }}>{label}</div>
        {hint && <div style={{ fontSize: 8.5, color: '#4a5058' }}>{hint}</div>}
      </div>
      {children}
    </div>
  );
}

function Toggle({ on, onClick }) {
  return (
    <button onClick={onClick} style={{
      width: 34, height: 17, borderRadius: 9, border: 'none', cursor: 'pointer',
      background: on ? '#00c9a7' : '#2c2c2c', position: 'relative',
      transition: 'background .2s', flexShrink: 0,
    }}>
      <span style={{
        position: 'absolute', top: 2, left: on ? 19 : 2, width: 13, height: 13,
        borderRadius: '50%', background: '#fff', transition: 'left .2s',
      }} />
    </button>
  );
}

const sty = {
  wrap: { padding: '8px 10px 16px', display: 'flex', flexDirection: 'column', gap: 10 },
  section: { display: 'flex', flexDirection: 'column', gap: 2 },
  secTitle: {
    fontSize: 10, fontWeight: 800, color: '#00c9a7', letterSpacing: 0.5,
    padding: '2px 0 5px', borderBottom: '1px solid #262626', marginBottom: 4,
  },
  row: { display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0' },
  input: {
    width: 78, background: '#161616', border: '1px solid #2c2c2c', borderRadius: 3,
    color: '#e9ecef', fontSize: 11, padding: '4px 6px',
    fontFamily: 'var(--font-mono)', flexShrink: 0,
  },
  presets: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 5 },
  preset: {
    display: 'flex', flexDirection: 'column', gap: 1, alignItems: 'flex-start',
    background: '#ffffff05', borderWidth: 1, borderStyle: 'solid', borderColor: 'transparent', borderRadius: 4,
    padding: '5px 7px', cursor: 'pointer', textAlign: 'left',
  },
  apply: {
    flex: 1, background: '#00c9a7', border: 'none', borderRadius: 4,
    color: '#04140f', fontSize: 11, fontWeight: 800, padding: '7px',
  },
  reset: {
    background: 'transparent', border: '1px solid #2c2c2c', borderRadius: 4,
    color: '#8b93a0', fontSize: 11, padding: '7px 12px', cursor: 'pointer',
  },
  msg: { fontSize: 10, color: '#00c9a7' },
  note: {
    fontSize: 9.5, color: '#5a6270', lineHeight: 1.7,
    borderTop: '1px solid #1e1e1e', paddingTop: 8,
  },
};
