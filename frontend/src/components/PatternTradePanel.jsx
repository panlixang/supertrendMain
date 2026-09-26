/**
 * 形态识别页 · 自动下单面板（右侧）
 *
 * 与首页 TradePanel 完全独立两套：读 /api/pattern/trade/*，
 * 配置存 pattern_trade.json、凭据存 pattern_credentials.json，互不干扰。
 *
 * 面板只暴露用户要的参数：
 *   交易所配置（OKX / Bitget + 各自 Key + 模拟盘）
 *   是否开启 4h 方向拦截
 *   每个品种的 4 条过滤规则（连续翻转 / 波动异常 / 箱体错误位置 / 极端K）+ 止盈止损
 * 出场规则固定用回测验证档（TP1 1.5% 平 70% + 保本 + 跟随 SuperTrend 跟踪）。
 */
import { useCallback, useEffect, useState } from "react";

const C = {
  up: "#00c9a7",
  down: "#e05263",
  neutral: "#8b93a0",
  dim: "#5a6270",
  border: "#262626",
  text: "#e8eaed",
};

const SZ = {
  panel: {
    width: 320, flexShrink: 0, borderLeft: "1px solid #1e1e1e",
    display: "flex", flexDirection: "column", overflowY: "auto",
    background: "#0b0b0c", color: C.text, fontSize: 12,
  },
  sec: { padding: "10px 12px", borderBottom: `1px solid ${C.border}` },
  h: { fontSize: 12, fontWeight: 700, marginBottom: 8, color: "#c8ccd4" },
  row: { display: "flex", alignItems: "center", gap: 6, marginBottom: 6 },
  inp: {
    flex: 1, minWidth: 0, background: "#111", color: C.text, border: `1px solid ${C.border}`,
    borderRadius: 6, padding: "5px 7px", fontSize: 12, fontFamily: "var(--font-mono)",
  },
  sel: {
    background: "#111", color: C.text, border: `1px solid ${C.border}`,
    borderRadius: 6, padding: "4px 6px", fontSize: 12,
  },
  btn: {
    background: C.up, color: "#000", border: "none", borderRadius: 6,
    padding: "5px 10px", fontSize: 11, fontWeight: 700, cursor: "pointer",
  },
  btnGhost: {
    background: "transparent", color: C.neutral, border: `1px solid ${C.border}`,
    borderRadius: 6, padding: "4px 8px", fontSize: 11, cursor: "pointer",
  },
  tag: {
    display: "inline-block", padding: "1px 6px", borderRadius: 4,
    border: `1px solid ${C.border}`, color: C.neutral, fontSize: 10.5,
  },
};


const getJSON = async (url, opts) => {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
};
const postJSON = (url, body) =>
  getJSON(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

export default function PatternTradePanel({ currentSymbol }) {
  const [cfg, setCfg] = useState(null);
  const [keys, setKeys] = useState(null);
  const [symbols, setSymbols] = useState([]);
  const [state, setState] = useState(null);
  const [err, setErr] = useState("");
  const [ping, setPing] = useState(null);
  const [testRes, setTestRes] = useState(null);
  const [xr, setXr] = useState({
    tp1_pct: 1.5, tp1_ratio: 70, sl_pct: 2.0,
    move_sl_to_entry: true, trail_with_st: true,
  });

  const [keyForm, setKeyForm] = useState({ api_key: "", api_secret: "", passphrase: "" });
  const [addForm, setAddForm] = useState({ symbol: "", margin: 10, leverage: 3, allow: ["1h"] });

  const load = useCallback(async () => {
    try {
      const d = await getJSON("/api/pattern/trade/config");
      setCfg(d.cfg);
      setKeys(d.keys);
      setSymbols(d.symbols || []);
      setErr("");
    } catch (e) {
      setErr(String(e.message || e));
    }
  }, []);

  // 状态（持仓 / 订单 / 日志）轮询
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const d = await getJSON("/api/pattern/trade/state");
        if (alive) setState(d);
      } catch {
        /* 忽略轮询错误 */
      }
    };
    poll();
    const t = setInterval(poll, 5000);
    load();
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [load]);

  // 页面品种变化时，顺手填进「添加品种」输入框
  useEffect(() => {
    if (currentSymbol) setAddForm((f) => ({ ...f, symbol: currentSymbol }));
  }, [currentSymbol]);

  const patch = async (body) => {
    try {
      const d = await postJSON("/api/pattern/trade/config", body);
      setCfg(d.cfg);
      setKeys(d.keys);
    } catch (e) {
      setErr(String(e.message || e));
    }
  };

  const saveKeys = async () => {
    try {
      const d = await postJSON("/api/pattern/trade/keys", {
        exchange_id: cfg.exchange, ...keyForm,
      });
      setKeys(d);
      setKeyForm({ api_key: "", api_secret: "", passphrase: "" });
    } catch (e) {
      setErr(String(e.message || e));
    }
  };

  const addSymbol = async () => {
    if (!addForm.symbol) return;
    try {
      const d = await postJSON("/api/pattern/trade/symbols", {
        action: "add",
        symbol: addForm.symbol,
        margin_usdt: Number(addForm.margin) || 10,
        leverage: Number(addForm.leverage) || 3,
        allow_tfs: addForm.allow,
        enabled: false, // 默认不启用，避免加进来就自动下单
      });
      setSymbols(d.symbols);
    } catch (e) {
      setErr(String(e.message || e));
    }
  };

  const updateSymbol = async (symbol, body) => {
    try {
      const d = await postJSON("/api/pattern/trade/symbols", { action: "update", symbol, ...body });
      setSymbols(d.symbols);
    } catch (e) {
      setErr(String(e.message || e));
    }
  };

  // 品种独立数值输入：失焦即下发。留空=回落全局默认（后端 None 语义）。
  const tpInput = (sym, row, key, fallback, step = 0.1, width = 60) => (
    <input
      style={{ ...SZ.inp, width }}
      type="number"
      step={step}
      defaultValue={row?.[key] ?? fallback}
      onBlur={(e) => {
        const v = e.target.value.trim();
        if (v !== "") updateSymbol(sym, { [key]: Number(v) });
      }}
    />
  );

  const removeSymbol = async (symbol) => {
    try {
      const d = await postJSON("/api/pattern/trade/symbols", { action: "remove", symbol });
      setSymbols(d.symbols);
    } catch (e) {
      setErr(String(e.message || e));
    }
  };

  const closePos = async (symbol) => {
    await postJSON("/api/pattern/trade/close", { action: "update", symbol }).catch(() => {});
    load();
  };

  const toggleTf = (tf) =>
    setAddForm((f) => ({
      ...f,
      allow: f.allow.includes(tf) ? f.allow.filter((x) => x !== tf) : [...f.allow, tf],
    }));

  // 止盈止损配置（从后端 cfg 同步，改动即时下发）
  useEffect(() => {
    if (!cfg) return;
    setXr({
      tp1_pct: cfg.tp1_pct ?? 1.5,
      tp1_ratio: cfg.tp1_ratio ?? 70,
      sl_pct: cfg.sl_pct ?? 2.0,
      move_sl_to_entry: cfg.move_sl_to_entry ?? true,
      trail_with_st: cfg.trail_with_st ?? true,
    });
  }, [cfg]);

  const patchXr = (k, v) => {
    setXr((x) => ({ ...x, [k]: v }));
    patch({ [k]: v });
  };

  const doPing = async () => {
    setPing({ loading: true });
    try {
      const d = await getJSON("/api/pattern/trade/ping");
      setPing(d);
    } catch (e) {
      setPing({ ok: false, error: String(e.message || e) });
    }
  };

  const testOrder = async () => {
    if (!currentSymbol) {
      setErr("请先在左侧图表选择品种，再点测试单");
      return;
    }
    try {
      const d = await postJSON("/api/pattern/trade/test-order", { symbol: currentSymbol });
      setTestRes(d);
      setErr("");
    } catch (e) {
      setTestRes({ ok: false, error: String(e.message || e) });
    }
  };

  if (!cfg) return <div style={SZ.panel}><div style={SZ.sec}>加载中…</div></div>;

  const fmtPnl = (p) => {
    if (!p) return null;
    const pct = p.pnl_pct ?? 0;
    return (
      <span style={{ color: pct >= 0 ? C.up : C.down }}>
        {p.side === "long" ? "多" : "空"} {p.entry} → {p.pnl_pct?.toFixed(2)}%
      </span>
    );
  };

  return (
    <div style={SZ.panel}>
      {/* 交易所 */}
      <div style={SZ.sec}>
        <div style={SZ.h}>交易所配置</div>
        <div style={SZ.row}>
          <select style={SZ.sel} value={cfg.exchange} onChange={(e) => patch({ exchange: e.target.value })}>
            {keys.exchanges.map((x) => (<option key={x} value={x}>{x.toUpperCase()}</option>))}
          </select>
          <span style={SZ.tag}>{cfg.paper ? "模拟盘" : "⚠️ 实盘"}</span>
        </div>
        <div style={SZ.row}>
          <input
            style={SZ.inp} placeholder="API Key" value={keyForm.api_key}
            onChange={(e) => setKeyForm((f) => ({ ...f, api_key: e.target.value }))}
          />
        </div>
        <div style={SZ.row}>
          <input
            style={SZ.inp} placeholder="Secret" type="password" value={keyForm.api_secret}
            onChange={(e) => setKeyForm((f) => ({ ...f, api_secret: e.target.value }))}
          />
        </div>
        <div style={SZ.row}>
          <input
            style={SZ.inp} placeholder="Passphrase" type="password" value={keyForm.passphrase}
            onChange={(e) => setKeyForm((f) => ({ ...f, passphrase: e.target.value }))}
          />
        </div>
        <div style={SZ.row}>
          <button style={SZ.btn} onClick={saveKeys}>保存凭据</button>
          <span style={{ fontSize: 11, color: keys.configured ? C.up : C.dim }}>
            {keys.configured ? `已配置 ${keys.key_hint}` : "未配置"}
          </span>
        </div>
        <div style={{ fontSize: 10.5, color: C.dim, marginTop: 4 }}>
          凭据存 pattern_credentials.json，与首页独立，可用不同子账户
        </div>
        <div style={SZ.row}>
          <button style={SZ.btn} onClick={doPing}>查账户</button>
          <button style={SZ.btnGhost} onClick={testOrder}>测试单</button>
        </div>
        {ping && (
          <div style={{ fontSize: 10.5, color: ping.ok ? C.up : C.down, marginTop: 4 }}>
            {ping.loading ? "查询中…" : ping.ok
              ? `✅ ${(ping.paper ? "模拟盘" : "⚠️ 实盘")} · 权益 ${ping.equity ?? "—"}U`
              : `❌ ${ping.error}`}
          </div>
        )}
        {testRes && (
          <div style={{ fontSize: 10.5, color: testRes.ok ? C.up : C.down, marginTop: 4 }}>
            {testRes.ok
              ? "✅ 测试单已挂（盘口 -3%，正常不成交，可撤单）"
              : `❌ ${testRes.error}`}
          </div>
        )}
      </div>

      {/* 开关 */}
      <div style={SZ.sec}>
        <div style={SZ.h}>下单开关</div>
        <div style={SZ.row}>
          <input type="checkbox" checked={cfg.enabled} onChange={(e) => patch({ enabled: e.target.checked })} />
          <span>自动下单总开关</span>
        </div>
        <div style={SZ.row}>
          <input type="checkbox" checked={cfg.paper} onChange={(e) => patch({ paper: e.target.checked })} />
          <span style={{ color: cfg.paper ? C.text : C.down }}>模拟盘（取消勾选 = 真实下单）</span>
        </div>
        <div style={SZ.row}>
          <input type="checkbox" checked={cfg.block_4h} onChange={(e) => patch({ block_4h: e.target.checked })} />
          <span>开启 4h 方向拦截（仅拦 4h 明确反向）</span>
        </div>
        </div>

      {/* 默认出场 · 止盈止损 */}
      <div style={SZ.sec}>
        <div style={SZ.h}>默认止盈止损（新品种沿用）</div>
        <div style={{ fontSize: 10.5, color: C.dim, marginBottom: 6 }}>
          全局默认值，新加品种未单独配置时沿用；每个品种可在下方「下单品种」里单独改
        </div>
        <div style={SZ.row}>
          <span style={{ color: C.neutral, width: 64 }}>TP1 幅度</span>
          <input style={{ ...SZ.inp, width: 64 }} type="number" step={0.1}
                 value={xr.tp1_pct}
                 onChange={(e) => patchXr("tp1_pct", Number(e.target.value))} />
          <span style={{ color: C.neutral }}>%</span>
          <span style={{ color: C.neutral, width: 40, textAlign: "right" }}>比例</span>
          <input style={{ ...SZ.inp, width: 56 }} type="number" step={1}
                 value={xr.tp1_ratio}
                 onChange={(e) => patchXr("tp1_ratio", Number(e.target.value))} />
          <span style={{ color: C.neutral }}>%</span>
        </div>
        <div style={SZ.row}>
          <span style={{ color: C.neutral, width: 64 }}>硬止损</span>
          <input style={{ ...SZ.inp, width: 64 }} type="number" step={0.1}
                 value={xr.sl_pct}
                 onChange={(e) => patchXr("sl_pct", Number(e.target.value))} />
          <span style={{ color: C.neutral }}>%（轨道无效兜底）</span>
        </div>
        <div style={SZ.row}>
          <label style={{ fontSize: 11, color: C.text, display: "flex", alignItems: "center", gap: 4 }}>
            <input type="checkbox" checked={xr.move_sl_to_entry}
                   onChange={(e) => patchXr("move_sl_to_entry", e.target.checked)} />
            保本（止盈后止损移到开仓价）
          </label>
        </div>
        <div style={SZ.row}>
          <label style={{ fontSize: 11, color: C.text, display: "flex", alignItems: "center", gap: 4 }}>
            <input type="checkbox" checked={xr.trail_with_st}
                   onChange={(e) => patchXr("trail_with_st", e.target.checked)} />
            跟踪（剩余仓位跟随 SuperTrend）
          </label>
        </div>
      </div>

      {/* 添加品种 */}
      <div style={SZ.sec}>
        <div style={SZ.h}>添加下单品种</div>
        <div style={SZ.row}>
          <input
            style={SZ.inp} placeholder="BTC-USDT" value={addForm.symbol}
            onChange={(e) => setAddForm((f) => ({ ...f, symbol: e.target.value.toUpperCase() }))}
          />
        </div>
        <div style={SZ.row}>
          <span style={{ color: C.neutral }}>保证金</span>
          <input
            style={{ ...SZ.inp, width: 70 }} type="number" value={addForm.margin}
            onChange={(e) => setAddForm((f) => ({ ...f, margin: e.target.value }))}
          />
          <span style={{ color: C.neutral }}>杠杆</span>
          <input
            style={{ ...SZ.inp, width: 60 }} type="number" value={addForm.leverage}
            onChange={(e) => setAddForm((f) => ({ ...f, leverage: e.target.value }))}
          />
        </div>
        <div style={SZ.row}>
          <span style={{ color: C.neutral }}>允许周期</span>
          {["15m", "1h", "4h", "1d"].map((tf) => (
            <label key={tf} style={{ fontSize: 11, color: C.text, display: "flex", alignItems: "center", gap: 3 }}>
              <input type="checkbox" checked={addForm.allow.includes(tf)} onChange={() => toggleTf(tf)} />
              {tf}
            </label>
          ))}
        </div>
        <button style={SZ.btn} onClick={addSymbol}>添加</button>
      </div>

      {/* 品种列表 */}
      <div style={SZ.sec}>
        <div style={SZ.h}>下单品种</div>
        {symbols.length === 0 && <div style={{ color: C.dim }}>暂无品种</div>}
        {symbols.map((s) => (
          <div key={s.symbol} style={{ borderTop: `1px solid ${C.border}`, paddingTop: 6, marginTop: 6 }}>
            <div style={SZ.row}>
              <input
                type="checkbox" checked={s.enabled}
                onChange={(e) => updateSymbol(s.symbol, { enabled: e.target.checked })}
              />
              <b style={{ fontFamily: "var(--font-mono)" }}>{s.symbol}</b>
              {s.position && <span style={{ ...SZ.tag, color: C.up }}>持仓中</span>}
              <button style={{ ...SZ.btnGhost, marginLeft: "auto" }} onClick={() => removeSymbol(s.symbol)}>删除</button>
            </div>
            <div style={{ ...SZ.row, flexWrap: "wrap" }}>
              <span style={{ color: C.neutral }}>
                保证金{(s.sizing_mode ?? "fixed") === "equity_pct" ? "(净值%)" : "(固定U)"}
              </span>
              <input
                style={{
                  ...SZ.inp, width: 62,
                  borderColor: (s.sizing_mode ?? "fixed") === "fixed" ? C.up : undefined,
                }}
                type="number" defaultValue={s.margin_usdt}
                title="固定保证金（U）：在这框里填值即按固定金额下单"
                onBlur={(e) => {
                  const v = e.target.value.trim();
                  if (v !== "") updateSymbol(s.symbol, { margin_usdt: Number(v), sizing_mode: "fixed" });
                }}
              />
              <span style={{ color: C.neutral }}>U</span>
              <span style={{ color: C.neutral, fontSize: 11 }}>或</span>
              <input
                style={{
                  ...SZ.inp, width: 52,
                  borderColor: (s.sizing_mode ?? "fixed") === "equity_pct" ? C.up : undefined,
                }}
                type="number" step={0.5} defaultValue={s.equity_pct ?? 10}
                title="账户净值百分比（%）：在这框里填值即按 净值×% 下单，并受 USDT 可用余额封顶"
                onBlur={(e) => {
                  const v = e.target.value.trim();
                  if (v !== "") updateSymbol(s.symbol, { equity_pct: Number(v), sizing_mode: "equity_pct" });
                }}
              />
              <span style={{ color: C.neutral }}>%</span>
              <span style={{ color: C.neutral }}>杠杆</span>
              <input
                style={{ ...SZ.inp, width: 52 }} type="number" defaultValue={s.leverage}
                onBlur={(e) => updateSymbol(s.symbol, { leverage: Number(e.target.value) })}
              />
            </div>
            <div style={{ ...SZ.row, flexWrap: "wrap" }}>
              {["15m", "1h", "4h", "1d"].map((tf) => (
                <label key={tf} style={{ fontSize: 11, display: "flex", alignItems: "center", gap: 3 }}>
                  <input
                    type="checkbox" checked={s.allow_tfs.includes(tf)}
                    onChange={(e) =>
                      updateSymbol(s.symbol, {
                        allow_tfs: e.target.checked
                          ? [...s.allow_tfs, tf]
                          : s.allow_tfs.filter((x) => x !== tf),
                      })
                    }
                  />
                  {tf}
                </label>
              ))}
              {s.position && (
                <button style={{ ...SZ.btnGhost, marginLeft: "auto" }} onClick={() => closePos(s.symbol)}>平仓</button>
              )}
            </div>
            {/* 该品种过滤策略 */}
            <div style={{ borderTop: `1px solid ${C.border}`, marginTop: 6, paddingTop: 6 }}>
              <div style={{ fontSize: 11, color: C.neutral, marginBottom: 4 }}>
                过滤策略（勾选即启用）
              </div>
              <div style={SZ.row}>
                <input
                  type="checkbox"
                  checked={!!s.filter_v3}
                  onChange={(e) => updateSymbol(s.symbol, { filter_v3: e.target.checked })}
                />
                <span style={{ fontSize: 11 }}>
                  V3 趋势过滤（Trend Score：成熟趋势 / 早期启动 / 慢热接住 才放行，含两级Fuse）
                </span>
              </div>
            </div>
            {/* 该品种独立止盈止损 */}
            <div style={{ borderTop: `1px dashed ${C.border}`, marginTop: 6, paddingTop: 6 }}>
              <div style={{ fontSize: 11, color: C.neutral, marginBottom: 4 }}>
                止盈止损（{s.symbol} 独立，留空=用默认）
              </div>
              <div style={SZ.row}>
                <span style={{ color: C.neutral, width: 56 }}>TP1</span>
                {tpInput(s.symbol, s, "tp1_pct", cfg?.tp1_pct ?? 1.0)}
                <span style={{ color: C.neutral }}>% 平</span>
                {tpInput(s.symbol, s, "tp1_ratio", cfg?.tp1_ratio ?? 30, 1)}
                <span style={{ color: C.neutral }}>%</span>
              </div>
              <div style={SZ.row}>
                <span style={{ color: C.neutral, width: 56 }}>TP2</span>
                {tpInput(s.symbol, s, "tp2_pct", cfg?.tp2_pct ?? 2.0)}
                <span style={{ color: C.neutral }}>% 平</span>
                {tpInput(s.symbol, s, "tp2_ratio", cfg?.tp2_ratio ?? 40, 1)}
                <span style={{ color: C.neutral }}>%</span>
              </div>
              <div style={SZ.row}>
                <span style={{ color: C.neutral, width: 56 }}>TP3</span>
                {tpInput(s.symbol, s, "tp3_pct", cfg?.tp3_pct ?? 3.5)}
                <span style={{ color: C.neutral }}>% 剩余全平（0 = 反向信号平仓）</span>
              </div>
              <div style={{ fontSize: 10, color: C.dim, marginTop: 4 }}>
                {(s.exit_mode ?? cfg?.exit_mode ?? "multi") === "single"
                  ? "单档模式（ExitRules）：只有 TP1 一档，平掉后剩余仓位靠保本 / 跟踪止损 / 同周期反向信号平掉，TP2、TP3 不生效。"
                  : "TP1 达成后止损移到开仓价保本；TP2 依次解锁并把止损抬到锁 1% 利润；TP3=0 时所有价格止盈止损失效，只在反向信号时平仓"}
              </div>
              {/* 出场档位：单档(ExitRules) / 三挡(EnhancedExitRules) + 一键预设 */}
              <div style={{ ...SZ.row, marginBottom: 4, flexWrap: "wrap" }}>
                <span style={{ color: C.neutral, width: 56 }}>档位</span>
                <label style={{ fontSize: 11, color: C.text, display: "flex", alignItems: "center", gap: 4 }}>
                  <input
                    type="checkbox"
                    checked={(s.exit_mode ?? cfg?.exit_mode ?? "multi") === "single"}
                    onChange={(e) => updateSymbol(s.symbol, { exit_mode: e.target.checked ? "single" : "multi" })}
                  />
                  单档止盈
                </label>
                <label style={{ fontSize: 11, color: C.text, display: "flex", alignItems: "center", gap: 4, marginLeft: 8 }}>
                  <input
                    type="checkbox"
                    checked={(s.exit_mode ?? cfg?.exit_mode ?? "multi") === "multi"}
                    onChange={(e) => updateSymbol(s.symbol, { exit_mode: e.target.checked ? "multi" : "single" })}
                  />
                  三挡止盈
                </label>
                <button
                  style={SZ.btnGhost}
                  title="切到单档 ExitRules：TP1 +1.5% 平 70% + 保本，剩余 30% 由跟踪止损/同周期反向信号平掉；初始止损为固定 3% 真实硬止损"
                  onClick={() => updateSymbol(s.symbol, {
                    exit_mode: "single",
                    tp1_pct: 1.5, tp1_ratio: 70,
                    sl_mode: "pct", sl_pct: 3.0,
                    move_sl_to_entry: true, trail_with_st: true, reverse_close: false,
                  })}
                >
                  预设：3%硬止损 · 单档1.5%/70% · 剩余反向平仓
                </button>
              </div>
              <div style={{ ...SZ.row, flexWrap: "wrap" }}>
                <span style={{ color: C.neutral, width: 56 }}>硬止损</span>
                <label style={{ fontSize: 11, color: C.text, display: "flex", alignItems: "center", gap: 4 }}>
                  <input
                    type="checkbox"
                    checked={(s.sl_mode ?? cfg?.sl_mode ?? "st") === "pct"}
                    onChange={(e) => updateSymbol(s.symbol, { sl_mode: e.target.checked ? "pct" : "st" })}
                  />
                  固定%
                </label>
                <label style={{ fontSize: 11, color: C.text, display: "flex", alignItems: "center", gap: 4, marginLeft: 8 }}>
                  <input
                    type="checkbox"
                    checked={(s.sl_mode ?? cfg?.sl_mode ?? "st") === "st"}
                    onChange={(e) => updateSymbol(s.symbol, { sl_mode: e.target.checked ? "st" : "pct" })}
                  />
                  超趋线
                </label>
                <input style={{ ...SZ.inp, width: 62, flex: "0 0 62px" }} type="number" step={0.1}
                       defaultValue={s.sl_pct ?? cfg?.sl_pct ?? 2.0}
                       onBlur={(e) => { const v = e.target.value.trim(); if (v !== "") updateSymbol(s.symbol, { sl_pct: Number(v) }); }} />
                <span style={{ color: C.neutral, fontSize: 11 }}>
                  %（{(s.sl_mode ?? cfg?.sl_mode ?? "st") === "pct" ? "真实硬止损" : "轨道兜底"}）
                </span>
              </div>
              <div style={SZ.row}>
                <label style={{ fontSize: 11, color: C.text, display: "flex", alignItems: "center", gap: 4 }}>
                  <input type="checkbox" defaultChecked={s.move_sl_to_entry ?? cfg.move_sl_to_entry ?? true}
                         onChange={(e) => updateSymbol(s.symbol, { move_sl_to_entry: e.target.checked })} />
                  保本
                </label>
                <label style={{ fontSize: 11, color: C.text, display: "flex", alignItems: "center", gap: 4, marginLeft: 8 }}>
                  <input type="checkbox" defaultChecked={s.trail_with_st ?? cfg.trail_with_st ?? true}
                         onChange={(e) => updateSymbol(s.symbol, { trail_with_st: e.target.checked })} />
                  跟踪
                </label>
              </div>
            </div>
            {s.position && <div style={{ fontSize: 11 }}>{fmtPnl(s.position)}</div>}
          </div>
        ))}
      </div>

      {/* 订单 / 日志 */}
      <div style={SZ.sec}>
        <div style={SZ.h}>
          最近下单
          <span style={{ fontWeight: 400, color: C.dim, marginLeft: 6 }}>
            {state?.running ? "运行中" : "未运行"}
          </span>
        </div>
        {(state?.orders || []).slice(-12).reverse().map((o, i) => (
          <div key={i} style={{ fontSize: 11, padding: "3px 0", borderTop: `1px solid ${C.border}` }}>
            <span style={{ color: o.ok === false ? C.down : C.text }}>
              {o.kind === "open" ? (o.sig_type === "buy" ? "买入开仓" : "卖出开仓")
                : (o.kind || "平仓")}
            </span>
            <span style={{ color: C.neutral, marginLeft: 6 }}>{o.sym}{o.tf ? ` · ${o.tf}` : ""}</span>
            {o.price ? <span style={{ color: C.neutral, marginLeft: 6 }}>@{o.price}</span> : null}
            {o.ok === false && <span style={{ color: C.down, marginLeft: 6 }}>{o.error}</span>}
          </div>
        ))}
        {err && <div style={{ color: C.down, fontSize: 11, marginTop: 6 }}>{err}</div>}
      </div>
    </div>
  );
}
