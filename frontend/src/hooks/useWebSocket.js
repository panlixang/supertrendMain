import { useEffect, useRef } from 'react';
import { ALL_TFS, useStore } from '../stores/useStore';
import { API } from '../utils/format';

const WS_URL = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`;
const RECONNECT_MS = 3000;

// 指标重算节流：短周期刷得勤，长周期没必要
const THROTTLE = {
  '1m': 4000, '5m': 8000, '15m': 12000, '30m': 20000,
  '1h': 30000, '4h': 60000, '1d': 120000, '1w': 300000, '1M': 600000,
};
const lastFetch = {};

export function useWebSocket() {
  const timer = useRef(null);
  const wsRef = useRef(null);

  useEffect(() => {
    let dead = false;

    async function fetchIndicators(tf, force = false) {
      const now = Date.now();
      if (!force && now - (lastFetch[tf] || 0) < (THROTTLE[tf] ?? 15000)) return;
      lastFetch[tf] = now;
      try {
        const r = await fetch(`${API}/api/indicators?tf=${tf}`);
        const d = await r.json();
        if (!dead && d && d.st) useStore.getState().setIndicators(tf, d);
      } catch {}
    }

    async function fetchOverview() {
      try {
        const r = await fetch(`${API}/api/overview`);
        const d = await r.json();
        if (!dead) useStore.getState().setOverview(d);
      } catch {}
    }

    async function fetchFullCandles(tf) {
      // snapshot 每周期只带 500 根，指标是按完整 deque 算的，
      // 这里补齐长度，否则末尾对齐后前段会缺一截
      try {
        const r = await fetch(`${API}/api/candles?tf=${tf}`);
        const d = await r.json();
        if (!dead && Array.isArray(d) && d.length) {
          useStore.getState().setCandles(tf, d);
        }
      } catch {}
    }

    function handle(msg) {
      const s = useStore.getState();
      const isChart = !msg.symbol || msg.symbol === s.symbol;   // 是否图表当前品种

      switch (msg.type) {
        case 'snapshot': {
          if (msg.symbol && msg.symbol !== s.symbol) s.setSymbol(msg.symbol);
          if (msg.ticker) s.setTicker(msg.ticker);
          if (msg.params) s.setParams(msg.params);
          if (msg.trade_config) s.setTradeConfig(msg.trade_config);
          if (msg.exit_rules) s.setExitRules(msg.exit_rules);
          if (msg.orders) s.setOrders(msg.orders);
          if (msg.closed) s.setClosed(msg.closed);
          s.setPosition(msg.position || null);
          if (msg.tickers) s.setTickers(msg.tickers);
          if (msg.positions) s.setPositions(msg.positions);
          if (msg.symbols) s.setSymbolCfgs(msg.symbols);
          if (msg.max_symbols) s.setMaxSymbols(msg.max_symbols);
          if (msg.candles) {
            Object.entries(msg.candles).forEach(([tf, d]) => s.setCandles(tf, d));
          }
          s.setSignals(msg.signals || []);
          s.clearIndicators();
          ALL_TFS.forEach((tf) => {
            fetchFullCandles(tf);
            fetchIndicators(tf, true);
          });
          fetchOverview();
          break;
        }
        case 'ticker':
          if (msg.symbol) s.setTickerFor(msg.symbol, msg.data);
          if (isChart) s.setTicker(msg.data);
          break;
        case 'candle':
          if (!isChart) break;              // 图表只画当前品种
          s.upsertCandle(msg.tf, msg.data);
          fetchIndicators(msg.tf);
          break;
        case 'trade_config':
          s.setTradeConfig(msg.data);
          break;
        case 'exit_rules':
          s.setExitRules(msg.data);
          break;
        case 'symbols':
          s.setSymbolCfgs(msg.data);
          break;
        case 'position':
          if (msg.symbol) s.setPositionFor(msg.symbol, msg.data);
          if (isChart) s.setPosition(msg.data);
          // 平仓后刷一次全品种持仓 + 已平仓列表
          if (!msg.data) {
            fetch(`${API}/api/trade/positions`)
              .then((r) => r.json())
              .then((d) => {
                s.setPositions(d.positions || {});
                s.setClosed(d.closed || []);
              })
              .catch(() => {});
          }
          break;
        case 'order':
          s.addOrder({ ...msg.data, sym: msg.data.sym || msg.symbol });
          // 挂单结果补进弹窗，让「已挂单/失败」直接显示在同一个弹窗里
          if (s.modalSignal && s.modalSignal.ts === msg.data.sig_ts) {
            s.showModal({ ...s.modalSignal, order: msg.data });
          }
          break;
        case 'signal': {
          const sig = { ...msg.data, symbol: msg.symbol };
          if (isChart) s.addSignal(sig);      // 信号列表跟着图表品种走
          if (s.opts.modal) s.showModal(sig); // 弹窗/提示所有品种都出，带品种名
          s.pushToast({
            type: sig.type,
            tf: sig.tf,
            grade: sig.grade,
            price: sig.price,
            label: `${msg.symbol || ''} ${sig.type === 'buy' ? 'BUY 超趋翻多' : 'SELL 超趋翻空'}`,
          });
          if (isChart) {
            fetchIndicators(sig.tf, true);
            fetchOverview();
          }
          break;
        }
        default:
          break;
      }
    }

    function connect() {
      if (dead) return;
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;
      ws.onopen = () => useStore.getState().setConnected(true);
      ws.onclose = () => {
        useStore.getState().setConnected(false);
        if (!dead) timer.current = setTimeout(connect, RECONNECT_MS);
      };
      ws.onerror = () => ws.close();
      ws.onmessage = (e) => {
        try {
          handle(JSON.parse(e.data));
        } catch {}
      };
    }

    connect();
    // Bias 表按 30s 兜底刷新（高周期不会频繁收盘，但 MA 值一直在动）
    const iv = setInterval(fetchOverview, 30000);
    return () => {
      dead = true;
      clearInterval(iv);
      clearTimeout(timer.current);
      wsRef.current?.close();
    };
  }, []);
}
