/**
 * 形态识别页 · 自动下单面板（右侧）
 *
 * 与首页 TradePanel 完全独立两套：读 /api/pattern/trade/*，
 * 配置存 pattern_trade.json、凭据存 pattern_credentials.json，互不干扰。
 *
 * 面板只暴露用户要的参数：
 *   交易所配置（OKX / Bitget + 各自 Key + 模拟盘）
 *   是否开启 4h 方向拦截
 *   下单品种的下单仓位保证金 / 杠杆 / 允许周期
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
        <div style={{ fontSize: 10.5, color: C.dim }}>
          出场固定用回测档：TP1 +1.5% 平 70% + 止损移到开仓价保本 + 剩余跟随 SuperTrend 跟踪
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
            <div style={SZ.row}>
              <span style={{ color: C.neutral }}>保证金</span>
              <input
                style={{ ...SZ.inp, width: 66 }} type="number" defaultValue={s.margin_usdt}
                onBlur={(e) => updateSymbol(s.symbol, { margin_usdt: Number(e.target.value) })}
              />
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
