/**
 * 策略学习 V2 · 独立新管线页（新增页面）
 *
 * 与原有「策略学习」页完全独立：
 *   - 原页面 /api/sl/* 逻辑一行未改，本页只调 /api/sl2/*
 *   - 后端以子进程跑 sl_v2.py，模型落盘在 backend/ml_runs/<run>/
 *   - 模型读写走 booster.save_model() / lgb.Booster(model_file=...)，
 *     特征顺序由 features.json 固定，训练/预测严格对齐
 *
 * 流程：配置 → 启动训练（异步）→ 轮询日志/指标 → 选中 run → 预测
 */
import { useCallback, useEffect, useRef, useState } from "react";

const C = {
  bg: "#0b0b0d", card: "#111316", panel: "#0e1013", border: "#262626",
  text: "#e8eaed", muted: "#8b93a0", faint: "#5a6270",
  bull: "#00c9a7", bear: "#e05263", blue: "#4e8aff", purple: "#a78bfa", amber: "#ef9f27",
};
const inp = {
  background: C.bg, color: C.text, border: "1px solid " + C.border,
  borderRadius: 6, padding: "5px 8px", fontSize: 12,
};
const btn = (color, ghost) => ({
  background: ghost ? "transparent" : color, color: ghost ? color : "#000",
  border: ghost ? "1px solid " + color + "66" : "none", borderRadius: 6,
  padding: "6px 14px", fontSize: 12, fontWeight: 700, cursor: "pointer",
});
const card = { background: C.card, border: "1px solid " + C.border, borderRadius: 10, padding: 14 };
const th = { padding: "7px 8px", fontWeight: 600, whiteSpace: "nowrap" };
const td = { padding: "6px 8px", textAlign: "right", fontFamily: "var(--font-mono)", whiteSpace: "nowrap" };

const SAMPLES = [
  { v: "all", label: "全部信号" },
  { v: "v3", label: "仅 V3 通过" },
  { v: "nonchop", label: "剔除震荡无序" },
  { v: "v3_nonchop", label: "V3 + 非震荡" },
];
const LABELS = [
  { v: "tpsl", label: "tpsl（先触TP/SL）· 分类", task: "cls" },
  { v: "exit", label: "exit（tp1+反向平仓）· 分类", task: "cls" },
  { v: "fwd", label: "fwd（未来N根方向）· 分类", task: "cls" },
  { v: "vol", label: "vol（未来波动率比）· 回归 ★", task: "reg" },
  { v: "mfe", label: "mfe（最大有利/不利偏移）· 回归", task: "reg" },
];

const fmtUtc = (ms) => {
  if (!ms) return "-";
  const d = new Date(ms);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}/${p(d.getUTCMonth() + 1)}/${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
};

const api = async (url, opts) => {
  const r = await fetch(url, opts);
  const j = await r.json().catch(() => null);
  if (!r.ok) throw new Error(`HTTP ${r.status} ${j ? JSON.stringify(j).slice(0, 120) : ""}`);
  return j;
};

export default function StrategyLearningV2Page() {
  const [health, setHealth] = useState(null);
  const [runs, setRuns] = useState([]);
  const [err, setErr] = useState(null);

  // 训练表单
  const [form, setForm] = useState({
    name: "btc_1h_tpsl_h30_v3", symbol: "BTC-USDT", base_tf: "1h",
    label_mode: "tpsl", horizon: 30, tp_pct: 2.5, sl_pct: 2.0,
    sample: "v3", task: "cls", rounds: 300, force: false, no_cache: false,
  });
  const [job, setJob] = useState(null);
  const [starting, setStarting] = useState(false);
  const timer = useRef(null);

  // 选中的 run
  const [sel, setSel] = useState(null);
  const [meta, setMeta] = useState(null);
  const [pred, setPred] = useState(null);
  const [predTail, setPredTail] = useState(20);
  const [predSample, setPredSample] = useState("");
  const [preding, setPreding] = useState(false);

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const loadRuns = useCallback(async () => {
    try {
      const j = await api("/api/sl2/runs");
      if (j.ok) setRuns(j.runs || []);
    } catch (e) { setErr("读取 run 列表失败：" + e.message); }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const h = await api("/api/sl2/health");
        setHealth(h);
      } catch (e) { setErr("后端未就绪（/api/sl2 需要重启后端才生效）：" + e.message); }
      loadRuns();
    })();
  }, [loadRuns]);

  // 训练中轮询
  useEffect(() => {
    if (!job || job.status !== "running") return;
    timer.current = setTimeout(async () => {
      try {
        const j = await api(`/api/sl2/jobs/${encodeURIComponent(job.name)}`);
        if (j.ok) {
          setJob(j);
          if (j.status !== "running") { loadRuns(); if (j.meta) openRun(j.name); }
        }
      } catch (_) { /* 忽略单次轮询失败 */ }
    }, 3000);
    return () => clearTimeout(timer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job, loadRuns]);

  const startTrain = async () => {
    setStarting(true); setErr(null); setPred(null);
    try {
      const j = await api("/api/sl2/train", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      if (!j.ok) { setErr(j.error); return; }
      const v = await api(`/api/sl2/jobs/${encodeURIComponent(j.name)}`);
      setJob(v.ok ? v : { name: j.name, status: "running", log_tail: "" });
    } catch (e) { setErr("启动训练失败：" + e.message); }
    finally { setStarting(false); }
  };

  const openRun = async (name) => {
    setSel(name); setPred(null);
    try {
      const j = await api(`/api/sl2/runs/${encodeURIComponent(name)}`);
      setMeta(j.ok ? j.meta : null);
      if (!j.ok) setErr(j.error);
    } catch (e) { setErr("读取 run 失败：" + e.message); }
  };

  const runPredict = async () => {
    if (!sel) return;
    setPreding(true); setErr(null);
    try {
      const j = await api("/api/sl2/predict", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: sel, tail: predTail, sample: predSample || null }),
      });
      if (!j.ok) { setErr(j.error + (j.detail ? " | " + j.detail : "")); return; }
      setPred(j);
    } catch (e) { setErr("预测失败：" + e.message); }
    finally { setPreding(false); }
  };

  const m = meta?.metrics || {};

  return (
    <div style={{ height: "100%", overflowY: "auto", color: C.text, padding: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", marginBottom: 14 }}>
        <span style={{ fontSize: 18, fontWeight: 800 }}>策略学习 V2 · 独立管线</span>
        <span style={{ fontSize: 12, color: C.faint }}>
          独立进程 + 独立目录，原有「策略学习」页与 /api/sl 完全不受影响
        </span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 10, alignItems: "center", fontSize: 11 }}>
          <span style={{ color: health?.lightgbm ? C.bull : C.bear }}>
            LightGBM {health?.lightgbm || "未检测到"}
          </span>
          <button onClick={loadRuns} style={btn(C.muted, true)}>刷新列表</button>
        </div>
      </div>

      {err && <div style={{ color: C.bear, fontSize: 12, marginBottom: 10 }}>{err}</div>}

      {/* ── 训练配置 ── */}
      <div style={card}>
        <div style={{ fontSize: 11, fontWeight: 700, color: C.blue, marginBottom: 10 }}>
          ① 新建训练（另起进程，不影响正在跑的那个）
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 10 }}>
          <Field label="run 名称">
            <input style={{ ...inp, width: "100%" }} value={form.name}
              onChange={(e) => set("name", e.target.value)} placeholder="btc_1h_tpsl_h30_v3" />
          </Field>
          <Field label="标签口径">
            <select style={{ ...inp, width: "100%" }} value={form.label_mode}
              onChange={(e) => {
                const v = e.target.value;
                const L = LABELS.find((x) => x.v === v);
                // vol / mfe 是回归标签，任务跟着切，避免提交出无效组合
                setForm((f) => ({ ...f, label_mode: v, task: L?.task || f.task }));
              }}>
              {LABELS.map((x) => <option key={x.v} value={x.v}>{x.label}</option>)}
            </select>
          </Field>
          <Field label="样本子集">
            <select style={{ ...inp, width: "100%" }} value={form.sample}
              onChange={(e) => set("sample", e.target.value)}>
              {SAMPLES.map((x) => <option key={x.v} value={x.v}>{x.label}</option>)}
            </select>
          </Field>
          <Field label="任务">
            <select style={{ ...inp, width: "100%" }} value={form.task}
              onChange={(e) => set("task", e.target.value)}>
              <option value="cls">分类（是否盈利）</option>
              <option value="reg">回归（盈利幅度）</option>
            </select>
          </Field>
          <Field label="horizon（根）">
            <input type="number" style={{ ...inp, width: "100%" }} value={form.horizon}
              onChange={(e) => set("horizon", Number(e.target.value))} />
          </Field>
          <Field label="TP %">
            <input type="number" step="0.1" style={{ ...inp, width: "100%" }} value={form.tp_pct}
              onChange={(e) => set("tp_pct", Number(e.target.value))} />
          </Field>
          <Field label="SL %">
            <input type="number" step="0.1" style={{ ...inp, width: "100%" }} value={form.sl_pct}
              onChange={(e) => set("sl_pct", Number(e.target.value))} />
          </Field>
          <Field label="rounds 上限">
            <input type="number" style={{ ...inp, width: "100%" }} value={form.rounds}
              onChange={(e) => set("rounds", Number(e.target.value))} />
          </Field>
        </div>

        <div style={{ display: "flex", gap: 16, alignItems: "center", marginTop: 12, flexWrap: "wrap" }}>
          <button onClick={startTrain} disabled={starting || job?.status === "running"} style={btn(C.blue)}>
            {starting ? "启动中…" : job?.status === "running" ? "训练进行中…" : "开始训练"}
          </button>
          <label style={{ fontSize: 11, color: C.muted }}>
            <input type="checkbox" checked={form.force}
              onChange={(e) => set("force", e.target.checked)} /> 覆盖同名 run
          </label>
          <label style={{ fontSize: 11, color: C.muted }}>
            <input type="checkbox" checked={form.no_cache}
              onChange={(e) => set("no_cache", e.target.checked)} /> 忽略数据集缓存（强制重建）
          </label>
          <span style={{ fontSize: 11, color: C.faint }}>
            首次建数据集约 3 分钟（之后命中磁盘缓存，秒级）
          </span>
        </div>

        {/* 训练任务状态 */}
        {job && (
          <div style={{ marginTop: 14, background: C.panel, border: "1px solid " + C.border, borderRadius: 8, padding: 12 }}>
            <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap", fontSize: 12, marginBottom: 8 }}>
              <b style={{ color: C.text }}>{job.name}</b>
              <StatusPill status={job.status} />
              <span style={{ color: C.faint }}>已用 {job.elapsed ?? 0}s{job.returncode != null ? ` · rc=${job.returncode}` : ""}</span>
            </div>
            {job.status === "done" && job.meta && (
              <>
                <Metrics m={job.meta.metrics} />
                <div style={{ fontSize: 11, color: C.faint, margin: "8px 0 4px" }}>
                  最优迭代 {job.meta.best_iteration} 轮 · 特征 {job.meta.n_features} 维 · 样本 {job.meta.metrics?.n_rows}
                </div>
                <Importance list={(job.meta.importance || []).slice(0, 8)} />
              </>
            )}
            {job.log_tail && (
              <pre style={{
                marginTop: 8, marginBottom: 0, maxHeight: 160, overflow: "auto", fontSize: 11,
                color: C.muted, background: C.bg, border: "1px solid " + C.border,
                borderRadius: 6, padding: 8, whiteSpace: "pre-wrap",
              }}>{job.log_tail}</pre>
            )}
          </div>
        )}
      </div>

      <div style={{ height: 14 }} />

      {/* ── run 列表 ── */}
      <div style={card}>
        <div style={{ fontSize: 11, fontWeight: 700, color: C.purple, marginBottom: 10 }}>
          ② 已有模型（backend/ml_runs/）· 共 {runs.length} 个
        </div>
        {runs.length === 0 && <div style={{ fontSize: 12, color: C.faint }}>暂无。启动一次训练后出现在这里。</div>}
        {runs.length > 0 && (
          <div style={{ overflowX: "auto", border: "1px solid " + C.border, borderRadius: 8 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
              <thead style={{ background: C.panel }}>
                <tr style={{ color: C.muted, textAlign: "right" }}>
                  <th style={{ ...th, textAlign: "left" }}>run</th>
                  <th style={th}>标签</th><th style={th}>样本集</th><th style={th}>h</th>
                  <th style={th}>样本</th><th style={th}>特征</th>
                  <th style={th}>acc</th><th style={th}>auc</th><th style={th}>最优轮</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.name} onClick={() => openRun(r.name)}
                    style={{
                      borderTop: "1px solid #1b1e22", cursor: "pointer",
                      background: sel === r.name ? "#4e8aff14" : "transparent",
                    }}>
                    <td style={{ ...td, textAlign: "left", color: sel === r.name ? C.blue : C.text }}>{r.name}</td>
                    <td style={{ ...td, color: C.muted }}>{r.label_mode}{r.label_mode === "tpsl" ? ` ${r.tp_pct}/${r.sl_pct}` : ""}</td>
                    <td style={{ ...td, color: C.muted }}>{r.sample}</td>
                    <td style={{ ...td, color: C.faint }}>{r.horizon}</td>
                    <td style={{ ...td, color: C.muted }}>{r.n_rows}</td>
                    <td style={{ ...td, color: C.faint }}>{r.n_features}</td>
                    <td style={{ ...td }}>{r.metrics?.accuracy != null ? r.metrics.accuracy.toFixed(3) : "—"}</td>
                    <td style={{ ...td, color: (r.metrics?.auc ?? 0) > 0.55 ? C.bull : C.muted }}>
                      {r.metrics?.auc != null ? r.metrics.auc.toFixed(3) : "—"}</td>
                    <td style={{ ...td, color: (r.best_iteration ?? 0) <= 5 ? C.bear : C.muted }}>{r.best_iteration}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div style={{ height: 14 }} />

      {/* ── run 详情 + 预测 ── */}
      <div style={card}>
        <div style={{ fontSize: 11, fontWeight: 700, color: C.bull, marginBottom: 10 }}>
          ③ 预测 {sel ? `· ${sel}` : ""}
        </div>
        {!sel && <div style={{ fontSize: 12, color: C.faint }}>在上表点选一个 run。</div>}
        {sel && meta && (
          <>
            <Metrics m={meta.metrics} />
            <div style={{ fontSize: 11, color: C.faint, margin: "10px 0 6px" }}>
              训练配置：{meta.symbol} {meta.base_tf} · label={meta.label_mode} · sample={meta.sample} ·
              horizon={meta.horizon} · TP/SL={meta.tp_pct}/{meta.sl_pct} ·
              特征 {meta.n_features} 维 · 最优迭代 {meta.best_iteration} ·
              {String(meta.created_at || "").slice(0, 19)}
            </div>
            <div style={{ fontSize: 11, color: C.faint, marginBottom: 8 }}>
              特征顺序已固定为 features.json（共 {meta.feature_order?.length || meta.n_features} 项），
              预测时按该顺序取值，缺失补 0、多余忽略
            </div>
            <Importance list={(meta.importance || []).slice(0, 10)} />

            <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 14, flexWrap: "wrap" }}>
              <button onClick={runPredict} disabled={preding} style={btn(C.bull)}>
                {preding ? "预测中…（首次建缓存约3分钟）" : "运行预测"}
              </button>
              <label style={{ fontSize: 11, color: C.muted }}>
                最近
                <input type="number" style={{ ...inp, width: 70, marginLeft: 6 }}
                  value={predTail} onChange={(e) => setPredTail(Number(e.target.value))} />
                笔
              </label>
              <label style={{ fontSize: 11, color: C.muted }}>
                样本集
                <select style={{ ...inp, marginLeft: 6 }} value={predSample}
                  onChange={(e) => setPredSample(e.target.value)}>
                  <option value="">沿用训练时（{meta.sample}）</option>
                  {SAMPLES.map((x) => <option key={x.v} value={x.v}>{x.label}</option>)}
                </select>
              </label>
            </div>
          </>
        )}

        {pred && (() => {
          const isCls = pred.task === "cls";   // 回归标签没有"对否"，换个列头
          return (
          <div style={{ marginTop: 14 }}>
            <div style={{ fontSize: 11, color: C.faint, marginBottom: 8 }}>
              {pred.n} 笔 · {pred.n_features} 维特征对齐 · 数据集{pred.ds_cached ? "命中缓存" : "本次构建"}
              {pred.hit != null ? ` · 样本内命中 ${pred.hit}/${pred.n} = ${(pred.hit / Math.max(1, pred.n) * 100).toFixed(1)}%` : ""}
            </div>
            <div style={{ maxHeight: 320, overflow: "auto", border: "1px solid " + C.border, borderRadius: 8 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                <thead style={{ position: "sticky", top: 0, background: C.panel }}>
                  <tr style={{ color: C.muted, textAlign: "right" }}>
                    <th style={th}>时间</th><th style={th}>方向</th><th style={th}>实际盈亏%</th>
                    <th style={th}>市场状态</th><th style={th}>V3</th>
                    <th style={th}>{isCls ? "预测概率" : "模型预测值"}</th><th style={th}>模型判断</th>
                    {isCls && <th style={th}>对否</th>}
                  </tr>
                </thead>
                <tbody>
                  {pred.rows.slice().reverse().map((r, i) => {
                    const ok = r.win == null ? null : (r.pred === r.win);
                    return (
                      <tr key={i} style={{ borderTop: "1px solid #1b1e22" }}>
                        <td style={{ ...td, textAlign: "left", color: C.faint }}>{fmtUtc(r.ts)}</td>
                        <td style={{ ...td, color: r.dir > 0 ? C.bull : C.bear }}>{r.dir > 0 ? "多" : "空"}</td>
                        <td style={{ ...td, color: (r.pnl || 0) >= 0 ? C.bull : C.bear }}>
                          {(r.pnl ?? 0) > 0 ? "+" : ""}{(r.pnl ?? 0).toFixed(2)}</td>
                        <td style={{ ...td, color: C.muted }}>{r.regime6_cn || "-"}</td>
                        <td style={{ ...td, color: r.v3_pass ? C.bull : C.faint }}>{r.v3_pass ? "✓" : "✕"}</td>
                        <td style={{ ...td, color: (!isCls || r.prob >= 0.5) ? C.bull : C.muted }}>
                          {isCls ? r.prob.toFixed(3) : r.prob.toFixed(3)}</td>
                        <td style={{ ...td, color: isCls ? (r.pred ? C.bull : C.bear) : C.text }}>
                          {isCls ? (r.pred ? "盈" : "亏") : (r.prob >= 1 ? "波动放大" : "波动收敛")}</td>
                        {isCls && (
                          <td style={{ ...td, color: ok == null ? C.faint : ok ? C.bull : C.bear }}>
                            {ok == null ? "—" : ok ? "✓" : "✕"}</td>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
          );
        })()}
      </div>
    </div>
  );
}

function Field({ label, children }) {
  return (
    <label style={{ fontSize: 11, color: C.muted, display: "block" }}>
      <div style={{ marginBottom: 4 }}>{label}</div>
      {children}
    </label>
  );
}
function StatusPill({ status }) {
  const map = { running: [C.amber, "训练中"], done: [C.bull, "完成"], failed: [C.bear, "失败"] };
  const [color, text] = map[status] || [C.muted, status];
  return (
    <span style={{
      fontSize: 11, fontWeight: 700, color, background: color + "1a",
      border: "1px solid " + color + "55", borderRadius: 20, padding: "2px 10px",
    }}>{text}</span>
  );
}
function Metrics({ m }) {
  if (!m) return null;
  if (m.task === "cls") {
    const cm = m.confusion_matrix || {};
    return (
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", fontSize: 12 }}>
        <Stat k="准确率" v={m.accuracy != null ? (m.accuracy * 100).toFixed(1) + "%" : "—"} c={C.bull} />
        <Stat k="精确率" v={m.precision != null ? (m.precision * 100).toFixed(1) + "%" : "—"} />
        <Stat k="召回率" v={m.recall != null ? (m.recall * 100).toFixed(1) + "%" : "—"} />
        <Stat k="F1" v={m.f1 != null ? m.f1.toFixed(3) : "—"} />
        <Stat k="AUC" v={m.auc != null ? m.auc.toFixed(3) : "—"} c={C.blue} />
        <Stat k="TP/FP" v={`${cm.tp || 0}/${cm.fp || 0}`} />
        <Stat k="FN/TN" v={`${cm.fn || 0}/${cm.tn || 0}`} />
        <Stat k="n_test" v={m.n_test ?? "—"} />
      </div>
    );
  }
  return (
    <div style={{ display: "flex", gap: 10, flexWrap: "wrap", fontSize: 12 }}>
      <Stat k="MAE" v={m.mae != null ? m.mae.toFixed(3) : "—"} />
      <Stat k="RMSE" v={m.rmse != null ? m.rmse.toFixed(3) : "—"} />
      <Stat k="R²" v={m.r2 != null ? m.r2.toFixed(3) : "—"} c={(m.r2 || 0) > 0 ? C.bull : C.bear} />
      <Stat k="n_test" v={m.n_test ?? "—"} />
    </div>
  );
}
function Stat({ k, v, c }) {
  return (
    <div style={{ background: C.panel, border: "1px solid " + C.border, borderRadius: 8, padding: "6px 10px" }}>
      <div style={{ fontSize: 10.5, color: C.muted }}>{k}</div>
      <div style={{ fontSize: 14, fontWeight: 700, color: c || C.text }}>{v}</div>
    </div>
  );
}
function Importance({ list }) {
  if (!list || !list.length) return null;
  const max = list[0].ratio || 1;
  return (
    <div>
      <div style={{ fontSize: 11, color: C.muted, fontWeight: 700, margin: "10px 0 6px" }}>特征重要性（gain）</div>
      {list.map((f) => (
        <div key={f.feature} style={{ marginBottom: 5 }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: C.muted }}>
            <span>{f.cn || f.feature}</span>
            <span style={{ color: C.text, fontFamily: "var(--font-mono)" }}>
              {(f.ratio * 100).toFixed(1)}%</span>
          </div>
          <div style={{ height: 6, background: "#1b1e22", borderRadius: 4, overflow: "hidden", marginTop: 3 }}>
            <div style={{ width: Math.max(2, (f.ratio / max) * 100) + "%", height: "100%", background: C.blue, borderRadius: 4 }} />
          </div>
        </div>
      ))}
    </div>
  );
}
