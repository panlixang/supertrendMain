/** 实时信号弹窗：收到 WS 的 signal 消息时右上角提示 8 秒 */
import { useStore } from '../stores/useStore';
import { fmt, gradeColor } from '../utils/format';

export default function Toasts() {
  const toasts = useStore((s) => s.toasts);
  if (!toasts.length) return null;

  return (
    <div style={sty.wrap}>
      {toasts.map((t) => {
        const buy = t.type === 'buy';
        const col = buy ? '#00c9a7' : '#c2185b';
        return (
          <div key={t.id} style={{
            ...sty.item, borderColor: col + '88',
            background: buy ? '#00c9a71a' : '#8b000040',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ fontSize: 13, fontWeight: 800, color: buy ? '#00c9a7' : '#e05263' }}>
                {t.label}
              </span>
              <span style={{
                fontSize: 9, fontWeight: 800, padding: '1px 5px', borderRadius: 3,
                border: `1px solid ${gradeColor(t.grade)}66`,
                background: gradeColor(t.grade) + '22', color: gradeColor(t.grade),
              }}>{t.grade || '—'}</span>
              <span style={sty.tf}>{t.tf}</span>
            </div>
            <div style={{ fontSize: 11, color: '#c8ccd4', fontFamily: 'var(--font-mono)' }}>
              {fmt.price(t.price)}
            </div>
          </div>
        );
      })}
    </div>
  );
}

const sty = {
  wrap: {
    position: 'fixed', top: 62, right: 16, zIndex: 200,
    display: 'flex', flexDirection: 'column', gap: 6,
  },
  item: {
    minWidth: 190, borderWidth: 1, borderStyle: 'solid', borderColor: 'transparent', borderRadius: 6, padding: '8px 12px',
    display: 'flex', flexDirection: 'column', gap: 3,
    backdropFilter: 'blur(6px)', animation: 'slideIn .25s ease',
  },
  tf: {
    fontSize: 9, color: '#8b93a0', background: '#ffffff10',
    borderRadius: 3, padding: '1px 5px', fontFamily: 'var(--font-mono)',
  },
};
