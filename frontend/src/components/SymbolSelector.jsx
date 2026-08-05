/** 品种选择器：搜 OKX 全量 instruments，选中后 POST /api/symbol 切换 */
import { useEffect, useRef, useState } from 'react';
import { useStore } from '../stores/useStore';
import { API } from '../utils/format';

export default function SymbolSelector() {
  const symbol = useStore((s) => s.symbol);
  const setSymbol = useStore((s) => s.setSymbol);

  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const [list, setList] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const boxRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    let cancel = false;
    const t = setTimeout(async () => {
      try {
        const r = await fetch(`${API}/api/instruments?q=${encodeURIComponent(q)}&limit=40`);
        const d = await r.json();
        if (!cancel) setList(Array.isArray(d) ? d : []);
      } catch {
        if (!cancel) setList([]);
      }
    }, 250);
    return () => { cancel = true; clearTimeout(t); };
  }, [q, open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e) => {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    setTimeout(() => inputRef.current?.focus(), 30);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  async function pick(inst) {
    if (!inst || inst === symbol) { setOpen(false); return; }
    setErr(''); setBusy(true);
    const prev = symbol;
    setSymbol(inst);                       // 乐观更新
    try {
      const r = await fetch(`${API}/api/symbol`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ instId: inst }),
      });
      const d = await r.json();
      if (!d.ok) { setSymbol(prev); setErr(d.error || '切换失败'); }
      else { setOpen(false); setQ(''); }
    } catch {
      setSymbol(prev); setErr('网络错误');
    } finally { setBusy(false); }
  }

  return (
    <div ref={boxRef} style={{ position: 'relative' }}>
      <button onClick={() => setOpen((o) => !o)} style={sty.chip}>
        <span style={{ fontWeight: 800, fontSize: 14 }}>{symbol}</span>
        <span style={{ fontSize: 8, color: '#5a6270' }}>▼</span>
      </button>

      {open && (
        <div style={sty.drop}>
          <input ref={inputRef} value={q} onChange={(e) => setQ(e.target.value)}
                 placeholder="搜索品种，如 BTC / ETH / NVDA" style={sty.search} />
          {err && <div style={{ fontSize: 10, color: '#e05263', padding: '4px 8px' }}>{err}</div>}
          {busy && <div style={{ fontSize: 10, color: '#00c9a7', padding: '4px 8px' }}>切换中…</div>}
          <div style={{ maxHeight: 280, overflowY: 'auto' }}>
            {list.map((r) => (
              <button key={r.instId} onClick={() => pick(r.instId)}
                      style={{ ...sty.item, background: r.instId === symbol ? '#ffffff0a' : 'transparent' }}>
                <span style={{ fontSize: 11, color: '#e9ecef', fontFamily: 'var(--font-mono)' }}>
                  {r.instId}
                </span>
                <span style={{ flex: 1 }} />
                {r.isStock && <span style={sty.tagStock}>美股</span>}
                <span style={sty.tagType}>{r.instType}</span>
              </button>
            ))}
            {!list.length && (
              <div style={{ padding: 12, fontSize: 10, color: '#4a5058', textAlign: 'center' }}>
                无匹配品种
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

const sty = {
  chip: {
    display: 'flex', alignItems: 'center', gap: 6,
    background: '#161616', border: '1px solid #2c2c2c', borderRadius: 5,
    color: '#e9ecef', cursor: 'pointer', padding: '5px 10px',
  },
  drop: {
    position: 'absolute', top: '110%', left: 0, zIndex: 100, width: 260,
    background: '#111', border: '1px solid #2c2c2c', borderRadius: 6,
    boxShadow: '0 8px 28px #000a', overflow: 'hidden',
  },
  search: {
    width: '100%', background: '#0d0d0d', border: 'none',
    borderBottom: '1px solid #262626', color: '#e9ecef',
    fontSize: 11, padding: '8px 10px', outline: 'none',
  },
  item: {
    display: 'flex', alignItems: 'center', gap: 6, width: '100%',
    border: 'none', cursor: 'pointer', padding: '6px 10px', textAlign: 'left',
  },
  tagStock: {
    fontSize: 8.5, background: '#f5a62322', color: '#f5a623',
    borderRadius: 2, padding: '1px 4px',
  },
  tagType: { fontSize: 8.5, color: '#4a5058' },
};
