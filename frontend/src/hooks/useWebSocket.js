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
  const retryTimer = useRef(null);
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
      // snapshot 只带近期几百根；15m/1h/4h/1d 展示窗口更长，这里拉满内存 deque
      try {
        const r = await fetch(`${API}/api/candles?tf=${tf}`);
        const d = await r.json();
        if (!dead && Array.isArray(d) && d.length) {
          useStore.getState().setCandles(tf, d);
        }
      } catch {}
    }

    // 长窗口周期优先拉满，短周期稍后，避免首屏卡顿
    const LONG_TFS = new Set(['15m', '1h', '4h', '1d']);
    async function fetchCandlesPreferLong() {
      const long = ALL_TFS.filter((tf) => LONG_TFS.has(tf));
      const short = ALL_TFS.filter((tf) => !LONG_TFS.has(tf));
      await Promise.all(long.map((tf) => fetchFullCandles(tf)));
      short.forEach((tf) => fetchFullCandles(tf));
    }

    // 后端缓冲可能晚于首帧才灌满，隔几秒对仍为空的周期补拉（最多重试 5 次）
    let retryCount = 0;
    function retryAfterSnapshot() {
      if (dead || retryCount >= 5) return;
      retryCount += 1;
      retryTimer.current = setTimeout(async () => {
        if (dead) return;
        const cur = useStore.getState().candles;
        const empty = ALL_TFS.filter((tf) => !(cur[tf] || []).length);
        if (!empty.length) return;
        await Promise.all(empty.map((tf) => fetchFullCandles(tf)));
        empty.forEach((tf) => fetchIndicators(tf, true));
        retryAfterSnapshot();
      }, 5000);
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
          fetchCandlesPreferLong().then(() => {
            ALL_TFS.forEach((tf) => fetchIndicators(tf, true));
            // 后端刚启动 / 刚换品种时 K 线缓冲可能还没灌满，晚几秒对仍为空的周期补拉一次
            retryAfterSnapshot();
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
          // s 是 handle 开始时的状态快照，这里用它判断「拉全量之前是否为空」
          const wasEmpty = !(s.candles[msg.tf] || []).length;
          s.upsertCandle(msg.tf, msg.data);
          if (wasEmpty) {
            // 之前没拉到（后端缓冲空），现在有实时推送了，重新拉一次全量并刷新指标
            fetchFullCandles(msg.tf).then(() => fetchIndicators(msg.tf, true));
          } else {
            fetchIndicators(msg.tf);
          }
          break;
        case 'trade_config':
          s.setTradeConfig(msg.data);
          // ER 参数可能改变了 hidden 字段，重新拉取各周期指标让图表刷新
          s.clearIndicators();
          ALL_TFS.forEach((tf) => fetchIndicators(tf, true));
          break;
        case 'exit_rules':
          s.setExitRules(msg.data);
          break;
        case 'symbols':
          s.setSymbolCfgs(msg.data);
          if (msg.params && isChart) s.setParams(msg.params);
          if (msg.signals && isChart) s.setSignals(msg.signals);
          // 闸门/指标参数变更后重新拉取图表品种的指标，让箭头 / ❌ / hidden 生效
          if (isChart) {
            s.clearIndicators();
            ALL_TFS.forEach((tf) => fetchIndicators(tf, true));
          }
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
        let msg;
        try {
          msg = JSON.parse(e.data);
        } catch (err) {
          console.error("[ws] 消息不是合法 JSON", err);
          return;
        }
        try {
          handle(msg);
        } catch (err) {
          console.error(`[ws] 处理 ${msg.type} 消息出错`, err);
        }
      };
    }

    connect();
    // Bias 表按 30s 兜底刷新（高周期不会频繁收盘，但 MA 值一直在动）
    const iv = setInterval(fetchOverview, 30000);
    return () => {
      dead = true;
      clearInterval(iv);
      clearTimeout(timer.current);
      clearTimeout(retryTimer.current);
      wsRef.current?.close();
    };
  }, []);
}
