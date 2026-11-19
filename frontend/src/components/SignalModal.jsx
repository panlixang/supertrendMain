/**
 * 买卖信号醒目弹窗
 *
 * 与右上角小提示（Toasts）的分工：
 *   Toasts  — 一闪而过，用于次要信息
 *   这个     — 居中遮罩 + 提示音，必须手动点掉，避免刷走没看见
 *
 * 同时显示这条信号「有没有真去挂单」以及没挂的原因（震荡行情等）。
 */
import { useEffect, useRef } from 'react';
import { useStore } from '../stores/useStore';
import { fmt, gradeColor } from '../utils/format';

// 用 WebAudio 现场合成提示音，省掉音频文件依赖。
// 买单上行两声、卖单下行两声，不用看屏幕也能分辨方向。
function beep(isBuy) {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    const notes = isBuy ? [660, 990] : [560, 380];
    notes.forEach((f, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.value = f;
      const t0 = ctx.currentTime + i * 0.18;
      gain.gain.setValueAtTime(0.0001, t0);
      gain.gain.exponentialRampToValueAtTime(0.25, t0 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.16);
      osc.connect(gain).connect(ctx.destination);
      osc.start(t0);
      osc.stop(t0 + 0.18);
    });
    setTimeout(() => ctx.close(), 800);
  } catch {}
}

export default function SignalModal() {
  const sig = useStore((s) => s.modalSignal);
  const close = useStore((s) => s.closeModal);
  const soundOn = useStore((s) => s.opts.sound);
  const played = useRef(null);

  // 同一条信号只响一次（弹窗重渲染不重复播）
  useEffect(() => {
    if (!sig || !soundOn) return;
    const key = `${sig.tf}-${sig.ts}-${sig.type}`;
    if (played.current === key) return;
    played.current = key;
    beep(sig.type === 'buy');
  }, [sig, soundOn]);

  // Esc 关闭
  useEffect(() => {
    if (!sig) return;
    const onKey = (e) => e.key === 'Escape' && close();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [sig, close]);

  if (!sig) return null;

  const buy = sig.type === 'buy';
  const col = buy ? '#00c9a7' : '#c2185b';
  const reg = sig.regime || {};
  const order = sig.order;
  const traded = order?.ok;

  return (
    <div style={sty.mask} onClick={close}>
      <div style={{ ...sty.box, borderColor: col }} onClick={(e) => e.stopPropagation()}>
        <div style={{ ...sty.head, background: buy ? '#00c9a71a' : '#8b000033' }}>
          <span style={{ fontSize: 30, fontWeight: 900, color: buy ? '#00c9a7' : '#e05263' }}>
            {buy ? '▲ BUY' : '▼ SELL'}
          </span>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 15, fontWeight: 700 }}>{sig.symbol || ''}</div>
            <div style={{ fontSize: 11, color: '#8b93a0' }}>
              {sig.tf} 周期超趋翻转 · {fmt.datetime(sig.ts)}
            </div>
          </div>
          <span style={{
            fontSize: 13, fontWeight: 900, padding: '3px 10px', borderRadius: 4,
            borderWidth: 1, borderStyle: 'solid',
            borderColor: gradeColor(sig.grade) + '88',
            background: gradeColor(sig.grade) + '22', color: gradeColor(sig.grade),
          }}>{sig.grade || '—'} 级</span>
        </div>

        <div style={sty.grid}>
          <Cell k="触发价" v={fmt.price(sig.price)} big />
          <Cell k="超趋线 / 建议止损" v={fmt.price(sig.line)} />
          <Cell k="强度" v={`${sig.score ?? 0} / 3`} />
          <Cell k="MTF Bias" v={sig.bias_label || '—'}
                sub={`多 ${sig.bulls ?? '?'} / 空 ${sig.bears ?? '?'}`} />
        </div>

        {/* 行情状态：震荡时高亮为警告色 */}
        <div style={{
          ...sty.regime,
          borderColor: reg.regime === 'range' ? '#f5a62366' : '#00c9a744',
          background: reg.regime === 'range' ? '#f5a62312' : '#00c9a70d',
        }}>
          <span style={{
            fontWeight: 800, fontSize: 12,
            color: reg.regime === 'range' ? '#f5a623' : '#00c9a7',
          }}>
            {reg.regime === 'range' ? '⚠ ' : '✓ '}{reg.label || '—'}
          </span>
          <span style={{ fontSize: 10.5, color: '#8b93a0', fontFamily: 'var(--font-mono)' }}>
            效率比 ER {reg.er ?? '—'}
          </span>
        </div>

        {/* 挂单结果 */}
        {traded ? (
          <div style={{ ...sty.order, borderColor: '#00c9a766', background: '#00c9a712' }}>
            <div style={{ fontWeight: 800, color: '#00c9a7', fontSize: 12 }}>
              ✅ 已成交{order.paper ? '（模拟盘）' : '（⚠️ 实盘）'}
            </div>
            <div style={{ fontSize: 11, color: '#c8ccd4', fontFamily: 'var(--font-mono)' }}>
              {fmt.price(order.price)} × {order.qty} ≈ {order.amount} USDT
            </div>
            <div style={{ fontSize: 9.5, color: '#4a5058' }}>订单号 {order.orderId}</div>
          </div>
        ) : order && !order.ok ? (
          <div style={{ ...sty.order, borderColor: '#c2185b66', background: '#8b000022' }}>
            <div style={{ fontWeight: 800, color: '#e05263', fontSize: 12 }}>
              {String(order.error || '').includes('未成交') ? '⏸ 未成交已撤' : '❌ 挂单失败'}
            </div>
            <div style={{ fontSize: 11, color: '#c8ccd4' }}>{order.error}</div>
          </div>
        ) : (
          <div style={{ ...sty.order, borderColor: '#2c2c2c', background: '#ffffff05' }}>
            <div style={{ fontWeight: 800, color: '#8b93a0', fontSize: 12 }}>⏸ 未挂单，仅提醒</div>
            {(sig.gate_reasons || []).map((r, i) => (
              <div key={i} style={{ fontSize: 10.5, color: '#8b93a0' }}>· {r}</div>
            ))}
            {reg.regime === 'range' && (
              <div style={{ fontSize: 9.5, color: '#f5a623', marginTop: 2 }}>
                实测：震荡行情下信号亏损率约 80%，已按设置跳过挂单
              </div>
            )}
          </div>
        )}

        <button onClick={close} style={{ ...sty.btn, background: col }}>
          知道了（Esc）
        </button>
      </div>
    </div>
  );
}

function Cell({ k, v, sub, big }) {
  return (
    <div style={{ background: '#ffffff06', borderRadius: 5, padding: '7px 9px' }}>
      <div style={{ fontSize: 9.5, color: '#4a5058' }}>{k}</div>
      <div style={{
        fontSize: big ? 16 : 13, fontWeight: 700,
        fontFamily: 'var(--font-mono)', color: '#e9ecef',
      }}>{v}</div>
      {sub && <div style={{ fontSize: 9, color: '#4a5058' }}>{sub}</div>}
    </div>
  );
}

const sty = {
  mask: {
    position: 'fixed', inset: 0, zIndex: 500, background: '#000000c4',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    backdropFilter: 'blur(3px)', animation: 'fadeIn .15s ease',
  },
  box: {
    width: 400, maxWidth: '92vw', background: '#111',
    borderWidth: 2, borderStyle: 'solid', borderRadius: 10,
    overflow: 'hidden', boxShadow: '0 20px 60px #000c',
    display: 'flex', flexDirection: 'column', gap: 10, padding: 0,
    animation: 'popIn .2s cubic-bezier(.2,1.2,.4,1)',
  },
  head: {
    display: 'flex', alignItems: 'center', gap: 12, padding: '14px 16px',
    borderBottom: '1px solid #262626',
  },
  grid: {
    display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, padding: '0 14px',
  },
  regime: {
    margin: '0 14px', padding: '7px 10px', borderRadius: 5,
    borderWidth: 1, borderStyle: 'solid',
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
  },
  order: {
    margin: '0 14px', padding: '8px 10px', borderRadius: 5,
    borderWidth: 1, borderStyle: 'solid',
    display: 'flex', flexDirection: 'column', gap: 3,
  },
  btn: {
    margin: '4px 14px 14px', padding: '9px', border: 'none', borderRadius: 5,
    color: '#06120f', fontSize: 12, fontWeight: 800, cursor: 'pointer',
  },
};
