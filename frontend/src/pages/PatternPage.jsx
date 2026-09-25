/**
 * 形态识别页（独立新页面，不依赖信号终端/研究页的任何组件或状态）。
 *
 * 主图：基础周期 K 线 + 原始 SuperTrend 信号（ATR 周期10 / factor 3.0，对应 supertrend原始代码.md）作为基础信号。
 * 注意：本页仅使用「原始信号」参数，与首页默认（periods=15, multiplier=9.1）无关。
 * 当 SuperTrend 翻转（出信号）时，用 4h K 线做趋势形态识别：
 *   - 4h 形态方向与信号同向 → 允许下单（绿/红箭头）
 *   - 4h 形态方向相反 → 不下单（灰色 ✕ 箭头）
 *   - 4h 判定「无明显趋势(dir=0)」→ 正常下单（2026-09 起去掉该拦截，回测验证可提收益并降回撤）
 * 副图：4h K 线 + MA20/MA60 + 极值点 + 形态方向色带。
 */
import { useEffect, useRef, useState } from "react";
import { createChart, CrosshairMode } from "lightweight-charts";
import PatternTradePanel from '../components/PatternTradePanel';

const C = {
  bg: "transparent",
  text: "#6c7480",
  grid: "#1a1a1a",
  bull: "#00c9a7",
  bear: "#e05263",
  up: "#00c9a7",
  down: "#e05263",
  ma20: "#f5a623",
  ma60: "#4e8aff",
  neutral: "#6c7480",
  border: "#262626",
};

// OKX 毫秒 → 图表秒时间戳，+8h 让坐标轴直接显示北京时间（与 CandleChart 一致）
const toT = (ms) => Math.floor(ms / 1000) + 8 * 3600;

const pad = (n) => String(n).padStart(2, "0");
const fmtBJ = (t) => {
  const d = new Date(t * 1000);
  return `${d.getUTCFullYear()}/${d.getUTCMonth() + 1}/${d.getUTCDate()} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
};
const fmtBJDate = (t) => {
  const d = new Date(t * 1000);
  return `${d.getUTCFullYear()}/${d.getUTCMonth() + 1}/${d.getUTCDate()}`;
};

const BASE_TFS = ["15m", "1h", "4h", "1d"];

function chartOptions(el) {
  // 首帧容器可能还没布局完（clientWidth/Height 为 0），直接按 0 建图会导致整张图空白。
  // 这里退一步用父容器尺寸起步，之后由 ResizeObserver 校正到真实尺寸。
  return {
    width: el.clientWidth || el.parentElement?.clientWidth || 800,
    height: el.clientHeight || el.parentElement?.clientHeight || 320,
    layout: { background: { color: C.bg }, textColor: C.text, fontSize: 11 },
    grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
    crosshair: {
      mode: CrosshairMode.Normal,
      vertLine: { color: "#00c9a755", labelBackgroundColor: "#00c9a7" },
      horzLine: { color: "#00c9a755", labelBackgroundColor: "#00c9a7" },
    },
    rightPriceScale: { borderColor: C.border, scaleMargins: { top: 0.08, bottom: 0.24 } },
    timeScale: { borderColor: C.border, timeVisible: true, secondsVisible: false },
    localization: { timeFormatter: fmtBJ, dateFormatter: fmtBJDate },
  };
}

function drawMain(el, data) {
  const chart = createChart(el, chartOptions(el));
  const base = data.base;
  const candles = base.candles;

  const candle = chart.addCandlestickSeries({
    upColor: C.up, downColor: C.down,
    borderUpColor: C.up, borderDownColor: C.down,
    wickUpColor: C.up, wickDownColor: C.down,
    priceLineColor: "#ffffff44",
  });
  candle.setData(
    candles.map((c) => ({
      time: toT(c.ts), open: c.o, high: c.h, low: c.l, close: c.c,
    }))
  );

  // 成交量（副图）
  const vol = chart.addHistogramSeries({ priceScaleId: "vol" });
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.86, bottom: 0 } });
  vol.setData(
    candles.map((c) => ({
      time: toT(c.ts),
      value: c.vol || 0,
      color: c.c >= c.o ? "#00c9a733" : "#e0526333",
    }))
  );

  // SuperTrend 双轨（断线：非当前趋势一侧用 whitespace）
  const up = chart.addLineSeries({ color: C.up, lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
  const dn = chart.addLineSeries({ color: C.down, lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
  const times = candles.map((c) => toT(c.ts));
  up.setData(
    candles.map((c, i) =>
      base.st.up_plot[i] == null ? { time: times[i] } : { time: times[i], value: base.st.up_plot[i] }
    )
  );
  dn.setData(
    candles.map((c, i) =>
      base.st.dn_plot[i] == null ? { time: times[i] } : { time: times[i], value: base.st.dn_plot[i] }
    )
  );

  // 当前策略标记：入场(ST翻转箭头) + 出场(v4-exit 圆点，按盈亏上色，标注 SL/TP/ST)
  const mk = [...base.signals].flatMap((s) => {
    const isBuy = s.type === "buy";
    const entry = {
      time: toT(s.ts),
      position: isBuy ? "belowBar" : "aboveBar",
      color: isBuy ? C.up : C.down,
      shape: isBuy ? "arrowUp" : "arrowDown",
      text: isBuy ? "买" : "卖",
    };
    const win = s.pnl > 0;
    const exit = {
      time: toT(s.exit_ts),
      position: isBuy ? "aboveBar" : "belowBar",
      color: win ? C.up : C.down,
      shape: "circle",
      text: (s.exit_type || "").toUpperCase(),
    };
    return [entry, exit];
  });
  mk.sort((a, b) => a.time - b.time);
  candle.setMarkers(mk);

  chart.timeScale().fitContent();
  return chart;
}

export default function PatternPage() {
  const [symbol, setSymbol] = useState("BTC-USDT");
  const [baseTf, setBaseTf] = useState("1h");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [filterD, setFilterD] = useState(false);

  const mainRef = useRef(null);
  const charts = useRef({ main: null });
  const reqId = useRef(0);

  const load = async (sym, tf) => {
    setLoading(true);
    setError(null);
    const id = ++reqId.current;
    try {
      const r = await fetch(`/api/pattern?symbol=${encodeURIComponent(sym)}&base_tf=${tf}${filterD ? "&filter_d=1" : ""}`);
      const j = await r.json().catch(() => null);
      if (id !== reqId.current) return;
      if (!r.ok || !j) {
        // 最常见：后端没启动 / 8000 端口被别的服务占了 → 这里拿到的是 404 的 {"detail":...}
        setError(`接口不可用（HTTP ${r.status}）— 请确认本项目后端已启动且未被其它服务占用 8000`);
        setData(null);
        return;
      }
      if (j.error || !j.base?.candles?.length) {
        setError(
          j.error === "no_base_candles"
            ? `无 ${tf} 历史 K 线（该周期未持久化或尚未采集）`
            : `${sym} / ${tf} 暂无数据`
        );
        setData(null);
        return;
      }
      setData(j);
    } catch (e) {
      if (id === reqId.current) { setError("请求失败：" + String(e)); setData(null); }
    } finally {
      if (id === reqId.current) setLoading(false);
    }
  };

  useEffect(() => {
    load(symbol, baseTf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, baseTf, filterD]);

  useEffect(() => {
    if (!data || !data.base) return;
    if (charts.current.main) { charts.current.main.remove(); charts.current.main = null; }

    if (mainRef.current) {
      charts.current.main = drawMain(mainRef.current, data);
    }

    const ro = new ResizeObserver(() => {
      if (charts.current.main && mainRef.current)
        charts.current.main.applyOptions({ width: mainRef.current.clientWidth, height: mainRef.current.clientHeight });
    });
    if (mainRef.current) ro.observe(mainRef.current);

    return () => {
      ro.disconnect();
      if (charts.current.main) { charts.current.main.remove(); charts.current.main = null; }
    };
  }, [data]);

  const sigs = data?.base?.signals || [];
  const stats = data?.base?.stats || { n: 0 };

  return (
    <div style={{ display: "flex", height: "100%", minWidth: 0, color: "#e8eaed" }}>
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
      {/* 工具栏 */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 14px", borderBottom: "1px solid #1e1e1e", flexWrap: "wrap" }}>
        <span style={{ fontSize: 14, fontWeight: 700 }}>当前策略 · ST翻转 + v4-exit</span>
        <label style={{ fontSize: 12, color: "#8b93a0" }}>
          品种
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.trim().toUpperCase())}
            onKeyDown={(e) => e.key === "Enter" && load(symbol, baseTf)}
            style={inp}
          />
        </label>
        <label style={{ fontSize: 12, color: "#8b93a0" }}>
          主图周期
          <select value={baseTf} onChange={(e) => setBaseTf(e.target.value)} style={sel}>
            {BASE_TFS.map((t) => (<option key={t} value={t}>{t}</option>))}
          </select>
        </label>
        <span style={{ fontSize: 12, color: "#5a6270" }}>出场：SL 1.5×ATR / TP 2×ATR 半仓 / ST 尾随</span>
        <label style={{ fontSize: 12, color: "#8b93a0", display: "flex", alignItems: "center", gap: 4 }}>
          <input type="checkbox" checked={filterD} onChange={(e) => setFilterD(e.target.checked)} style={{ accentColor: "#00c9a7" }} />
          D 评分过滤(评分≤60)
        </label>
        <button onClick={() => load(symbol, baseTf)} style={btn}>刷新</button>
        {loading && <span style={{ fontSize: 12, color: "#8b93a0" }}>加载中…</span>}
        {error && <span style={{ fontSize: 12, color: C.down }}>{error}</span>}
      </div>

      {/* 图例 / 策略统计（与回测同口径） */}
      <div style={{ display: "flex", alignItems: "center", gap: 18, padding: "6px 14px", fontSize: 12, color: "#8b93a0", borderBottom: "1px solid #1e1e1e", flexWrap: "wrap" }}>
        <span><i style={dot(C.up)} /> 买 / 盈利出场</span>
        <span><i style={dot(C.down)} /> 卖 / 亏损出场</span>
        <span><i style={dot(C.neutral)} /> 圆点=出场(SL/TP/ST)</span>
        <span style={{ color: filterD ? C.up : "#5a6270" }}>入场过滤：{filterD ? "D(评分≤60)" : "不过滤(全ST)"}</span>
        <span style={{ marginLeft: "auto" }}>
          信号 <b style={{ color: "#e8eaed" }}>{stats.n}</b> 笔 · 胜率 <b style={{ color: C.up }}>{stats.win_rate ?? "-"}%</b>
          · 均盈 <b style={{ color: (stats.avg_pnl ?? 0) >= 0 ? C.up : C.down }}>{(stats.avg_pnl ?? 0) > 0 ? "+" : ""}{stats.avg_pnl ?? "-"}%</b>
        </span>
        <span>盈亏比 <b style={{ color: "#e8eaed" }}>{stats.pl_ratio ?? "-"}</b></span>
        <span>PF <b style={{ color: "#e8eaed" }}>{stats.pf ?? "-"}</b></span>
        <span>t <b style={{ color: (stats.t ?? 0) >= 2 ? C.up : C.down }}>{stats.t ?? "-"}</b></span>
      </div>

      {/* 主图（基础周期） */}
      <div style={{ flex: 1, minHeight: 0, position: "relative" }}>
        <div style={tag("主图 · SuperTrend 翻转信号 10/3.0 + v4-exit（" + baseTf + "）")} />
        {data?.base?.candles?.length ? <div ref={mainRef} style={{ position: "absolute", inset: 0, padding: 6 }} /> : (
          <Empty err={error} loading={loading} />
        )}
      </div>
      </div>
      {/* 右侧：形态识别页自己的下单面板（与首页不复用同一套配置）*/}
      <PatternTradePanel currentSymbol={symbol} />
    </div>
  );
}

const inp = {
  marginLeft: 6, background: "#111", color: "#e8eaed", border: "1px solid #262626",
  borderRadius: 6, padding: "5px 8px", fontSize: 12, width: 110, fontFamily: "var(--font-mono)",
};
const sel = {
  marginLeft: 6, background: "#111", color: "#e8eaed", border: "1px solid #262626",
  borderRadius: 6, padding: "5px 8px", fontSize: 12,
};
const btn = {
  background: "#00c9a7", color: "#000", border: "none", borderRadius: 6,
  padding: "6px 14px", fontSize: 12, fontWeight: 700, cursor: "pointer",
};
const dot = (color) => ({
  display: "inline-block", width: 9, height: 9, borderRadius: "50%", background: color, marginRight: 5,
});
const tag = (text) => ({
  position: "absolute", top: 10, left: 14, zIndex: 2, fontSize: 10.5, fontWeight: 700,
  color: "#8b93a0", letterSpacing: 0.3, pointerEvents: "none",
});
const Empty = ({ err, loading }) => (
  <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", color: "#5a6270", fontSize: 13, padding: 16, textAlign: "center" }}>
    {loading ? "加载中…" : err || "暂无数据"}
  </div>
);
