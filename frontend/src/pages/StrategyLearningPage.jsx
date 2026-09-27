/**
 * 策略学习页
 *
 * 页面结构（与后端 /api/sl 一致）：
 *
 *   交易数据（SuperTrend 原始信号，分品种研究 / 先 BTC，预留其他品种）
 *        |
 *        +----------------------+
 *        |                      |
 *    LightGBM                聚类
 *    (预测/找影响因素)       (找市场类型)
 *        |
 *        |
 *      SHAP
 *    (解释 LightGBM 为什么这么判断)
 */
import { useEffect, useRef, useState } from "react";

const C = {
  bg: "#0b0b0d", card: "#111316", border: "#262626",
  text: "#e8eaed", muted: "#8b93a0", faint: "#5a6270",
  bull: "#00c9a7", bear: "#e05263", blue: "#4e8aff", purple: "#a78bfa",
};
const SYMBOLS = [
  { v: "BTC-USDT", label: "BTC-USDT", enabled: true },
  { v: "ETH-USDT", label: "ETH-USDT", enabled: false },
  { v: "SOL-USDT", label: "SOL-USDT", enabled: false },
];
const TFS = ["15m", "1h", "4h", "1d"];

const inp = {
  background: "#0b0b0d", color: C.text, border: "1px solid " + C.border,
  borderRadius: 6, padding: "5px 8px", fontSize: 12,
};
const btn = (color) => ({
  background: color, color: "#000", border: "none", borderRadius: 6,
  padding: "6px 14px", fontSize: 12, fontWeight: 700, cursor: "pointer",
});
const card = {
  background: C.card, border: "1px solid " + C.border, borderRadius: 10,
  padding: 14,
};
const nodeTag = (txt, color) => ({
  fontSize: 11, fontWeight: 700, color, letterSpacing: 0.4, marginBottom: 10,
  display: "flex", alignItems: "center", gap: 8,
});
const conn = { width: 2, height: 26, background: "linear-gradient(#2a2f36,#3a4048)", margin: "2px auto" };
const connArrow = { textAlign: "center", color: C.faint, fontSize: 12, margin: "1px 0" };

export default function StrategyLearningPage() {
  const [symbol, setSymbol] = useState("BTC-USDT");
  const [baseTf, setBaseTf] = useState("1h");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const [ds, setDs] = useState(null);             // 交易数据
  const [task, setTask] = useState("cls");        // LightGBM 任务
  const [train, setTrain] = useState(null);       // 训练结果
  const [training, setTraining] = useState(false);
  const [k, setK] = useState(4);                  // 聚类数
  const [cluster, setCluster] = useState(null);   // 聚类结果
  const [clustering, setClustering] = useState(false);
  const [shap, setShap] = useState(null);         // SHAP 结果
  const [shaping, setShaping] = useState(false);

  const reqId = useRef(0);

  const api = async (url, opts) => {
    const r = await fetch(url, opts);
    const j = await r.json().catch(() => null);
    if (!r.ok || !j) throw new Error(`HTTP ${r.status}`);
    return j;
  };

  const loadData = async () => {
    setLoading(true); setError(null);
    const id = ++reqId.current;
    try {
      const j = await api(`/api/sl/dataset?symbol=${encodeURIComponent(symbol)}&base_tf=${baseTf}&years=5`);
      if (id !== reqId.current) return;
      if (!j.ok) { setError(j.error || "无数据"); setDs(null); return; }
      setDs(j); setTrain(null); setCluster(null); setShap(null);
    } catch (e) {
      if (id === reqId.current) { setError("请求失败：" + String(e)); setDs(null); }
    } finally {
      if (id === reqId.current) setLoading(false);
    }
  };

  useEffect(() => { loadData(); /* eslint-disable-next-line */ }, []);

  const runTrain = async () => {
    if (!ds) return;
    setTraining(true); setError(null);
    try {
      const j = await api("/api/sl/train", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol, base_tf: baseTf, years: 5, task, test_size: 0.3 }),
      });
      if (!j.ok) { setError(j.error); return; }
      setTrain(j);
    } catch (e) { setError("训练失败：" + String(e)); }
    finally { setTraining(false); }
  };

  const runCluster = async () => {
    if (!ds) return;
    setClustering(true); setError(null);
    try {
      const j = await api("/api/sl/cluster", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol, base_tf: baseTf, years: 5, k }),
      });
      if (!j.ok) { setError(j.error); return; }
      setCluster(j);
    } catch (e) { setError("聚类失败：" + String(e)); }
    finally { setClustering(false); }
  };

  const runShap = async () => {
    if (!ds) return;
    setShaping(true); setError(null);
    try {
      const j = await api("/api/sl/shap", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol, base_tf: baseTf, years: 5, task, top_n: 6 }),
      });
      if (!j.ok) { setError(j.error); return; }
      setShap(j);
    } catch (e) { setError("SHAP 失败：" + String(e)); }
    finally { setShaping(false); }
  };

  const rows = ds?.rows || [];
  const fmtTime = (ts) => {
    if (!ts) return "-";
    const d = new Date(ts / 1000);
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getUTCFullYear()}/${p(d.getUTCMonth() + 1)}/${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
  };

  return (
    <div style={{ height: "100%", overflowY: "auto", color: C.text, padding: 16 }}>
      {/* 顶部控制栏 */}
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap", marginBottom: 14 }}>
        <span style={{ fontSize: 18, fontWeight: 800 }}>策略学习</span>
        <span style={{ fontSize: 12, color: C.faint }}>SuperTrend 原始信号（默认近 5 年 1h，首次加载需向 OKX 翻页，约 1 分钟）→ LightGBM / 聚类 → SHAP</span>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 10 }}>
          <label style={{ fontSize: 12, color: C.muted }}>品种
            <select value={symbol} onChange={(e) => setSymbol(e.target.value)} style={{ ...inp, marginLeft: 6 }}>
              {SYMBOLS.map((s) => (
                <option key={s.v} value={s.v} disabled={!s.enabled}>
                  {s.label}{s.enabled ? "" : "（预留）"}</option>
              ))}
            </select>
          </label>
          <label style={{ fontSize: 12, color: C.muted }}>周期
            <select value={baseTf} onChange={(e) => setBaseTf(e.target.value)} style={{ ...inp, marginLeft: 6 }}>
              {TFS.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
          <button onClick={loadData} style={btn(C.bull)} disabled={loading}>
            {loading ? "加载中…" : "加载交易数据"}</button>
        </div>
      </div>

      {error && <div style={{ color: C.bear, fontSize: 12, marginBottom: 10 }}>{error}</div>}

      {/* ── 交易数据 ── */}
      <div style={card}>
        <div style={nodeTag("① 交易数据 · SuperTrend 原始信号", C.bull)}>
          {ds && <span style={{ fontSize: 11, color: C.muted, fontWeight: 500 }}>
            {ds.symbol} / {ds.base_tf} · 信号 {ds.n_total} 笔 · 胜率 {ds.win_rate}% · 均盈 {ds.avg_pnl > 0 ? "+" : ""}{ds.avg_pnl}% · V3 通过 {ds.n_v3_pass} 笔</span>}
        </div>
        {!ds && <Empty loading={loading} err={error} />}
        {ds && (
          <div style={{ maxHeight: 280, overflowY: "auto", border: "1px solid " + C.border, borderRadius: 8 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
              <thead style={{ position: "sticky", top: 0, background: "#0e1013" }}>
                <tr style={{ color: C.muted, textAlign: "right" }}>
                  <th style={th}>时间</th><th style={th}>方向</th><th style={th}>盈亏%</th>
                  <th style={th}>市场状态</th><th style={th}>置信</th><th style={th}>V3路径</th><th style={th}>V3通过</th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 300).map((r, i) => (
                  <tr key={i} style={{ borderTop: "1px solid #1b1e22" }}>
                    <td style={{ ...td, textAlign: "left", color: C.faint }}>{fmtTime(r.ts)}</td>
                    <td style={{ ...td, color: r.dir > 0 ? C.bull : C.bear }}>{r.dir > 0 ? "多" : "空"}</td>
                    <td style={{ ...td, color: (r.pnl || 0) >= 0 ? C.bull : C.bear }}>
                      {(r.pnl ?? 0) > 0 ? "+" : ""}{r.pnl?.toFixed(2)}</td>
                    <td style={{ ...td, color: C.muted }}>{r.regime_cn || "-"}</td>
                    <td style={{ ...td, color: C.muted }}>{r.confidence ?? "-"}</td>
                    <td style={{ ...td, color: C.muted }}>{r.v3_path}</td>
                    <td style={{ ...td, color: r.v3_pass ? C.bull : C.faint }}>{r.v3_pass ? "✓" : "✕"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div style={conn} /><div style={connArrow}>▼</div>

      {/* ── 分支：LightGBM | 聚类 ── */}
      <div style={{ display: "flex", gap: 14, alignItems: "stretch" }}>
        {/* LightGBM */}
        <div style={{ ...card, flex: 1, minWidth: 0 }}>
          <div style={nodeTag("② LightGBM · 预测 / 找影响因素", C.blue)}>
            <label style={{ fontSize: 11, color: C.muted, fontWeight: 500, marginLeft: "auto" }}>
              任务
              <select value={task} onChange={(e) => setTask(e.target.value)} style={{ ...inp, marginLeft: 6, padding: "3px 6px" }}>
                <option value="cls">分类（是否盈利）</option>
                <option value="reg">回归（盈利幅度）</option>
              </select>
            </label>
          </div>
          <button onClick={runTrain} style={btn(C.blue)} disabled={!ds || training}>
            {training ? "训练中…" : "训练 LightGBM"}</button>
          {train && (
            <div style={{ marginTop: 12 }}>
              <div style={{ fontSize: 11, color: C.faint, marginBottom: 8 }}>
                后端：{train.metrics.backend}{train.lgbm_available ? "" : "（未装 lightgbm，已降级）"} ·
                训练 {train.metrics.n_train} / 测试 {train.metrics.n_test}
              </div>
              <MetricsBlock m={train.metrics} />
              <div style={{ fontSize: 12, color: C.muted, margin: "12px 0 6px", fontWeight: 700 }}>特征重要性（找影响因素）</div>
              <FeatureImportance list={train.importance} max={train.importance[0]?.ratio || 1} color={C.blue} />
            </div>
          )}
          {!train && <Hint>点击训练，用信号行情上下文预测该笔交易是否盈利，并给出特征重要性。</Hint>}
        </div>

        {/* 聚类 */}
        <div style={{ ...card, flex: 1, minWidth: 0 }}>
          <div style={nodeTag("③ 聚类 · 找市场类型", C.purple)}>
            <label style={{ fontSize: 11, color: C.muted, fontWeight: 500, marginLeft: "auto" }}>
              K
              <select value={k} onChange={(e) => setK(Number(e.target.value))} style={{ ...inp, marginLeft: 6, padding: "3px 6px" }}>
                {[3, 4, 5, 6].map((x) => <option key={x} value={x}>{x}</option>)}
              </select>
            </label>
          </div>
          <button onClick={runCluster} style={btn(C.purple)} disabled={!ds || clustering}>
            {clustering ? "聚类中…" : "运行聚类"}</button>
          {cluster && (
            <div style={{ marginTop: 12 }}>
              <div style={{ fontSize: 11, color: C.faint, marginBottom: 10 }}>
                轮廓系数 {cluster.silhouette != null ? cluster.silhouette.toFixed(3) : "—"} · 共 {cluster.n} 笔
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                {cluster.clusters.map((c) => (
                  <div key={c.id} style={{ background: "#0e1013", border: "1px solid " + C.border, borderRadius: 8, padding: 10 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 6 }}>
                      <span style={{ fontWeight: 700, color: C.purple }}>类型 {c.id}</span>
                      <span style={{ color: C.muted }}>{c.size} 笔</span>
                    </div>
                    <div style={{ fontSize: 11, color: C.muted, marginBottom: 6 }}>
                      主导状态 <b style={{ color: C.text }}>{c.dominant_regime}</b>
                    </div>
                    <div style={{ fontSize: 11, color: C.muted, marginBottom: 6 }}>
                      胜率 <b style={{ color: c.win_rate >= 0.5 ? C.bull : C.bear }}>{(c.win_rate * 100).toFixed(0)}%</b>
                      · 均盈 <b style={{ color: (c.avg_pnl || 0) >= 0 ? C.bull : C.bear }}>{(c.avg_pnl || 0) > 0 ? "+" : ""}{(c.avg_pnl || 0).toFixed(2)}%</b>
                      · V3通过 <b style={{ color: C.text }}>{(c.v3_pass_rate * 100).toFixed(0)}%</b>
                    </div>
                    <div style={{ fontSize: 10.5, color: C.faint }}>
                      {c.top_features.map((f) => (
                        <div key={f.feature} style={{ display: "flex", justifyContent: "space-between" }}>
                          <span>{f.cn}</span>
                          <span style={{ fontFamily: "var(--font-mono)" }}>{f.centroid.toFixed(2)} <span style={{ color: f.z > 0 ? C.bull : C.bear }}>(z{f.z.toFixed(1)})</span></span>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
          {!cluster && <Hint>点击运行 KMeans，把信号按行情上下文分成若干「市场类型」。</Hint>}
        </div>
      </div>

      <div style={conn} /><div style={connArrow}>▼</div>

      {/* ── SHAP ── */}
      <div style={card}>
        <div style={nodeTag("④ SHAP · 解释 LightGBM 为什么这么判断", C.bull)}>
          <button onClick={runShap} style={{ ...btn(C.bull), marginLeft: "auto" }} disabled={!ds || shaping}>
            {shaping ? "计算中…" : "生成 SHAP 解释"}</button>
        </div>
        {shap && (
          <div>
            <div style={{ fontSize: 11, color: C.faint, marginBottom: 8 }}>
              {shap.backend}{shap.shap_available ? "" : "（未装 shap，已降级）"}
              {shap.note ? " · " + shap.note : ""}
            </div>
            <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
              <div style={{ flex: 1, minWidth: 280 }}>
                <div style={{ fontSize: 12, color: C.muted, fontWeight: 700, marginBottom: 6 }}>全局特征贡献（均值 |SHAP|）</div>
                <FeatureImportance list={shap.global.map((g) => ({ feature: g.feature, cn: g.cn, ratio: g.mean_abs_shap }))}
                  max={shap.global[0]?.mean_abs_shap || 1} color={C.bull} />
              </div>
              <div style={{ flex: 1.4, minWidth: 320 }}>
                <div style={{ fontSize: 12, color: C.muted, fontWeight: 700, marginBottom: 6 }}>单样本分解（为什么这么判）</div>
                {shap.samples.length === 0 && <Hint>未安装 shap，仅显示全局贡献；pip install shap 后可见单样本分解。</Hint>}
                {shap.samples.map((s, i) => (
                  <div key={i} style={{ background: "#0e1013", border: "1px solid " + C.border, borderRadius: 8, padding: 10, marginBottom: 8 }}>
                    <div style={{ fontSize: 11, color: C.muted, marginBottom: 6 }}>
                      {fmtTime(s.ts)} · {s.dir > 0 ? "多" : "空"} · {s.regime_cn} ·
                      实际 <b style={{ color: s.actual >= 0.5 || s.actual > 0 ? C.bull : C.bear }}>{task === "cls" ? (s.actual >= 0.5 ? "盈利" : "亏损") : (s.pnl?.toFixed(2) + "%")}</b> ·
                      模型 <b style={{ color: C.text }}>{task === "cls" ? (s.predicted * 100).toFixed(0) + "%" : s.predicted.toFixed(2)}</b>
                    </div>
                    {s.contrib.map((c) => (
                      <div key={c.feature} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11, marginBottom: 4 }}>
                        <span style={{ width: 86, color: C.muted }}>{c.cn}</span>
                        <span style={{ width: 56, color: C.faint, fontFamily: "var(--font-mono)" }}>{c.value}</span>
                        <div style={{ flex: 1, height: 8, background: "#1b1e22", borderRadius: 4, position: "relative", overflow: "hidden" }}>
                          <div style={{
                            position: "absolute", top: 0, bottom: 0,
                            left: c.shap >= 0 ? "50%" : String(50 - Math.min(50, (Math.abs(c.shap) / (s.contrib[0]?.shap ? Math.abs(s.contrib[0].shap) : 1)) * 50)) + "%",
                            width: Math.min(50, (Math.abs(c.shap) / (s.contrib[0]?.shap ? Math.abs(s.contrib[0].shap) : 1)) * 50) + "%",
                            background: c.shap >= 0 ? C.bull : C.bear, borderRadius: 4,
                          }} />
                          <div style={{ position: "absolute", left: "50%", top: 0, bottom: 0, width: 1, background: "#3a4048" }} />
                        </div>
                        <span style={{ width: 50, textAlign: "right", color: c.shap >= 0 ? C.bull : C.bear, fontFamily: "var(--font-mono)" }}>{c.shap.toFixed(3)}</span>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
        {!shap && <Hint>点击生成，用 SHAP 解释 LightGBM 的逐笔判断：哪些特征把它推往「盈利/亏损」。</Hint>}
      </div>
    </div>
  );
}

const th = { padding: "7px 8px", fontWeight: 600, whiteSpace: "nowrap" };
const td = { padding: "6px 8px", textAlign: "right", fontFamily: "var(--font-mono)", whiteSpace: "nowrap" };

function MetricsBlock({ m }) {
  if (m.task === "cls") {
    const cm = m.confusion_matrix || {};
    return (
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 12 }}>
        <Stat k="准确率" v={(m.accuracy * 100).toFixed(1) + "%"} c={C.bull} />
        <Stat k="精确率" v={(m.precision * 100).toFixed(1) + "%"} />
        <Stat k="召回率" v={(m.recall * 100).toFixed(1) + "%"} />
        <Stat k="F1" v={m.f1.toFixed(2)} />
        <Stat k="AUC" v={m.auc != null ? m.auc.toFixed(2) : "—"} c={C.blue} />
        <Stat k="TP / FP" v={`${cm.tp || 0} / ${cm.fp || 0}`} />
        <Stat k="FN / TN" v={`${cm.fn || 0} / ${cm.tn || 0}`} />
      </div>
    );
  }
  return (
    <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 12 }}>
      <Stat k="MAE" v={m.mae.toFixed(2)} />
      <Stat k="RMSE" v={m.rmse.toFixed(2)} />
      <Stat k="R²" v={m.r2.toFixed(2)} c={m.r2 > 0 ? C.bull : C.bear} />
    </div>
  );
}
function Stat({ k, v, c }) {
  return (
    <div style={{ background: "#0e1013", border: "1px solid " + C.border, borderRadius: 8, padding: "6px 10px" }}>
      <div style={{ fontSize: 10.5, color: C.muted }}>{k}</div>
      <div style={{ fontSize: 14, fontWeight: 700, color: c || C.text }}>{v}</div>
    </div>
  );
}

function FeatureImportance({ list, max, color, suffix }) {
  if (!list || !list.length) return null;
  return (
    <div>
      {list.map((f) => {
        const v = f.ratio != null ? f.ratio : (f.importance || 0);
        const w = max > 0 ? Math.max(2, (Math.abs(v) / max) * 100) : 0;
        return (
          <div key={f.feature} style={{ marginBottom: 6 }}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: C.muted }}>
              <span>{f.cn || f.feature}</span>
              <span style={{ color: C.text, fontFamily: "var(--font-mono)" }}>{v.toFixed(3)}</span>
            </div>
            <div style={{ height: 7, background: "#1b1e22", borderRadius: 4, overflow: "hidden", marginTop: 3 }}>
              <div style={{ width: w + "%", height: "100%", background: color, borderRadius: 4 }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Hint({ children }) {
  return <div style={{ fontSize: 11.5, color: C.faint, marginTop: 12, lineHeight: 1.6 }}>{children}</div>;
}
function Empty({ loading, err }) {
  return <div style={{ padding: 30, textAlign: "center", color: C.faint, fontSize: 13 }}>
    {loading ? "加载中…" : (err || "暂无数据，点击「加载交易数据」")}</div>;
}
