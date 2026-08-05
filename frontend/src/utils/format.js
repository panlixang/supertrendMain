// 同源相对路径：请求发回当前页面所在主机，由 Vite 代理转给后端 :8000。
// 写死 localhost 会导致远程访问（如服务器 IP）时浏览器去连访问者自己的电脑。
export const API = '';

export const fmt = {
  // 价格位数随量级自适应：BTC 用 2 位，SHIB 这种要 6 位
  price: (v) => {
    if (v == null || Number.isNaN(v)) return '—';
    const n = Number(v);
    const d = Math.abs(n) >= 100 ? 2 : Math.abs(n) >= 1 ? 4 : 6;
    return n.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  },
  pct: (v) => (v == null ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%'),
  num: (v, d = 2) => (v == null ? '—' : Number(v).toFixed(d)),
  vol: (v) => {
    if (v == null) return '—';
    if (v >= 1e9) return (v / 1e9).toFixed(2) + 'B';
    if (v >= 1e6) return (v / 1e6).toFixed(2) + 'M';
    if (v >= 1e3) return (v / 1e3).toFixed(2) + 'K';
    return Number(v).toFixed(2);
  },
  time: (ts) => (ts ? new Date(ts).toLocaleTimeString('zh-CN', { hour12: false }) : '—'),
  datetime: (ts) => {
    if (!ts) return '—';
    const d = new Date(ts);
    const p = (n) => String(n).padStart(2, '0');
    return `${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  },
  ago: (ts) => {
    if (!ts) return '—';
    const s = Math.max(0, (Date.now() - ts) / 1000);
    if (s < 60) return `${s | 0}秒前`;
    if (s < 3600) return `${(s / 60) | 0}分前`;
    if (s < 86400) return `${(s / 3600) | 0}小时前`;
    return `${(s / 86400) | 0}天前`;
  },
};

export const C = {
  bull: '#00c9a7',
  bear: '#8b0000',
  bearLit: '#e05263',
  buy: '#00c9a7',
  sell: '#c2185b',
  muted: '#6c7480',
};

// 趋势 / 偏向的统一取色：+1 多头绿，-1 空头红
export const trendColor = (t, lit = true) =>
  t === 1 ? C.bull : t === -1 ? (lit ? C.bearLit : C.bear) : C.muted;

export const gradeColor = (g) =>
  ({ A: '#00c9a7', B: '#f5a623', C: '#6c7480' })[g] || '#6c7480';
