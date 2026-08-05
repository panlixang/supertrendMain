import { create } from 'zustand';

export const ALL_TFS = ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w', '1M'];
export const BIAS_TFS = ['5m', '15m', '30m', '1h', '4h', '1d', '1w', '1M'];

const byTf = (v) => Object.fromEntries(ALL_TFS.map((tf) => [tf, v]));

// 对应 Pine 的 input 默认值
export const DEFAULT_PARAMS = {
  periods: 15,
  multiplier: 9.1,
  src: 'hl2',
  change_atr: true,
  fast_len: 20,
  slow_len: 50,
  ma_type: 'EMA',
};

export const useStore = create((set) => ({
  connected: false,
  setConnected: (v) => set({ connected: v }),

  symbol: 'BTC-USDT',
  setSymbol: (s) => set({ symbol: s }),

  ticker: null,
  setTicker: (t) => set({ ticker: t }),

  tf: '1h',
  setTf: (tf) => set({ tf }),

  candles: byTf([]),
  setCandles: (tf, data) => set((s) => ({ candles: { ...s.candles, [tf]: data } })),
  upsertCandle: (tf, c) =>
    set((s) => {
      const list = [...(s.candles[tf] || [])];
      const i = list.findIndex((x) => x.ts === c.ts);
      if (i >= 0) list[i] = c;
      else list.push(c);
      return { candles: { ...s.candles, [tf]: list } };
    }),

  // 按周期缓存的 /api/indicators 结果
  indicators: byTf(null),
  setIndicators: (tf, v) => set((s) => ({ indicators: { ...s.indicators, [tf]: v } })),
  clearIndicators: () => set({ indicators: byTf(null) }),

  // MTF Bias 表 + 各周期 ST 方向
  overview: null,
  setOverview: (v) => set({ overview: v }),

  signals: [],
  setSignals: (list) => set({ signals: list || [] }),
  addSignal: (sig) =>
    set((s) => {
      const rest = s.signals.filter((x) => !(x.tf === sig.tf && x.ts === sig.ts));
      return { signals: [...rest, sig].sort((a, b) => a.ts - b.ts).slice(-300) };
    }),

  params: DEFAULT_PARAMS,
  setParams: (p) => set({ params: { ...DEFAULT_PARAMS, ...p } }),

  // 点击信号列表时让主图滚到那根 K 线（毫秒时间戳）
  focusTs: null,
  focusSignal: (tf, ts) => set({ tf, focusTs: ts }),

  // ── 自动挂单 ──
  tradeConfig: null,
  setTradeConfig: (c) => set((s) => ({ tradeConfig: { ...s.tradeConfig, ...c } })),

  // 双档结构 {normal: {...}, quick: {...}}，后端每次广播完整对象。
  // 不能浅合并 —— 嵌套档位会被旧字段污染，直接整体替换
  exitRules: null,
  setExitRules: (r) => set({ exitRules: r }),

  position: null,
  setPosition: (p) => set({ position: p }),

  closed: [],
  setClosed: (list) => set({ closed: list || [] }),

  orders: [],
  setOrders: (list) => set({ orders: list || [] }),
  addOrder: (o) => set((s) => ({ orders: [...s.orders, o].slice(-200) })),

  // 醒目弹窗（一次只弹一条，新信号覆盖旧的）
  modalSignal: null,
  showModal: (sig) => set({ modalSignal: sig }),
  closeModal: () => set({ modalSignal: null }),

  // 图表显示开关，对应 Pine 的 showsignals / highlighting / barcoloring
  opts: {
    showSignals: true,
    highlighting: true,
    barColoring: true,
    showMA: false,       // Bias 用的 fast/slow MA
    onlyCurrentTf: true, // 信号流是否只看当前周期
    sound: true,         // 信号提示音
    modal: true,         // 信号醒目弹窗
  },
  toggleOpt: (k) => set((s) => ({ opts: { ...s.opts, [k]: !s.opts[k] } })),

  toasts: [],
  pushToast: (t) => {
    const item = { ...t, id: Math.random().toString(36).slice(2) };
    set((s) => ({ toasts: [item, ...s.toasts].slice(0, 6) }));
    setTimeout(
      () => set((s) => ({ toasts: s.toasts.filter((x) => x.id !== item.id) })),
      8000,
    );
  },
}));

// 调试用：控制台可直接 __ST__.getState() 查看/构造状态，方便验证弹窗等交互
if (typeof window !== 'undefined') window.__ST__ = useStore;
