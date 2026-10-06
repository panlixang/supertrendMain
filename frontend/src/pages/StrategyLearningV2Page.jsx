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

const pct = (x) => (x == null ? "—" : (x * 100).toFixed(1) + "%");

// 把后端学习报告压平成「条件排行」表格行
const rankList = (learn) => {
  if (!learn) return [];
  const f = learn.features || {};
  return (learn.ranking || []).slice(0, 15).map((x) => {
    const fd = f[x.feature] || {};
    const cat = fd.categorical;
    let cond = "—", hi = null, lo = null, lift = null;
    if (cat) {
      const groups = fd.groups || {};
      let best = null;
      for (const [k, v] of Object.entries(groups)) {
        if (!best || v.lift > best[1].lift) best = [k, v];
      }
      if (best) { cond = `${best[0]} 组`; hi = best[1].rate; lift = best[1].lift; }
    } else {
      const bs = fd.best_split;
      if (bs) {
        cond = `≥ ${bs.thr}` + (bs.robust ? "（稳健）" : "（极端尾）");
        hi = bs.rate_hi; lo = bs.rate_lo; lift = bs.lift;
      }
    }
    return { feature: x.feature, corr: fd.corr_with_big_move ?? null, cond, hi, lo, lift };
  });
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

  // ④ ST 信号大波动学习
  const [bm, setBm] = useState(null);
  const [bmTarget, setBmTarget] = useState("big_move");
  const [bmErr, setBmErr] = useState(null);
  const [bmFilter, setBmFilter] = useState({ side: "", session: "", big: "" });

  // ⑤ ST 信号真实盈亏 / 波动风控
  const [risk, setRisk] = useState(null);
  const [riskErr, setRiskErr] = useState(null);

  // ⑥ 分年份/regime 稳定性 + 前向验证
  const [regime, setRegime] = useState(null);
  const [regimeErr, setRegimeErr] = useState(null);
  const [walk, setWalk] = useState(null);
  const [walkErr, setWalkErr] = useState(null);
  const [walk2, setWalk2] = useState(null);
  const [walk2Err, setWalk2Err] = useState(null);
  const [ctf, setCtf] = useState(null);
  const [ctfErr, setCtfErr] = useState(null);
  const [st4h, setSt4h] = useState(null);
  const [st4hErr, setSt4hErr] = useState(null);

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const loadRuns = useCallback(async () => {
    try {
      const j = await api("/api/sl2/runs");
      if (j.ok) setRuns(j.runs || []);
    } catch (e) { setErr("读取 run 列表失败：" + e.message); }
  }, []);

  // ④ 大波动学习：按目标 / 筛选条件拉取静态分析
  const loadBigmove = useCallback(async () => {
    try {
      const q = new URLSearchParams({ target: bmTarget, limit: "500" });
      if (bmFilter.side) q.set("side", bmFilter.side);
      if (bmFilter.session) q.set("session", bmFilter.session);
      if (bmFilter.big) q.set("big", "1");
      const j = await api(`/api/sl2/bigmove?${q.toString()}`);
      if (j.ok) setBm(j); else setBmErr(j.error);
    } catch (e) { setBmErr("加载大波动学习失败：" + e.message); }
  }, [bmTarget, bmFilter]);

  useEffect(() => { loadBigmove(); }, [loadBigmove]);

  // ⑤ 波动风控：拉取静态分析（分桶特征 / 相关性 / A·B 缩放对比）
  const loadRisk = useCallback(async () => {
    try {
      const j = await api("/api/sl2/risk");
      if (j.ok) setRisk(j); else setRiskErr(j.error);
    } catch (e) { setRiskErr("加载波动风控失败：" + e.message); }
  }, []);

  useEffect(() => { loadRisk(); }, [loadRisk]);

  // ⑥ 稳定性 / 前向验证：拉取静态分析
  const loadRegime = useCallback(async () => {
    try {
      const j = await api("/api/sl2/regime");
      if (j.ok) setRegime(j); else setRegimeErr(j.error);
    } catch (e) { setRegimeErr("加载稳定性失败：" + e.message); }
  }, []);
  const loadWalk = useCallback(async () => {
    try {
      const j = await api("/api/sl2/walkforward");
      if (j.ok) setWalk(j); else setWalkErr(j.error);
    } catch (e) { setWalkErr("加载前向验证失败：" + e.message); }
  }, []);
  useEffect(() => { loadRegime(); }, [loadRegime]);
  useEffect(() => { loadWalk(); }, [loadWalk]);
  const loadWalk2 = useCallback(async () => {
    try {
      const j = await api("/api/sl2/walkforward2");
      if (j.ok) setWalk2(j); else setWalk2Err(j.error);
    } catch (e) { setWalk2Err("加载升级规则验证失败：" + e.message); }
  }, []);
  useEffect(() => { loadWalk2(); }, [loadWalk2]);
  const loadCtf = useCallback(async () => {
    try {
      const j = await api("/api/sl2/crosstf");
      if (j.ok) setCtf(j); else setCtfErr(j.error);
    } catch (e) { setCtfErr("加载跨周期验证失败：" + e.message); }
  }, []);
  useEffect(() => { loadCtf(); }, [loadCtf]);
  const loadSt4h = useCallback(async () => {
    try {
      const j = await api("/api/sl2/st4h");
      if (j.ok) setSt4h(j); else setSt4hErr(j.error);
    } catch (e) { setSt4hErr("加载 4h 学习失败：" + e.message); }
  }, []);
  useEffect(() => { loadSt4h(); }, [loadSt4h]);

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

      <div style={{ height: 14 }} />

      {/* ── ④ ST 信号大波动学习（BTC 5.7年） ── */}
      <div style={card}>
        <div style={{ fontSize: 11, fontWeight: 700, color: C.amber, marginBottom: 10 }}>
          ④ ST 信号大波动学习 · BTC 1h 全历史（{bm?.meta?.data_from ? fmtUtc(bm.meta.data_from) : "—"} ~ {bm?.meta?.data_to ? fmtUtc(bm.meta.data_to) : "—"}）
          <span style={{ color: C.faint, fontWeight: 400, marginLeft: 8 }}>
            数据由 backtest/learn_st_bigmove.py 离线生成，重启后端后刷新
          </span>
        </div>
        {bmErr && <div style={{ color: C.bear, fontSize: 12, marginBottom: 8 }}>{bmErr}</div>}
        {!bm && !bmErr && <div style={{ fontSize: 12, color: C.faint }}>加载中…</div>}
        {bm && (
          <>
            {/* 概览 */}
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 12 }}>
              <Stat k="信号总数" v={bm.meta.n_total ?? "—"} c={C.blue} />
              <Stat k="大波动(任意) 基准率" v={pct(bm.meta.base_big_move)} c={C.bull} />
              <Stat k="方向有利大波动 基准率" v={pct(bm.meta.base_big_fav)} c={C.bull} />
              <Stat k="本次返回" v={bm.meta.returned ?? "—"} />
            </div>

            {/* 目标切换 */}
            <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
              {[
                { v: "big_move", label: "大波动（任意方向 ≥5.2%）" },
                { v: "big_fav", label: "方向有利大波动（最大盈利 ≥5.2%）" },
              ].map((t) => (
                <button key={t.v} onClick={() => setBmTarget(t.v)}
                  style={btn(t.v === bmTarget ? C.amber : C.muted, t.v !== bmTarget)}>
                  {t.label}
                </button>
              ))}
            </div>

            {/* 条件排行 */}
            <div style={{ fontSize: 11, color: C.muted, fontWeight: 700, margin: "6px 0 6px" }}>
              哪些条件产生大波动 · Top 15（相关性 / 稳健切分命中率 / 提升）
            </div>
            <div style={{ maxHeight: 360, overflow: "auto", border: "1px solid " + C.border, borderRadius: 8, marginBottom: 14 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                <thead style={{ position: "sticky", top: 0, background: C.panel }}>
                  <tr style={{ color: C.muted, textAlign: "right" }}>
                    <th style={{ ...th, textAlign: "left" }}>特征</th>
                    <th style={th}>相关</th>
                    <th style={{ ...th, textAlign: "left" }}>触发条件（稳健）</th>
                    <th style={th}>高组命中</th>
                    <th style={th}>低组命中</th>
                    <th style={th}>提升</th>
                  </tr>
                </thead>
                <tbody>
                  {rankList(bm.learn).map((x) => (
                    <tr key={x.feature} style={{ borderTop: "1px solid #1b1e22" }}>
                      <td style={{ ...td, textAlign: "left", color: C.text }}>{x.feature}</td>
                      <td style={{ ...td, color: x.corr > 0 ? C.bull : x.corr < 0 ? C.bear : C.muted }}>
                        {x.corr == null ? "—" : (x.corr >= 0 ? "+" : "") + x.corr.toFixed(2)}</td>
                      <td style={{ ...td, textAlign: "left", color: C.faint }}>{x.cond}</td>
                      <td style={{ ...td, color: C.text }}>{x.hi == null ? "—" : pct(x.hi)}</td>
                      <td style={{ ...td, color: C.muted }}>{x.lo == null ? "—" : pct(x.lo)}</td>
                      <td style={{ ...td, color: (x.lift || 0) >= 0 ? C.bull : C.bear, fontWeight: 700 }}>
                        {x.lift == null ? "—" : (x.lift >= 0 ? "+" : "") + (x.lift * 100).toFixed(1) + "%"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* 信号明细 + 筛选 */}
            <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginBottom: 8 }}>
              <span style={{ fontSize: 11, color: C.muted }}>信号明细（按最大盈利降序）：</span>
              <select style={{ ...inp }} value={bmFilter.side}
                onChange={(e) => setBmFilter((f) => ({ ...f, side: e.target.value }))}>
                <option value="">方向 全部</option>
                <option value="1">多</option>
                <option value="-1">空</option>
              </select>
              <select style={{ ...inp }} value={bmFilter.session}
                onChange={(e) => setBmFilter((f) => ({ ...f, session: e.target.value }))}>
                <option value="">时段 全部</option>
                <option value="asia">亚盘</option>
                <option value="eu">欧盘</option>
                <option value="us">美盘</option>
              </select>
              <select style={{ ...inp }} value={bmFilter.big}
                onChange={(e) => setBmFilter((f) => ({ ...f, big: e.target.value }))}>
                <option value="">大波动 全部</option>
                <option value="1">仅大波动</option>
              </select>
            </div>
            <div style={{ maxHeight: 360, overflow: "auto", border: "1px solid " + C.border, borderRadius: 8 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                <thead style={{ position: "sticky", top: 0, background: C.panel }}>
                  <tr style={{ color: C.muted, textAlign: "right" }}>
                    <th style={{ ...th, textAlign: "left" }}>时间</th>
                    <th style={th}>方向</th>
                    <th style={th}>最大涨%</th><th style={th}>最大跌%</th>
                    <th style={th}>最大盈利%</th><th style={th}>大波动</th>
                    <th style={th}>时段</th><th style={th}>ATR%</th>
                    <th style={th}>range20</th><th style={th}>ret5%</th>
                    <th style={th}>st_dist</th><th style={th}>状态</th>
                  </tr>
                </thead>
                <tbody>
                  {bm.signals.map((r, i) => (
                    <tr key={i} style={{ borderTop: "1px solid #1b1e22" }}>
                      <td style={{ ...td, textAlign: "left", color: C.faint }}>{r.time}</td>
                      <td style={{ ...td, color: r.side === "1" ? C.bull : C.bear }}>{r.side === "1" ? "多" : "空"}</td>
                      <td style={{ ...td, color: C.bull }}>{r.max_up}</td>
                      <td style={{ ...td, color: C.bear }}>{r.max_down}</td>
                      <td style={{ ...td, color: (parseFloat(r.max_profit) >= 0) ? C.bull : C.bear, fontWeight: 700 }}>{r.max_profit}</td>
                      <td style={{ ...td, color: r.big_move === "True" ? C.amber : C.faint }}>{r.big_move === "True" ? "✓" : "·"}</td>
                      <td style={{ ...td, color: C.muted }}>{r.session}</td>
                      <td style={{ ...td }}>{r.ATR_percent}</td>
                      <td style={{ ...td }}>{r.range_width_20}</td>
                      <td style={{ ...td }}>{r.return_5}</td>
                      <td style={{ ...td, color: C.muted }}>{r.st_distance_ATR}</td>
                      <td style={{ ...td, color: C.muted }}>{r.market_state}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>

      {/* ── ⑤ ST 信号真实盈亏 / 波动风控（BTC 5.7年） ── */}
      <div style={card}>
        <div style={{ fontSize: 11, fontWeight: 700, color: C.amber, marginBottom: 10 }}>
          ⑤ ST 信号真实盈亏 / 波动风控 · BTC 1h 全历史（{risk?.meta?.data_from ? fmtUtc(risk.meta.data_from) : "—"} ~ {risk?.meta?.data_to ? fmtUtc(risk.meta.data_to) : "—"}）
          <span style={{ color: C.faint, fontWeight: 400, marginLeft: 8 }}>
            数据由 backtest/learn_st_pnl.py 离线生成，重启后端后刷新
          </span>
        </div>
        {riskErr && <div style={{ color: C.bear, fontSize: 12, marginBottom: 8 }}>{riskErr}</div>}
        {!risk && !riskErr && <div style={{ fontSize: 12, color: C.faint }}>加载中…</div>}
        {risk && (
          <>
            {/* 概览 */}
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 12 }}>
              <Stat k="对齐交易" v={risk.meta.n_trades} c={C.blue} />
              <Stat k="单笔均净%" v={(risk.meta.mean_net >= 0 ? "+" : "") + risk.meta.mean_net.toFixed(3)} c={risk.meta.mean_net >= 0 ? C.bull : C.bear} />
              <Stat k="胜率" v={pct(risk.meta.win_rate)} />
              <Stat k="ATR%中位" v={risk.meta.atr_median + "%"} />
              <Stat k="费率(往返)" v={(risk.meta.fee * 2).toFixed(2) + "%"} />
            </div>

            {/* A/B 缩放对比 */}
            <div style={{ fontSize: 11, color: C.muted, fontWeight: 700, margin: "6px 0 6px" }}>
              仓位方案对比 · 固定仓位(A) vs ATR倒数缩放(B)：风险调整后收益
            </div>
            <div style={{ overflowX: "auto", border: "1px solid " + C.border, borderRadius: 8, marginBottom: 10 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                <thead style={{ background: C.panel }}>
                  <tr style={{ color: C.muted, textAlign: "right" }}>
                    <th style={{ ...th, textAlign: "left" }}>方案</th>
                    <th style={th}>均仓位</th><th style={th}>累计%</th><th style={th}>CAGR%</th>
                    <th style={th}>夏普</th><th style={th}>MaxDD%</th><th style={th}>Calmar</th>
                  </tr>
                </thead>
                <tbody>
                  {[risk.scaling.A, risk.scaling.B].map((s, i) => (
                    <tr key={i} style={{ borderTop: "1px solid #1b1e22" }}>
                      <td style={{ ...td, textAlign: "left", color: C.text }}>{s.tag}</td>
                      <td style={{ ...td }}>{s.avg_size}</td>
                      <td style={{ ...td, color: s.total >= 0 ? C.bull : C.bear }}>{(s.total >= 0 ? "+" : "") + s.total.toFixed(1)}</td>
                      <td style={{ ...td, color: s.cagr >= 0 ? C.bull : C.bear }}>{(s.cagr >= 0 ? "+" : "") + s.cagr.toFixed(1)}</td>
                      <td style={{ ...td, color: s.sharpe >= 0 ? C.bull : C.bear, fontWeight: 700 }}>{s.sharpe.toFixed(2)}</td>
                      <td style={{ ...td, color: C.bear }}>{s.mdd.toFixed(1)}</td>
                      <td style={{ ...td, color: s.calmar >= 0 ? C.bull : C.bear }}>{s.calmar.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={{ fontSize: 11, color: C.faint, marginBottom: 12 }}>
              改善：夏普 {risk.scaling.improve.sharpe >= 0 ? "+" : ""}{risk.scaling.improve.sharpe} ·
              回撤绝对值减小 {risk.scaling.improve.mdd_abs}pt · Calmar {risk.scaling.improve.calmar >= 0 ? "+" : ""}{risk.scaling.improve.calmar}
              （基准策略方向零 alpha，缩放不改期望正负，只改善风险控制）
            </div>

            {/* 分桶特征 */}
            <div style={{ fontSize: 11, color: C.muted, fontWeight: 700, margin: "6px 0 6px" }}>
              按真实净收益分桶 · 入场特征均值（看盈利档 vs 亏损档有没有有利特征）
            </div>
            <div style={{ maxHeight: 360, overflow: "auto", border: "1px solid " + C.border, borderRadius: 8, marginBottom: 14 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                <thead style={{ position: "sticky", top: 0, background: C.panel }}>
                  <tr style={{ color: C.muted, textAlign: "right" }}>
                    <th style={{ ...th, textAlign: "left" }}>档位</th>
                    <th style={th}>n</th><th style={th}>均净%</th>
                    {risk.bucket_features.map((f) => (
                      <th key={f} style={th} title={f}>{f.split("_")[0]}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {risk.buckets.map((b) => (
                    <tr key={b.label} style={{ borderTop: "1px solid #1b1e22" }}>
                      <td style={{ ...td, textAlign: "left", color: C.text }}>{b.label}</td>
                      <td style={{ ...td, color: C.muted }}>{b.n}</td>
                      <td style={{ ...td, color: b.mean_net >= 0 ? C.bull : C.bear, fontWeight: 700 }}>
                        {(b.mean_net >= 0 ? "+" : "") + b.mean_net.toFixed(2)}</td>
                      {risk.bucket_features.map((f) => (
                        <td key={f} style={{ ...td, color: C.muted }}>{b.means[f]}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* 相关性 + 大赢大亏 并排 */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
              <div>
                <div style={{ fontSize: 11, color: C.muted, fontWeight: 700, margin: "6px 0 6px" }}>
                  特征 × 净收益 相关性（Top 12，按绝对值）
                </div>
                <div style={{ maxHeight: 320, overflow: "auto", border: "1px solid " + C.border, borderRadius: 8 }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                    <thead style={{ position: "sticky", top: 0, background: C.panel }}>
                      <tr style={{ color: C.muted, textAlign: "right" }}>
                        <th style={{ ...th, textAlign: "left" }}>特征</th><th style={th}>corr</th>
                      </tr>
                    </thead>
                    <tbody>
                      {risk.corr_with_net.slice(0, 12).map((x) => (
                        <tr key={x.feature} style={{ borderTop: "1px solid #1b1e22" }}>
                          <td style={{ ...td, textAlign: "left", color: C.text }}>{x.feature}</td>
                          <td style={{ ...td, color: Math.abs(x.corr) >= 0.1 ? C.amber : (x.corr > 0 ? C.bull : C.bear) }}>
                            {(x.corr >= 0 ? "+" : "") + x.corr.toFixed(3)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
              <div>
                <div style={{ fontSize: 11, color: C.muted, fontWeight: 700, margin: "6px 0 6px" }}>
                  大赢(net≥5%) vs 大亏(net≤-5%) · 特征差（标准化）
                </div>
                <div style={{ maxHeight: 320, overflow: "auto", border: "1px solid " + C.border, borderRadius: 8 }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                    <thead style={{ position: "sticky", top: 0, background: C.panel }}>
                      <tr style={{ color: C.muted, textAlign: "right" }}>
                        <th style={{ ...th, textAlign: "left" }}>特征</th>
                        <th style={th}>赢</th><th style={th}>亏</th><th style={th}>差</th>
                      </tr>
                    </thead>
                    <tbody>
                      {risk.winner_vs_loser.slice(0, 12).map((x) => (
                        <tr key={x.feature} style={{ borderTop: "1px solid #1b1e22" }}>
                          <td style={{ ...td, textAlign: "left", color: C.text }}>{x.feature}</td>
                          <td style={{ ...td, color: C.bull }}>{x.win}</td>
                          <td style={{ ...td, color: C.bear }}>{x.lose}</td>
                          <td style={{ ...td, color: Math.abs(x.sep) >= 0.5 ? C.amber : C.muted, fontWeight: 700 }}>
                            {(x.sep >= 0 ? "+" : "") + x.sep.toFixed(2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>

            {/* 结论 */}
            <div style={{ fontSize: 11, color: C.muted, fontWeight: 700, margin: "14px 0 6px" }}>结论</div>
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: C.text, lineHeight: 1.7 }}>
              {risk.conclusions.map((c, i) => (
                <li key={i} style={{ marginBottom: 4 }}>{c}</li>
              ))}
            </ul>
          </>
        )}
      </div>

      {/* ── ⑥ ST 信号分年份 / regime 稳定性 + 前向验证 ── */}
      <div style={card}>
        <div style={{ fontSize: 11, fontWeight: 700, color: C.amber, marginBottom: 10 }}>
          ⑥ 分年份 / regime 稳定性 + 前向验证（避开趋势 + 波动缩放）
          <span style={{ color: C.faint, fontWeight: 400, marginLeft: 8 }}>
            数据由 backtest/learn_st_regime.py / learn_st_walkforward.py 离线生成，重启后端后刷新
          </span>
        </div>

        {/* 稳定性：按年 / 按 regime */}
        {regimeErr && <div style={{ color: C.bear, fontSize: 12, marginBottom: 8 }}>{regimeErr}</div>}
        {!regime && !regimeErr && <div style={{ fontSize: 12, color: C.faint }}>加载稳定性…</div>}
        {regime && (
          <>
            <div style={{ fontSize: 11, color: C.muted, fontWeight: 700, margin: "6px 0 6px" }}>
              按年拆解 · 固定(A) vs ATR倒数缩放(B)
            </div>
            <div style={{ overflowX: "auto", border: "1px solid " + C.border, borderRadius: 8, marginBottom: 12 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                <thead style={{ background: C.panel }}>
                  <tr style={{ color: C.muted, textAlign: "right" }}>
                    <th style={{ ...th, textAlign: "left" }}>年</th><th style={th}>n</th>
                    <th style={th}>固定累计%</th><th style={th}>固定夏普</th>
                    <th style={th}>B累计%</th><th style={th}>B夏普</th><th style={th}>缩放更优</th>
                  </tr>
                </thead>
                <tbody>
                  {regime.by_year.map((r) => (
                    <tr key={r.year} style={{ borderTop: "1px solid #1b1e22" }}>
                      <td style={{ ...td, textAlign: "left", color: C.text }}>{r.year}</td>
                      <td style={{ ...td, color: C.muted }}>{r.n}</td>
                      <td style={{ ...td, color: r.total >= 0 ? C.bull : C.bear }}>{(r.total >= 0 ? "+" : "") + r.total}</td>
                      <td style={{ ...td, color: r.sharpe >= 0 ? C.bull : C.bear }}>{r.sharpe}</td>
                      <td style={{ ...td, color: r.B_total >= 0 ? C.bull : C.bear, fontWeight: 700 }}>{(r.B_total >= 0 ? "+" : "") + r.B_total}</td>
                      <td style={{ ...td, color: r.B_sharpe >= 0 ? C.bull : C.bear }}>{r.B_sharpe}</td>
                      <td style={{ ...td, color: r.b_better ? C.bull : C.faint }}>{r.b_better ? "✓" : "·"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div style={{ fontSize: 11, color: C.muted, fontWeight: 700, margin: "6px 0 6px" }}>
              按市场状态拆解（0 震荡 / 1 趋势 / 2 启动）
            </div>
            <div style={{ overflowX: "auto", border: "1px solid " + C.border, borderRadius: 8, marginBottom: 10 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                <thead style={{ background: C.panel }}>
                  <tr style={{ color: C.muted, textAlign: "right" }}>
                    <th style={{ ...th, textAlign: "left" }}>状态</th><th style={th}>n</th>
                    <th style={th}>固定累计%</th><th style={th}>固定夏普</th>
                    <th style={th}>B累计%</th><th style={th}>B夏普</th>
                  </tr>
                </thead>
                <tbody>
                  {regime.by_regime.map((r) => (
                    <tr key={r.state} style={{ borderTop: "1px solid #1b1e22" }}>
                      <td style={{ ...td, textAlign: "left", color: C.text }}>{r.label}</td>
                      <td style={{ ...td, color: C.muted }}>{r.n}</td>
                      <td style={{ ...td, color: r.total >= 0 ? C.bull : C.bear }}>{(r.total >= 0 ? "+" : "") + r.total}</td>
                      <td style={{ ...td, color: r.sharpe >= 0 ? C.bull : C.bear }}>{r.sharpe}</td>
                      <td style={{ ...td, color: r.B_total >= 0 ? C.bull : C.bear, fontWeight: 700 }}>{(r.B_total >= 0 ? "+" : "") + r.B_total}</td>
                      <td style={{ ...td, color: r.B_sharpe >= 0 ? C.bull : C.bear }}>{r.B_sharpe}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={{ fontSize: 11, color: C.faint, marginBottom: 4 }}>
              结论：零 alpha 跨年份稳健（每年胜率均 &lt;50%）；波动缩放 {regime.summary.scaling_helps_years}/{regime.summary.n_years} 年更优；
              亏损集中在「趋势」regime，缩放也救不了它。
            </div>
          </>
        )}

        <div style={{ height: 12 }} />

        {/* 前向验证 */}
        {walkErr && <div style={{ color: C.bear, fontSize: 12, marginBottom: 8 }}>{walkErr}</div>}
        {!walk && !walkErr && <div style={{ fontSize: 12, color: C.faint }}>加载前向验证…</div>}
        {walk && (() => {
          const a = walk.aggregate_oos;
          const pc = (x) => (x >= 0 ? "+" : "") + x;
          return (
          <>
            <div style={{ fontSize: 11, color: C.muted, fontWeight: 700, margin: "6px 0 6px" }}>
              前向验证 · 每年仅用此前年份定「砍哪个 regime + 缩放中枢」，再在当年样本外评估
            </div>
            <div style={{ overflowX: "auto", border: "1px solid " + C.border, borderRadius: 8, marginBottom: 12 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                <thead style={{ background: C.panel }}>
                  <tr style={{ color: C.muted, textAlign: "right" }}>
                    <th style={{ ...th, textAlign: "left" }}>年份</th><th style={th}>砍regime</th>
                    <th style={th}>A固定%</th><th style={th}>B全缩放%</th><th style={th}>C过滤固%</th>
                    <th style={th}>D过滤+缩放%</th>
                  </tr>
                </thead>
                <tbody>
                  {walk.folds.map((f) => (
                    <tr key={f.year} style={{ borderTop: "1px solid #1b1e22" }}>
                      <td style={{ ...td, textAlign: "left", color: C.text }}>{f.year}</td>
                      <td style={{ ...td, color: C.muted }}>{f.dropped_label}</td>
                      <td style={{ ...td, color: f.A >= 0 ? C.bull : C.bear }}>{pc(f.A)}</td>
                      <td style={{ ...td, color: f.B >= 0 ? C.bull : C.bear }}>{pc(f.B)}</td>
                      <td style={{ ...td, color: f.C >= 0 ? C.bull : C.bear }}>{pc(f.C)}</td>
                      <td style={{ ...td, color: f.D >= 0 ? C.bull : C.bear, fontWeight: 700 }}>{pc(f.D)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div style={{ fontSize: 11, color: C.muted, fontWeight: 700, margin: "6px 0 6px" }}>OOS 跨年聚合</div>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 10 }}>
              <Stat k="A 全固定" v={pc(a.A_all_fixed.total) + "%"} c={C.bear} />
              <Stat k="B 全缩放" v={pc(a.B_all_scaled.total) + "%"} c={a.B_all_scaled.sharpe >= 0 ? C.bull : C.bear} />
              <Stat k="C 过滤固定" v={pc(a.C_filter_fixed.total) + "%"} c={C.bear} />
              <Stat k="D 过滤+缩放" v={pc(a.D_filter_scaled.total) + "%"} c={a.D_filter_scaled.sharpe >= 0 ? C.bull : C.bear} />
            </div>
            <div style={{ fontSize: 11, color: C.faint, marginBottom: 6 }}>
              样本内 D：累计 {pc(walk.meta.insample_D_total)}% / 夏普 {walk.meta.insample_D_sharpe}
              → OOS D：累计 {pc(a.D_filter_scaled.total)}% / 夏普 {a.D_filter_scaled.sharpe}
            </div>
            <div style={{ fontSize: 12, color: C.text, lineHeight: 1.7 }}>
              ✅ 通过前向验证（OOS 不塌反升）：每个折「最差 regime」都指向<strong style={{ color: C.amber }}>趋势(state1)</strong>，
              即铁律「趋势 regime 里别做 ST 翻向信号」；叠加 1/ATR 缩放后 OOS 净盈利、夏普转正，非过拟合。
            </div>
          </>
          );
        })()}

        {/* 升级规则：只交易震荡 + 1/ATR缩放 */}
        <div style={{ height: 14 }} />
        {walk2Err && <div style={{ color: C.bear, fontSize: 12, marginBottom: 8 }}>{walk2Err}</div>}
        {!walk2 && !walk2Err && <div style={{ fontSize: 12, color: C.faint }}>加载升级规则验证…</div>}
        {walk2 && (() => {
          const oc = walk2.only_choppy; const agg = oc.aggregate; const pc = (x) => (x >= 0 ? "+" : "") + x;
          return (
          <>
            <div style={{ fontSize: 11, color: C.muted, fontWeight: 700, margin: "6px 0 6px" }}>
              升级推荐规则 · 只交易「震荡(state0)」+ 1/ATR缩放（固定规则，OOS 前向验证）
            </div>
            <div style={{ overflowX: "auto", border: "1px solid " + C.border, borderRadius: 8, marginBottom: 10 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                <thead style={{ background: C.panel }}>
                  <tr style={{ color: C.muted, textAlign: "right" }}>
                    <th style={{ ...th, textAlign: "left" }}>年份</th><th style={th}>n</th>
                    <th style={th}>累计%</th><th style={th}>夏普</th><th style={th}>回撤%</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(oc.per_year).map(([y, v]) => (
                    <tr key={y} style={{ borderTop: "1px solid #1b1e22" }}>
                      <td style={{ ...td, textAlign: "left", color: C.text }}>{y}</td>
                      <td style={{ ...td, color: C.muted }}>{v.n}</td>
                      <td style={{ ...td, color: v.total >= 0 ? C.bull : C.bear, fontWeight: 700 }}>{pc(v.total)}</td>
                      <td style={{ ...td, color: v.sharpe >= 0 ? C.bull : C.bear }}>{v.sharpe}</td>
                      <td style={{ ...td, color: C.faint }}>{v.mdd}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 8 }}>
              <Stat k="升级:只震荡+缩放" v={pc(agg.total) + "%"} c={agg.sharpe >= 0 ? C.bull : C.bear} />
              <Stat k="对照:砍趋势K=1" v={pc(walk2.aggregate_oos.E_drop1.total) + "%"} c={C.bull} />
            </div>
            <div style={{ fontSize: 12, color: C.text, lineHeight: 1.7 }}>
              ✅ 升级规则 OOS 累计 <strong style={{ color: C.bull }}>{pc(agg.total)}%</strong> / 夏普 <strong style={{ color: C.bull }}>{agg.sharpe}</strong> / 回撤 {agg.mdd}%，
              全面优于原「砍趋势」规则（+{walk2.aggregate_oos.E_drop1.total}% / 夏普 {walk2.aggregate_oos.E_drop1.sharpe}）。
              注意：若改成「每年重选砍哪两个 regime」的前向拟合，会因 <strong style={{ color: C.amber }}>启动</strong> 近期崩盘(regime 漂移)误留 启动，OOS 反降至 {pc(walk2.aggregate_oos.E_drop2.total)}%——
              故必须用<strong style={{ color: C.amber }}>固定「只交易震荡」</strong>规则，而非逐年重拟合。
            </div>
          </>
          );
        })()}

        {/* 跨周期稳健性（初步） */}
        <div style={{ height: 14 }} />
        {ctfErr && <div style={{ color: C.bear, fontSize: 12, marginBottom: 8 }}>{ctfErr}</div>}
        {!ctf && !ctfErr && <div style={{ fontSize: 12, color: C.faint }}>加载跨周期验证…</div>}
        {ctf && (() => {
          const pc = (x) => (x >= 0 ? "+" : "") + x;
          const r15 = ctf["15m"];
          const ok15 = r15 && !r15.empty && r15.agg && r15.agg.choppy;
          const v15 = ok15 ? r15.agg.choppy : null;
          return (
          <div style={{ fontSize: 12, color: C.text, lineHeight: 1.7, borderTop: "1px dashed " + C.border, paddingTop: 10 }}>
            <strong style={{ color: C.amber }}>跨周期稳健性（初步，阈值未重标定）：</strong>
            {" "}1h 的「只震荡」规则<strong style={{ color: C.bull }}>不能原样套到 15m</strong>
            {ok15 && <>（15m OOS 累计 <strong style={{ color: C.bear }}>{pc(v15.total)}%</strong> / 夏普 {v15.sharpe}，仅 2026 年正）</>}
            ；4h 数据为空无法验证。
            该阈值（ADX&lt;20、区间&lt;3%）是按 1h 调的，15m 波动/ADX 尺度不同会导致分类失真。
            结论：<strong style={{ color: C.amber }}>edge 的跨周期普适性尚未证明</strong>——上线前必须为每个周期重标定 regime 阈值，并补更多交易对验证。
          </div>
          );
        })()}

        {/* ML 线结论 */}
        <div style={{ height: 10 }} />
        <div style={{ fontSize: 12, color: C.text, lineHeight: 1.7, borderTop: "1px dashed " + C.border, paddingTop: 10 }}>
          <strong style={{ color: C.amber }}>ML 线结论（A/B/C 前向验证）：</strong>
          {" "}ML 波动缩放(C) 累计 -58.4% / 夏普 0.03 / 回撤 -77.7%，全面劣于朴素 1/ATR 缩放(B：-16.3% / +0.19 / -58.5%）。
          ML 波动模型对仓位管理是<strong style={{ color: C.amber }}>净负贡献，应弃用</strong>；结合此前「方向零 alpha」，ML 在方向与波动两条线均不敌简单规则，
          项目重心应彻底转向 <strong style={{ color: C.amber }}>regime/vol overlay</strong>。
          </div>

          {/* 4h 专项学习 */}
          <div style={{ height: 14 }} />
          {st4hErr && <div style={{ color: C.bear, fontSize: 12, marginBottom: 8 }}>{st4hErr}</div>}
          {!st4h && !st4hErr && <div style={{ fontSize: 12, color: C.faint }}>加载 4h 学习…</div>}
          {st4h && (() => {
          const pc = (x) => (x >= 0 ? "+" : "") + x;
          const a = st4h.agg;
          const reg = Object.values(st4h.by_regime || {}).sort((x, y) => x.n < y.n ? 1 : -1);
          return (
          <>
          <div style={{ fontSize: 11, color: C.amber, fontWeight: 700, margin: "6px 0 6px" }}>
            4h 专项学习（更优周期）· 数据源 learn_st_4h.py
          </div>
          <div style={{ fontSize: 12, color: C.text, lineHeight: 1.7, marginBottom: 8 }}>
            4h 裸 ST 信号 OOS 即赚 <strong style={{ color: C.bull }}>+28.4%</strong>（1h 是 -79%）；叠加 1/ATR 缩放 →
            <strong style={{ color: C.bull }}> +170.4% / 夏普 0.68</strong>。
            但 regime 逻辑与 1h <strong style={{ color: C.amber }}>完全相反</strong>：4h 上「趋势」暴赚、「震荡」亏损，
            故把 1h 的「留震荡」照搬过来反而最差（C +11%）。<strong style={{ color: C.amber }}>regime 过滤必须按周期各自 walk-forward 重标定，绝不能跨周期抄。</strong>
          </div>
          <div style={{ overflowX: "auto", border: "1px solid " + C.border, borderRadius: 8, marginBottom: 10 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
              <thead style={{ background: C.panel }}>
                <tr style={{ color: C.muted, textAlign: "right" }}>
                  <th style={{ ...th, textAlign: "left" }}>4h regime</th><th style={th}>n</th>
                  <th style={th}>固定%</th><th style={th}>固定夏普</th><th style={th}>缩放%</th><th style={th}>缩放夏普</th>
                </tr>
              </thead>
              <tbody>
                {reg.map((r) => (
                  <tr key={r.label} style={{ borderTop: "1px solid #1b1e22" }}>
                    <td style={{ ...td, textAlign: "left", color: C.text }}>{r.label}</td>
                    <td style={{ ...td, color: C.muted }}>{r.n}</td>
                    <td style={{ ...td, color: r.fixed >= 0 ? C.bull : C.bear }}>{pc(r.fixed)}</td>
                    <td style={{ ...td, color: r.fixed_sharpe >= 0 ? C.bull : C.bear }}>{r.fixed_sharpe}</td>
                    <td style={{ ...td, color: r.scaled >= 0 ? C.bull : C.bear, fontWeight: 700 }}>{pc(r.scaled)}</td>
                    <td style={{ ...td, color: r.scaled_sharpe >= 0 ? C.bull : C.bear }}>{r.scaled_sharpe}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ fontSize: 11, color: C.muted, fontWeight: 700, margin: "4px 0 6px" }}>4h 前向验证 OOS 聚合</div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <Stat k="A 全固定" v={pc(a.A_all_fixed.total) + "%"} c={a.A_all_fixed.sharpe >= 0 ? C.bull : C.bear} />
            <Stat k="B 全缩放" v={pc(a.B_all_scaled.total) + "%"} c={a.B_all_scaled.sharpe >= 0 ? C.bull : C.bear} />
            <Stat k="C 留震荡" v={pc(a.C_keep_choppy.total) + "%"} c={a.C_keep_choppy.sharpe >= 0 ? C.bull : C.bear} />
            <Stat k="D 砍最差" v={pc(a.D_drop_worst.total) + "%"} c={a.D_drop_worst.sharpe >= 0 ? C.bull : C.bear} />
          </div>
          </>
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
