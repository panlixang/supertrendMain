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
  return {
    width: el.clientWidth,
    height: el.clientHeight,
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

  // SuperTrend 信号标记（按 4h 形态过滤结论上色）
  const markers = [...base.signals]
    .sort((a, b) => a.ts - b.ts)
    .map((s) => {
      const allow = s.decision === "allow";
      const isBuy = s.type === "buy";
      return {
        time: toT(s.ts),
        position: isBuy ? "belowBar" : "aboveBar",
        color: allow ? (isBuy ? C.up : C.down) : C.neutral,
        shape: isBuy ? "arrowUp" : "arrowDown",
        text: allow ? (isBuy ? "买" : "卖") : isBuy ? "买✕" : "卖✕",
      };
    });
  candle.setMarkers(markers);

  chart.timeScale().fitContent();
  return chart;
}

function drawH4(el, data) {
  const chart = createChart(el, chartOptions(el));
  const h4 = data.h4;
  const candles = h4.candles;
  const pattern = h4.pattern;

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

  // MA20 / MA60
  const ma20 = chart.addLineSeries({ color: C.ma20, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
  const ma60 = chart.addLineSeries({ color: C.ma60, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
  ma20.setData(
    candles.map((c, i) =>
      pattern[i]?.ma20 == null ? { time: toT(c.ts) } : { time: toT(c.ts), value: pattern[i].ma20 }
    )
  );
  ma60.setData(
    candles.map((c, i) =>
      pattern[i]?.ma60 == null ? { time: toT(c.ts) } : { time: toT(c.ts), value: pattern[i].ma60 }
    )
  );

  // 极值点标记（波峰=橙，波谷=蓝）
  const pivots = (h4.pivots || []).map((p) => ({
    time: toT(p.ts),
    position: p.type === "H" ? "aboveBar" : "belowBar",
    color: p.type === "H" ? C.ma20 : C.ma60,
    shape: "circle",
    text: p.type,
  }));
  candle.setMarkers(pivots);

  // 形态方向色带（顶部细条）：绿=上行，红=下行，灰=无明显趋势
  const pat = chart.addHistogramSeries({ priceScaleId: "pat" });
  chart.priceScale("pat").applyOptions({ scaleMargins: { top: 0, bottom: 0.82 } });
  pat.setData(
    candles.map((c, i) => {
      const d = pattern[i]?.dir;
      if (d == null) return { time: toT(c.ts), value: 0, color: C.neutral };
      return {
        time: toT(c.ts),
        value: d,
        color: d === 1 ? "#00c9a766" : d === -1 ? "#e0526366" : "#6c748633",
      };
    })
  );

  chart.timeScale().fitContent();
  return chart;
}

export default function PatternPage() {
  const [symbol, setSymbol] = useState("BTC-USDT");
  const [baseTf, setBaseTf] = useState("1h");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const mainRef = useRef(null);
  const h4Ref = useRef(null);
  const charts = useRef({ main: null, h4: null });
  const reqId = useRef(0);

  const load = async (sym, tf) => {
    setLoading(true);
    setError(null);
    const id = ++reqId.current;
    try {
      const r = await fetch(`/api/pattern?symbol=${encodeURIComponent(sym)}&base_tf=${tf}`);
      const j = await r.json();
      if (id !== reqId.current) return;
      if (j.error) {
        setError("无 " + tf + " 历史 K 线（该周期未持久化或尚未采集）");
        setData(null);
      } else {
        setData(j);
      }
    } catch (e) {
      if (id === reqId.current) setError(String(e));
    } finally {
      if (id === reqId.current) setLoading(false);
    }
  };

  useEffect(() => {
    load(symbol, baseTf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, baseTf]);

  useEffect(() => {
    if (!data || !data.base) return;
    if (charts.current.main) { charts.current.main.remove(); charts.current.main = null; }
    if (charts.current.h4) { charts.current.h4.remove(); charts.current.h4 = null; }

    const instances = [];
    if (mainRef.current) {
      charts.current.main = drawMain(mainRef.current, data);
      instances.push([mainRef.current, charts.current.main]);
    }
    if (h4Ref.current && data.h4) {
      charts.current.h4 = drawH4(h4Ref.current, data);
      instances.push([h4Ref.current, charts.current.h4]);
    }

    const ro = new ResizeObserver(() => {
      if (charts.current.main && mainRef.current)
        charts.current.main.applyOptions({ width: mainRef.current.clientWidth, height: mainRef.current.clientHeight });
      if (charts.current.h4 && h4Ref.current)
        charts.current.h4.applyOptions({ width: h4Ref.current.clientWidth, height: h4Ref.current.clientHeight });
    });
    if (mainRef.current) ro.observe(mainRef.current);
    if (h4Ref.current) ro.observe(h4Ref.current);

    return () => {
      ro.disconnect();
      if (charts.current.main) { charts.current.main.remove(); charts.current.main = null; }
      if (charts.current.h4) { charts.current.h4.remove(); charts.current.h4 = null; }
    };
  }, [data]);

  const sigs = data?.base?.signals || [];
  const allowN = sigs.filter((s) => s.decision === "allow").length;
  const blockN = sigs.length - allowN;
  const lastPat = (data?.h4?.pattern || []).filter((p) => p.dir != null).slice(-1)[0];

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minWidth: 0, color: "#e8eaed" }}>
      {/* 工具栏 */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 14px", borderBottom: "1px solid #1e1e1e", flexWrap: "wrap" }}>
        <span style={{ fontSize: 14, fontWeight: 700 }}>形态识别</span>
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
        <span style={{ fontSize: 12, color: "#5a6270" }}>形态识别周期：4h（固定）</span>
        <button onClick={() => load(symbol, baseTf)} style={btn}>刷新</button>
        {loading && <span style={{ fontSize: 12, color: "#8b93a0" }}>加载中…</span>}
        {error && <span style={{ fontSize: 12, color: C.down }}>{error}</span>}
      </div>

      {/* 图例 / 统计 */}
      <div style={{ display: "flex", alignItems: "center", gap: 18, padding: "6px 14px", fontSize: 12, color: "#8b93a0", borderBottom: "1px solid #1e1e1e", flexWrap: "wrap" }}>
        <span><i style={dot(C.up)} /> 上行 / 允许买</span>
        <span><i style={dot(C.down)} /> 下行 / 允许卖</span>
        <span><i style={dot(C.neutral)} /> 4h 反向→不下单（无趋势照常下单）</span>
        <span style={{ marginLeft: "auto" }}>
          信号 {sigs.length} 笔 · <b style={{ color: C.up }}>允许 {allowN}</b> · <b style={{ color: C.neutral }}>不下单 {blockN}</b>
        </span>
        {lastPat && (
          <span>
            最新 4h 形态：
            <b style={{ color: lastPat.dir === 1 ? C.up : lastPat.dir === -1 ? C.down : C.neutral }}>
              {lastPat.label}
            </b>
          </span>
        )}
      </div>

      {/* 主图 */}
      <div style={{ flex: 1.5, minHeight: 0, position: "relative" }}>
        <div style={tag("主图 · 原始 SuperTrend 信号 10/3.0（" + baseTf + "）")} />
        {data?.base ? <div ref={mainRef} style={{ position: "absolute", inset: 0, padding: 6 }} /> : (
          <Empty err={error} />
        )}
      </div>

      {/* 4h 副图 */}
      <div style={{ flex: 1, minHeight: 0, borderTop: "1px solid #1e1e1e", position: "relative" }}>
        <div style={tag("4h · 趋势形态识别（MA20/MA60 · 极值点 · 方向色带）")} />
        {data?.h4 ? <div ref={h4Ref} style={{ position: "absolute", inset: 0, padding: 6 }} /> : (
          <Empty err={error} />
        )}
      </div>
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
const Empty = ({ err }) => (
  <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", color: "#5a6270", fontSize: 13 }}>
    {err ? "暂无数据" : "加载中…"}
  </div>
);
