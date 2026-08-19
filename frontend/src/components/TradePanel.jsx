/**
 * 自动挂单面板（OKX 合约 / 现货）
 *
 * 设计前提：下单不可逆，所以
 *   - 总开关默认关，环境默认模拟盘
 *   - 切「实盘」要二次确认，全程红色警示
 *   - 闸门条件全部可见可调，用户随时知道「为什么没下单」
 */
import { useEffect, useState } from 'react';
import { ALL_TFS, useStore } from '../stores/useStore';
import { API, fmt } from '../utils/format';

const LEVERAGES = [1, 2, 3, 5, 10, 20];

export default function TradePanel() {
  const cfg = useStore((s) => s.tradeConfig);
  const setCfg = useStore((s) => s.setTradeConfig);
  const allRules = useStore((s) => s.exitRules);
  const setRules = useStore((s) => s.setExitRules);
  // 双档：normal 标准档 / quick 快进快出档（弱档信号用）
  const rules = allRules?.normal;
  const quick = allRules?.quick;
  const orders = useStore((s) => s.orders);
  const closed = useStore((s) => s.closed);
  const tf = useStore((s) => s.tf);
  const positions = useStore((s) => s.positions);
  const tickers = useStore((s) => s.tickers);
  const symbolCfgs = useStore((s) => s.symbolCfgs);
  const setSymbolCfgs = useStore((s) => s.setSymbolCfgs);
  const maxSymbols = useStore((s) => s.maxSymbols);

  // 后端只在仓位变动时推 position，浮动盈亏要跟着各品种 ticker 秒级更新，
  // 所以这里用「该品种自己的最新价」本地重算 —— 千万不能拿别的品种的价来算
  const posList = Object.entries(positions)
    .map(([sym, p]) => {
      const t = tickers[sym]?.last;
      return [sym, p && t ? withLivePnl(p, t) : p];
    })
    .filter(([, p]) => p && p.qty > 0);

  const [amount, setAmount] = useState(10);
  const [offset, setOffset] = useState(0.05);
  const [tp1, setTp1] = useState(1.5);
  const [tpRatio, setTpRatio] = useState(70);
  const [slPct, setSlPct] = useState(2);
  // 弱档（快进快出）规则的本地输入
  const [qTp, setQTp] = useState(0.8);
  const [qSl, setQSl] = useState(1);
  const [msg, setMsg] = useState('');
  const [ping, setPing] = useState(null);
  const [regime, setRegime] = useState(null);
  const [confirmLive, setConfirmLive] = useState(false);

  useEffect(() => {
    if (!cfg) return;
    setAmount(cfg.amount_usdt);
    setOffset(cfg.price_offset);
  }, [cfg?.amount_usdt, cfg?.price_offset]);

  useEffect(() => {
    if (!rules) return;
    setTp1(rules.tp1_pct);
    setTpRatio(rules.tp1_ratio);
    setSlPct(rules.sl_pct);
  }, [rules?.tp1_pct, rules?.tp1_ratio, rules?.sl_pct]);

  useEffect(() => {
    if (!quick) return;
    setQTp(quick.tp1_pct);
    setQSl(quick.sl_pct);
  }, [quick?.tp1_pct, quick?.sl_pct]);

  // 当前周期的行情状态，让用户直观看到「现在下不下得了单」
  useEffect(() => {
    let dead = false;
    const load = async () => {
      try {
        const r = await fetch(`${API}/api/regime?tf=${tf}`);
        if (!dead) setRegime(await r.json());
      } catch {}
    };
    load();
    const iv = setInterval(load, 20000);
    return () => { dead = true; clearInterval(iv); };
  }, [tf]);

  const flash = (m) => { setMsg(m); setTimeout(() => setMsg(''), 4500); };

  async function patch(body, note) {
    try {
      const r = await fetch(`${API}/api/trade/config`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (d.ok) { setCfg(d.config); flash(note || '已保存'); }
      else flash(d.error || '保存失败');
    } catch { flash('网络错误'); }
  }

  async function patchRules(body, note) {
    try {
      const r = await fetch(`${API}/api/trade/exit-rules`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (d.ok) { setRules(d.rules); flash(note || '规则已更新'); }
      else flash(d.error || '保存失败');
    } catch { flash('网络错误'); }
  }

  async function doPing() {
    setPing({ loading: true });
    try {
      const r = await fetch(`${API}/api/trade/ping`);
      setPing(await r.json());
    } catch { setPing({ ok: false, error: '网络错误' }); }
  }

  async function testOrder() {
    flash('下测试单中…');
    try {
      const r = await fetch(`${API}/api/trade/test-order`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ side: 'buy' }),
      });
      const d = await r.json();
      flash(d.ok ? `✅ 测试单已挂 ${d.qty}张 @ ${d.price}` : `❌ ${d.error}`);
    } catch { flash('网络错误'); }
  }

  async function closeNow(sym) {
    if (!window.confirm(`确认市价平掉 ${sym} 的全部持仓？`)) return;
    try {
      const r = await fetch(`${API}/api/trade/close`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: sym }),
      });
      const d = await r.json();
      flash(d.ok ? `${sym} 已平仓` : d.error);
    } catch { flash('网络错误'); }
  }

  // ── 品种列表操作 ──
  async function patchSymbol(sym, body, note) {
    try {
      const r = await fetch(`${API}/api/trade/symbols`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: sym, ...body }),
      });
      const d = await r.json();
      if (d.ok) { setSymbolCfgs(d.symbols); flash(note || '已保存'); }
      else flash(d.error || '保存失败');
      return d.ok;
    } catch { flash('网络错误'); return false; }
  }

  async function removeSymbol(sym) {
    if (!window.confirm(`确认把 ${sym} 移出交易列表？`)) return;
    try {
      const r = await fetch(`${API}/api/trade/symbols/${encodeURIComponent(sym)}`,
                            { method: 'DELETE' });
      const d = await r.json();
      if (d.ok) { setSymbolCfgs(d.symbols); flash(`${sym} 已移除`); }
      else flash(d.error || '移除失败');
    } catch { flash('网络错误'); }
  }

  if (!cfg) return <div style={sty.empty}>加载中…</div>;

  if (!cfg.configured) {
    return (
      <div style={sty.wrap}>
        <div style={sty.warn}>
          <b style={{ color: '#f5a623' }}>未配置 OKX API 密钥</b>
          <div style={{ marginTop: 6, lineHeight: 1.8, color: '#8b93a0' }}>
            自动挂单不可用，信号仍会正常提醒 + 推送。启用步骤：
          </div>
          <ol style={{ margin: '8px 0 0 16px', lineHeight: 1.9, color: '#8b93a0' }}>
            <li>项目根目录 <code style={sty.inline}>cp .env.example .env</code></li>
            <li>把 .env 里的 key / secret / passphrase 换成你自己的</li>
            <li>OKX 模拟盘 key：交易 → 模拟交易 → 个人中心 → 模拟盘API</li>
            <li>重新 <code style={sty.inline}>bash start.sh</code></li>
          </ol>
          <pre style={sty.code}>{`OKX_API_KEY=你的key
OKX_API_SECRET=你的secret
OKX_API_PASSPHRASE=你的密码
OKX_SIMULATED=1     # 1=模拟盘 0=实盘`}</pre>
        </div>
      </div>
    );
  }

  const live = !cfg.paper;
  const swap = cfg.category === 'SWAP';

  return (
    <div style={sty.wrap}>
      {live && cfg.enabled && (
        <div style={sty.liveBanner}>
          ⚠️ 实盘模式已开启 · 信号触发将使用<b>真实资金</b>下单
        </div>
      )}

      {/* ── 当前持仓（全品种） ── */}
      {posList.map(([sym, pos]) => (
        <div key={sym}
             style={{ ...sty.card, borderColor: pos.side === 'long' ? '#00c9a7' : '#c2185b' }}>
          <div style={sty.rowBetween}>
            <span style={{ fontSize: 13, fontWeight: 800,
                           color: pos.side === 'long' ? '#00c9a7' : '#e05263' }}>
              {pos.side === 'long' ? '▲ 持多' : '▼ 持空'}
              <span style={{ fontSize: 10, color: '#c8ccd4', marginLeft: 6, fontWeight: 700 }}>
                {sym}
              </span>
              <span style={{ fontSize: 10, color: '#8b93a0', marginLeft: 6, fontWeight: 400 }}>
                {pos.tf} · {pos.leverage}x
                {pos.profile === 'quick' && (
                  <span style={{ color: '#f5a623', marginLeft: 4 }}>快进快出</span>
                )}
              </span>
            </span>
            <span style={{
              fontSize: 15, fontWeight: 800, fontFamily: 'var(--font-mono)',
              color: (pos.roe_pct ?? 0) >= 0 ? '#00c9a7' : '#e05263',
            }}>
              {(pos.roe_pct ?? 0) > 0 ? '+' : ''}{pos.roe_pct ?? 0}%
            </span>
          </div>
          <div style={sty.posGrid}>
            <Mini k="开仓价" v={fmt.price(pos.entry)} />
            <Mini k="现价" v={fmt.price(pos.price)} />
            <Mini k="止损" v={fmt.price(pos.stop)}
                  color={pos.breakeven ? '#00c9a7' : '#f5a623'}
                  sub={pos.breakeven ? '已保本' : undefined} />
            <Mini k={swap ? '剩余(张)' : '剩余'} v={pos.qty}
                  sub={pos.tp1_done ? `已止盈${Math.round((1 - pos.qty / pos.init_qty) * 100)}%` : '未止盈'} />
            <Mini k="价格幅度" v={`${(pos.pnl_pct ?? 0) > 0 ? '+' : ''}${pos.pnl_pct ?? 0}%`}
                  color={(pos.pnl_pct ?? 0) >= 0 ? '#00c9a7' : '#e05263'} />
            <Mini k="浮动盈亏" v={`${(pos.float_pnl ?? 0) > 0 ? '+' : ''}${pos.float_pnl ?? 0}U`}
                  color={(pos.float_pnl ?? 0) >= 0 ? '#00c9a7' : '#e05263'}
                  sub={pos.realized ? `已实现 ${pos.realized > 0 ? '+' : ''}${pos.realized}U` : undefined} />
          </div>
          {/* 仓位动作流水：开仓/止盈/保本/移动止损 */}
          {(pos.events || []).length > 0 && (
            <div style={sty.events}>
              {pos.events.slice(-4).map((e, i) => (
                <div key={i} style={{ fontSize: 9.5, color: EVT_COLOR[e.kind] || '#5a6270' }}>
                  · {e.msg}
                </div>
              ))}
            </div>
          )}
          <button onClick={() => closeNow(sym)}
                  style={{ ...sty.btn, borderColor: '#c2185b', color: '#e05263' }}>
            市价全平 {sym}
          </button>
        </div>
      ))}

      {/* ── 总开关 ── */}
      <div style={{ ...sty.card, borderColor: cfg.enabled ? (live ? '#c2185b' : '#00c9a7') : '#262626' }}>
        <div style={sty.rowBetween}>
          <div>
            <div style={{ fontSize: 12, fontWeight: 700 }}>自动挂单</div>
            <div style={{ fontSize: 9.5, color: '#4a5058' }}>
              {cfg.enabled ? '信号触发时自动挂限价单' : '仅提醒，不下单'}
            </div>
          </div>
          <Toggle on={cfg.enabled} color={live ? '#c2185b' : '#00c9a7'}
                  onClick={() => patch({ enabled: !cfg.enabled },
                    !cfg.enabled ? '自动挂单已开启' : '自动挂单已关闭')} />
        </div>

        <div style={{ ...sty.rowBetween, borderTop: '1px solid #1e1e1e', paddingTop: 8 }}>
          <div>
            <div style={{ fontSize: 12, fontWeight: 700, color: live ? '#e05263' : '#00c9a7' }}>
              {live ? '⚠️ 实盘' : '模拟盘'}
            </div>
            <div style={{ fontSize: 9.5, color: '#4a5058' }}>
              {live ? '真实资金' : 'OKX 模拟资金，无风险'}
            </div>
          </div>
          {live ? (
            <button onClick={() => { patch({ paper: true }, '已切回模拟盘'); setConfirmLive(false); }}
                    style={{ ...sty.smallBtn, borderColor: '#00c9a7', color: '#00c9a7' }}>
              切回模拟盘
            </button>
          ) : confirmLive ? (
            <div style={{ display: 'flex', gap: 4 }}>
              <button onClick={() => { patch({ paper: false }, '⚠️ 已切到实盘'); setConfirmLive(false); }}
                      style={{ ...sty.smallBtn, borderColor: '#c2185b', color: '#e05263', background: '#8b000033' }}>
                确认实盘
              </button>
              <button onClick={() => setConfirmLive(false)} style={sty.smallBtn}>取消</button>
            </div>
          ) : (
            <button onClick={() => setConfirmLive(true)} style={sty.smallBtn}>切到实盘</button>
          )}
        </div>
        {confirmLive && (
          <div style={{ fontSize: 10, color: '#e05263', lineHeight: 1.7 }}>
            实盘每次触发都用真实资金。确认前逐个核对下方「交易品种」里
            各品种的保证金 × 杠杆、允许周期和品种开关。
          </div>
        )}
      </div>

      {/* ── 交易品种（多品种并行） ── */}
      <Section title={`交易品种（${symbolCfgs.length}/${maxSymbols}）`}>
        <div style={{ fontSize: 9.5, color: '#5a6270', lineHeight: 1.7 }}>
          每个品种独立设置保证金 / 杠杆 / 允许周期 / 指标参数 / ER 阈值 / 止盈止损规则，独立开关；
          品种开关 × 上方总开关同时打开才会下单。
        </div>
        {symbolCfgs.map((c) => (
          <SymbolRow key={c.symbol} c={c} swap={swap}
                     last={tickers[c.symbol]?.last ?? c.last}
                     hasPos={!!positions[c.symbol]}
                     onPatch={(body, note) => patchSymbol(c.symbol, body, note)}
                     onRemove={() => removeSymbol(c.symbol)} />
        ))}
        <AddSymbol disabled={symbolCfgs.length >= maxSymbols}
                   max={maxSymbols}
                   onAdd={(sym) => patchSymbol(sym, {}, `${sym} 已加入，正在拉历史K线…`)} />
      </Section>

      {/* ── 下单参数（全局默认） ── */}
      <Section title="下单参数（新品种默认值）">
        <Row label="交易品类" hint={swap ? '永续合约，可做多做空' : '现货，只能做多'}>
          <div style={{ display: 'flex', gap: 3 }}>
            {[['SWAP', '合约'], ['SPOT', '现货']].map(([v, l]) => (
              <button key={v} onClick={() => patch({ category: v }, `已切换到${l}`)}
                      style={{ ...sty.chip, opacity: cfg.category === v ? 1 : 0.3 }}>{l}</button>
            ))}
          </div>
        </Row>

        {swap && (
          <>
            <Row label="杠杆倍数" hint={`名义价值 = 保证金 × ${cfg.leverage}`}>
              <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                {LEVERAGES.map((l) => (
                  <button key={l} onClick={() => patch({ leverage: l }, `杠杆改为 ${l}x`)}
                          style={{ ...sty.chip, opacity: cfg.leverage === l ? 1 : 0.3 }}>
                    {l}x
                  </button>
                ))}
              </div>
            </Row>
            <Row label="保证金模式" hint="cross 全仓 / isolated 逐仓">
              <div style={{ display: 'flex', gap: 3 }}>
                {[['cross', '全仓'], ['isolated', '逐仓']].map(([v, l]) => (
                  <button key={v} onClick={() => patch({ margin_mode: v }, `已改为${l}`)}
                          style={{ ...sty.chip, opacity: cfg.margin_mode === v ? 1 : 0.3 }}>{l}</button>
                ))}
              </div>
            </Row>
          </>
        )}

        <Row label="每笔保证金" hint="新品种默认固定金额；各品种可改成净值百分比">
          <div style={{ display: 'flex', gap: 4 }}>
            <input type="number" min={1} step={1} value={amount}
                   onChange={(e) => setAmount(+e.target.value)} style={sty.input} />
            <button onClick={() => patch({ amount_usdt: amount }, `每笔改为 ${amount} USDT`)}
                    disabled={amount === cfg.amount_usdt}
                    style={{ ...sty.smallBtn, opacity: amount === cfg.amount_usdt ? 0.3 : 1 }}>改</button>
          </div>
        </Row>
        <Row label="追价偏移 %" hint="买单略高于现价、卖单略低于现价，IOC 立刻成交；吃不到自动撤，不记持仓">
          <div style={{ display: 'flex', gap: 4 }}>
            <input type="number" min={0} max={2} step={0.01} value={offset}
                   onChange={(e) => setOffset(+e.target.value)} style={sty.input} />
            <button onClick={() => patch({ price_offset: offset }, `偏移改为 ${offset}%`)}
                    disabled={offset === cfg.price_offset}
                    style={{ ...sty.smallBtn, opacity: offset === cfg.price_offset ? 0.3 : 1 }}>改</button>
          </div>
        </Row>
      </Section>

      {/* ── 止盈止损 · 标准档 ── */}
      {rules && (
        <Section title="止盈止损 · 标准档（ER ≥ 标准线）">
          <div style={sty.rulesFlow}>
            开仓 → 浮盈 <b style={{ color: '#00c9a7' }}>{rules.tp1_pct}%</b> 止盈{' '}
            <b style={{ color: '#00c9a7' }}>{rules.tp1_ratio}%</b> 仓位
            {rules.move_sl_to_entry && ' → 止损抬到开仓价（保本）'}
            {rules.tp1_ratio < 100 && (
              <>
                {' → 剩余 '}<b style={{ color: '#00c9a7' }}>{(100 - rules.tp1_ratio).toFixed(0)}%</b>
                {' 等反向信号全平'}
              </>
            )}
          </div>

          <div style={sty.rowBetween}>
            <span style={{ fontSize: 11, color: '#c8ccd4' }}>启用止盈止损</span>
            <Toggle on={rules.enabled} onClick={() => patchRules({ enabled: !rules.enabled })} />
          </div>

          <Row label="止盈触发" hint={swap ? `价格幅度，${cfg.leverage}x 下 = 保证金 ${(tp1 * cfg.leverage).toFixed(1)}%` : '价格涨幅 %'}>
            <div style={{ display: 'flex', gap: 4 }}>
              <input type="number" min={0.1} step={0.1} value={tp1}
                     onChange={(e) => setTp1(+e.target.value)} style={sty.input} />
              <button onClick={() => patchRules({ tp1_pct: tp1 }, `止盈线改为 ${tp1}%`)}
                      disabled={tp1 === rules.tp1_pct}
                      style={{ ...sty.smallBtn, opacity: tp1 === rules.tp1_pct ? 0.3 : 1 }}>改</button>
            </div>
          </Row>
          <Row label="止盈比例 %" hint="触发时平掉多少仓位">
            <div style={{ display: 'flex', gap: 4 }}>
              <input type="number" min={1} max={100} step={5} value={tpRatio}
                     onChange={(e) => setTpRatio(+e.target.value)} style={sty.input} />
              <button onClick={() => patchRules({ tp1_ratio: tpRatio }, `止盈比例改为 ${tpRatio}%`)}
                      disabled={tpRatio === rules.tp1_ratio}
                      style={{ ...sty.smallBtn, opacity: tpRatio === rules.tp1_ratio ? 0.3 : 1 }}>改</button>
            </div>
          </Row>
          <div style={sty.rowBetween}>
            <div>
              <div style={{ fontSize: 11, color: '#c8ccd4' }}>止盈后止损抬到开仓价</div>
              <div style={{ fontSize: 8.5, color: '#4a5058' }}>剩余仓位变成无风险持有</div>
            </div>
            <Toggle on={rules.move_sl_to_entry}
                    onClick={() => patchRules({ move_sl_to_entry: !rules.move_sl_to_entry })} />
          </div>

          <Row label="初始止损" hint={rules.sl_mode === 'st' ? '用超趋线（推荐）' : '按开仓价百分比'}>
            <div style={{ display: 'flex', gap: 3 }}>
              {[['st', '超趋线'], ['pct', '百分比']].map(([v, l]) => (
                <button key={v} onClick={() => patchRules({ sl_mode: v }, `止损改为${l}`)}
                        style={{ ...sty.chip, opacity: rules.sl_mode === v ? 1 : 0.3 }}>{l}</button>
              ))}
            </div>
          </Row>
          {rules.sl_mode === 'pct' && (
            <Row label="止损幅度 %" hint="距开仓价">
              <div style={{ display: 'flex', gap: 4 }}>
                <input type="number" min={0.1} step={0.1} value={slPct}
                       onChange={(e) => setSlPct(+e.target.value)} style={sty.input} />
                <button onClick={() => patchRules({ sl_pct: slPct }, `止损改为 ${slPct}%`)}
                        disabled={slPct === rules.sl_pct}
                        style={{ ...sty.smallBtn, opacity: slPct === rules.sl_pct ? 0.3 : 1 }}>改</button>
              </div>
            </Row>
          )}
          <div style={sty.rowBetween}>
            <div>
              <div style={{ fontSize: 11, color: '#c8ccd4' }}>跟随超趋线移动止损</div>
              <div style={{ fontSize: 8.5, color: '#4a5058' }}>只朝有利方向移，锁住利润</div>
            </div>
            <Toggle on={rules.trail_with_st}
                    onClick={() => patchRules({ trail_with_st: !rules.trail_with_st })} />
          </div>
        </Section>
      )}

      {/* ── 止盈止损 · 弱档（快进快出） ── */}
      {quick && cfg && (
        <Section title="弱档 · 快进快出（震荡边缘）">
          <div style={{ fontSize: 9.5, color: '#5a6270', lineHeight: 1.7 }}>
            ER 在 <b style={{ color: '#8b93a0' }}>{cfg.er_weak_min}</b> ~{' '}
            <b style={{ color: '#8b93a0' }}>{cfg.er_min}</b> 的信号走这一档：
            止盈一到<b style={{ color: '#f5a623' }}>全部平掉</b>。
            吃不到大波段的行情就赚快钱。
          </div>

          <div style={sty.rowBetween}>
            <div>
              <div style={{ fontSize: 11, color: '#c8ccd4' }}>弱档自动下单</div>
              <div style={{ fontSize: 8.5, color: cfg.quick_enabled ? '#f5a623' : '#4a5058' }}>
                {cfg.quick_enabled
                  ? `已开启 —— ER ${cfg.er_weak_min}~${cfg.er_min} 的信号也会真实下单`
                  : `未开启 —— 该区间信号只提醒、不下单`}
              </div>
            </div>
            <Toggle on={!!cfg.quick_enabled}
                    onClick={() => patch({ quick_enabled: !cfg.quick_enabled },
                                         cfg.quick_enabled ? '弱档下单已关闭' : '⚠️ 弱档下单已开启')} />
          </div>

          <div style={{ opacity: cfg.quick_enabled ? 1 : 0.4,
                        pointerEvents: cfg.quick_enabled ? 'auto' : 'none',
                        display: 'flex', flexDirection: 'column', gap: 6 }}>
            <Row label="止盈 %" hint={swap ? `全平。${cfg.leverage}x 下 = 保证金 ${(qTp * cfg.leverage).toFixed(1)}%` : '价格幅度，触发即全平'}>
              <div style={{ display: 'flex', gap: 4 }}>
                <input type="number" min={0.1} step={0.1} value={qTp}
                       onChange={(e) => setQTp(+e.target.value)} style={sty.input} />
                <button onClick={() => patchRules({ profile: 'quick', tp1_pct: qTp }, `弱档止盈改为 ${qTp}%`)}
                        disabled={qTp === quick.tp1_pct}
                        style={{ ...sty.smallBtn, opacity: qTp === quick.tp1_pct ? 0.3 : 1 }}>改</button>
              </div>
            </Row>
            <Row label="止损方式" hint={quick.sl_mode === 'st'
                ? '用超趋线 —— 离开仓价通常比固定百分比远，破线即趋势翻转'
                : '距开仓价固定百分比（快进快出默认）'}>
              <div style={{ display: 'flex', gap: 3 }}>
                {[['pct', '百分比'], ['st', '超趋线']].map(([v, l]) => (
                  <button key={v}
                          onClick={() => patchRules({ profile: 'quick', sl_mode: v }, `弱档止损改为${l}`)}
                          style={{ ...sty.chip, opacity: quick.sl_mode === v ? 1 : 0.3 }}>{l}</button>
                ))}
              </div>
            </Row>
            {quick.sl_mode === 'pct' && (
              <Row label="止损 %" hint="距开仓价固定">
                <div style={{ display: 'flex', gap: 4 }}>
                  <input type="number" min={0.1} step={0.1} value={qSl}
                         onChange={(e) => setQSl(+e.target.value)} style={sty.input} />
                  <button onClick={() => patchRules({ profile: 'quick', sl_pct: qSl }, `弱档止损改为 ${qSl}%`)}
                          disabled={qSl === quick.sl_pct}
                          style={{ ...sty.smallBtn, opacity: qSl === quick.sl_pct ? 0.3 : 1 }}>改</button>
                </div>
              </Row>
            )}
            {quick.sl_mode === 'st' && (
              <div style={sty.rowBetween}>
                <div>
                  <div style={{ fontSize: 11, color: '#c8ccd4' }}>跟随超趋线移动止损</div>
                  <div style={{ fontSize: 8.5, color: '#4a5058' }}>
                    关闭则用开仓那一刻的趋势线，之后不动
                  </div>
                </div>
                <Toggle on={quick.trail_with_st}
                        onClick={() => patchRules({ profile: 'quick', trail_with_st: !quick.trail_with_st })} />
              </div>
            )}
            <div style={{ fontSize: 9, color: '#3f4650', lineHeight: 1.6 }}>
              {quick.sl_mode === 'pct'
                ? <>盈亏比 {qSl > 0 ? (qTp / qSl).toFixed(2) : '—'} : 1 —— 靠胜率赚钱。</>
                : <>超趋线止损时盈亏比不固定（取决于开仓时离线多远）。</>}
              {' '}止盈比例 / 保本已按「快进快出」定义写死（100% / 关）。
            </div>
          </div>
        </Section>
      )}

      {/* ── 闸门 ── */}
      <Section title="下单闸门（全部满足才挂单）">
        <div style={{ ...sty.gateCard, borderColor: regime?.tradable ? '#00c9a755' : '#f5a62355' }}>
          <div style={sty.rowBetween}>
            <span style={{ fontSize: 11, fontWeight: 700,
                           color: regime?.tradable ? '#00c9a7' : '#f5a623' }}>
              {regime?.tradable ? '✓ ' : '⚠ '}{regime?.label || '—'}（{tf}）
            </span>
            <span style={{ fontSize: 10, color: '#8b93a0', fontFamily: 'var(--font-mono)' }}>
              ER {regime?.er ?? '—'}
            </span>
          </div>
          <div style={{ fontSize: 9.5, color: '#5a6270', lineHeight: 1.7, marginTop: 4 }}>
            效率比 ER = 净位移 / 路径长度。图表品种当前 ER 档位以此展示；
            每个品种独立配置 ER 阈值，在「交易品种」展开编辑。
          </div>
        </div>

        <Row label="允许等级" hint="C 级为逆 Bias 信号">
          <div style={{ display: 'flex', gap: 3 }}>
            {['A', 'B', 'C'].map((g) => {
              const grades = cfg.allow_grades || [];
              const on = grades.includes(g);
              return (
                <button key={g} onClick={() => patch({
                  allow_grades: on ? grades.filter((x) => x !== g) : [...grades, g],
                })} style={{ ...sty.chip, opacity: on ? 1 : 0.3 }}>{g}</button>
              );
            })}
          </div>
        </Row>
        <Row label="最低强度" hint="翻转当根的质量 0~3">
          <div style={{ display: 'flex', gap: 3 }}>
            {[0, 1, 2, 3].map((n) => (
              <button key={n} onClick={() => patch({ min_score: n })}
                      style={{ ...sty.chip, opacity: cfg.min_score === n ? 1 : 0.3 }}>{n}</button>
            ))}
          </div>
        </Row>
        <div style={{ fontSize: 9, color: '#3f4650', lineHeight: 1.6, padding: '2px 0' }}>
          ER 阈值、允许周期、止盈止损已移到「交易品种」里按品种单独设置。
        </div>
      </Section>

      {/* ── 自检 ── */}
      <Section title="连通性自检">
        <div style={{ display: 'flex', gap: 5 }}>
          <button onClick={doPing} style={{ ...sty.btn, flex: 1 }}>查账户</button>
          <button onClick={testOrder} style={sty.btn}>测试单</button>
        </div>
        {ping && (
          <div style={{ fontSize: 10, color: ping.ok ? '#00c9a7' : '#e05263', lineHeight: 1.7 }}>
            {ping.loading ? '查询中…'
              : ping.ok ? `✅ 密钥有效 · ${ping.paper ? '模拟盘' : '⚠️ 实盘'} · 权益 ${ping.equity ?? '—'} USDT`
              : `❌ ${ping.error}`}
          </div>
        )}
        <div style={{ fontSize: 9, color: '#3f4650', lineHeight: 1.7 }}>
          测试单挂在盘口 ±3%，正常不成交，可在交易所撤单；不进入持仓管理。
        </div>
      </Section>

      {msg && (
        <div style={sty.toast}>{msg}</div>
      )}

      {/* ── 订单记录 ── */}
      <Section title={`挂单记录（${orders.length}）`}>
        {!orders.length && (
          <div style={{ fontSize: 10, color: '#4a5058', padding: '8px 0' }}>暂无挂单记录</div>
        )}
        {orders.slice().reverse().slice(0, 20).map((o, i) => (
          <div key={i} style={{
            ...sty.order,
            borderLeftColor: !o.ok ? '#5a6270'
              : o.kind === 'tp1' ? '#f5a623'
              : o.kind === 'close' ? '#8b93a0'
              : o.sig_type === 'buy' ? '#00c9a7' : '#c2185b',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <span style={{ fontSize: 10, fontWeight: 800, color: KIND_COLOR[o.kind] || '#8b93a0' }}>
                {KIND_LABEL[o.kind] || o.kind || '—'}
              </span>
              {(o.sym || o.symbol) && (
                <span style={{ ...sty.tag, color: '#c8ccd4' }}>{o.sym || o.symbol}</span>
              )}
              <span style={sty.tag}>{o.tf}</span>
              {o.grade && <span style={sty.tag}>{o.grade}</span>}
              {o.leverage > 1 && <span style={sty.tag}>{o.leverage}x</span>}
              <span style={{ ...sty.tag, color: o.paper ? '#8b93a0' : '#e05263' }}>
                {o.paper ? '模拟' : '实盘'}
              </span>
              <span style={{ flex: 1 }} />
              <span style={{ fontSize: 9, color: '#4a5058' }}>{fmt.datetime(o.ts)}</span>
            </div>
            {o.ok ? (
              <div style={{ fontSize: 10, color: '#c8ccd4', fontFamily: 'var(--font-mono)' }}>
                {fmt.price(o.price)} × {o.qty}{o.category === 'SWAP' ? '张' : ''}
                {o.notional ? ` ≈ ${o.notional}U` : ''}
                {o.kind === 'open' && (
                  <span style={{ ...sty.tag, marginLeft: 6,
                                  color: o.fill_confirmed ? '#00c9a7' : '#f5a623' }}>
                    {o.fill_confirmed ? '已成交' : '成交未确认'}
                  </span>
                )}
              </div>
            ) : (
              <div style={{ fontSize: 10, color: '#e05263' }}>
                {String(o.error || '').includes('未成交') ? '未成交已撤：' : '失败：'}{o.error}
              </div>
            )}
            {o.reason && <div style={{ fontSize: 9, color: '#5a6270' }}>{o.reason}</div>}
          </div>
        ))}
      </Section>

      {/* ── 已平仓 ── */}
      {closed?.length > 0 && (
        <Section title={`已平仓（${closed.length}）`}>
          {closed.slice().reverse().slice(0, 10).map((c, i) => (
            <div key={i} style={{ ...sty.order, borderLeftColor: c.realized >= 0 ? '#00c9a7' : '#c2185b' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <span style={{ fontSize: 10, fontWeight: 700,
                               color: c.side === 'long' ? '#00c9a7' : '#e05263' }}>
                  {c.side === 'long' ? '多' : '空'}
                </span>
                {(c.sym || c.symbol) && (
                  <span style={{ ...sty.tag, color: '#c8ccd4' }}>{c.sym || c.symbol}</span>
                )}
                <span style={sty.tag}>{c.tf}</span>
                <span style={sty.tag}>{c.leverage}x</span>
                <span style={{ flex: 1 }} />
                <span style={{
                  fontSize: 11, fontWeight: 800, fontFamily: 'var(--font-mono)',
                  color: c.realized >= 0 ? '#00c9a7' : '#e05263',
                }}>
                  {c.realized > 0 ? '+' : ''}{c.realized}U
                </span>
              </div>
              <div style={{ fontSize: 9.5, color: '#5a6270', fontFamily: 'var(--font-mono)' }}>
                {fmt.price(c.entry)} → {fmt.price(c.price)}
              </div>
            </div>
          ))}
        </Section>
      )}
    </div>
  );
}

/** 用最新价重算持仓的浮动盈亏（与后端 position.py 的算法一致）。 */
function withLivePnl(p, price) {
  const long = p.side === 'long';
  const raw = ((price - p.entry) / p.entry) * 100;
  const pnl = long ? raw : -raw;
  const coins = p.qty * (p.ct_val || 1);
  const d = (price - p.entry) * coins;
  return {
    ...p,
    price,
    pnl_pct: +pnl.toFixed(2),
    roe_pct: +(pnl * Math.max(1, p.leverage)).toFixed(2),
    float_pnl: +(long ? d : -d).toFixed(4),
  };
}

const KIND_LABEL = { open: '开仓', tp1: '止盈', close: '平仓', test: '测试' };
const KIND_COLOR = { open: '#00c9a7', tp1: '#f5a623', close: '#8b93a0', test: '#6c7480' };
const EVT_COLOR = {
  open: '#8b93a0', tp1: '#f5a623', breakeven: '#00c9a7',
  trail: '#4e8aff', close: '#8b93a0',
};

function Section({ title, children }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <div style={sty.secTitle}>{title}</div>
      {children}
    </div>
  );
}

function Row({ label, hint, children }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0' }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 11, color: '#c8ccd4' }}>{label}</div>
        {hint && <div style={{ fontSize: 8.5, color: '#4a5058' }}>{hint}</div>}
      </div>
      {children}
    </div>
  );
}

function Mini({ k, v, sub, color }) {
  return (
    <div>
      <div style={{ fontSize: 8.5, color: '#4a5058' }}>{k}</div>
      <div style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: color || '#e9ecef' }}>{v}</div>
      {sub && <div style={{ fontSize: 8, color: '#4a5058' }}>{sub}</div>}
    </div>
  );
}

/** 交易品种行：一行摘要 + 展开后编辑保证金/杠杆/周期/指标参数/ER阈值/止盈止损。 */
function SymbolRow({ c, swap, last, hasPos, onPatch, onRemove }) {
  const [open, setOpen] = useState(false);
  const [margin, setMargin] = useState(c.margin_usdt);
  const [sizingMode, setSizingMode] = useState(c.sizing_mode || 'fixed');
  const [eqPct, setEqPct] = useState(c.equity_pct ?? 10);
  const [periods, setPeriods] = useState(c.params?.periods ?? 15);
  const [mult, setMult] = useState(c.params?.multiplier ?? 9.1);
  // ER 阈值
  const [erHide, setErHide] = useState(c.er_hide_below ?? 0.10);
  const [erWeakMin, setErWeakMin] = useState(c.er_weak_min ?? 0.12);
  const [erMin, setErMin] = useState(c.er_min ?? 0.15);
  // 组合过滤器
  const [atrOn, setAtrOn] = useState(c.atr_filter_enabled ?? false);
  const [atrMin, setAtrMin] = useState(c.atr_vol_min ?? 0.7);
  const [rangeOn, setRangeOn] = useState(c.range_filter_enabled ?? false);
  const [rangeMax, setRangeMax] = useState(c.range_size_max ?? 0.15);
  const [rangeTouches, setRangeTouches] = useState(c.range_touches_min ?? 3);
  const [mtfOn, setMtfOn] = useState(c.mtf_filter_enabled ?? false);
  const [mtfMin, setMtfMin] = useState(c.mtf_consistency_min ?? 0.6);
  const [mtfFlipMax, setMtfFlipMax] = useState(c.mtf_flip_max ?? 5);
  const [adxOn, setAdxOn] = useState(c.adx_filter_enabled ?? false);
  const [adxMin, setAdxMin] = useState(c.adx_min ?? 20);
  const [adxPeriod, setAdxPeriod] = useState(c.adx_period ?? 14);
  // 标准档止盈止损
  const [tp1, setTp1] = useState(c.exit_rules?.tp1_pct ?? 2);
  const [tpRatio, setTpRatio] = useState(c.exit_rules?.tp1_ratio ?? 70);
  const [slPct, setSlPct] = useState(c.exit_rules?.sl_pct ?? 2);
  const [moveSlToEntry, setMoveSlToEntry] = useState(c.exit_rules?.move_sl_to_entry ?? true);
  const [trailWithSt, setTrailWithSt] = useState(c.exit_rules?.trail_with_st ?? true);
  // 快档止盈止损
  const [qTp, setQTp] = useState(c.exit_rules_quick?.tp1_pct ?? 1);
  const [qTpRatio, setQTpRatio] = useState(c.exit_rules_quick?.tp1_ratio ?? 100);
  const [qSl, setQSl] = useState(c.exit_rules_quick?.sl_pct ?? 1);
  const [qMoveSlToEntry, setQMoveSlToEntry] = useState(c.exit_rules_quick?.move_sl_to_entry ?? false);
  const [qTrailWithSt, setQTrailWithSt] = useState(c.exit_rules_quick?.trail_with_st ?? true);

  useEffect(() => { setMargin(c.margin_usdt); }, [c.margin_usdt]);
  useEffect(() => { setSizingMode(c.sizing_mode || 'fixed'); }, [c.sizing_mode]);
  useEffect(() => { setEqPct(c.equity_pct ?? 10); }, [c.equity_pct]);
  useEffect(() => {
    setPeriods(c.params?.periods ?? 15);
    setMult(c.params?.multiplier ?? 9.1);
  }, [c.params?.periods, c.params?.multiplier]);
  useEffect(() => {
    setErHide(c.er_hide_below ?? 0.10);
    setErWeakMin(c.er_weak_min ?? 0.12);
    setErMin(c.er_min ?? 0.15);
  }, [c.er_hide_below, c.er_weak_min, c.er_min]);
  useEffect(() => {
    setAtrOn(c.atr_filter_enabled ?? false);
    setAtrMin(c.atr_vol_min ?? 0.7);
    setRangeOn(c.range_filter_enabled ?? false);
    setRangeMax(c.range_size_max ?? 0.15);
    setRangeTouches(c.range_touches_min ?? 3);
    setMtfOn(c.mtf_filter_enabled ?? false);
    setMtfMin(c.mtf_consistency_min ?? 0.6);
    setMtfFlipMax(c.mtf_flip_max ?? 5);
    setAdxOn(c.adx_filter_enabled ?? false);
    setAdxMin(c.adx_min ?? 20);
    setAdxPeriod(c.adx_period ?? 14);
  }, [c.atr_filter_enabled, c.range_filter_enabled, c.mtf_filter_enabled, c.adx_filter_enabled]);
  useEffect(() => {
    if (!c.exit_rules) return;
    setTp1(c.exit_rules.tp1_pct ?? 2);
    setTpRatio(c.exit_rules.tp1_ratio ?? 70);
    setSlPct(c.exit_rules.sl_pct ?? 2);
    setMoveSlToEntry(c.exit_rules.move_sl_to_entry ?? true);
    setTrailWithSt(c.exit_rules.trail_with_st ?? true);
  }, [c.exit_rules?.tp1_pct, c.exit_rules?.tp1_ratio, c.exit_rules?.sl_pct,
      c.exit_rules?.move_sl_to_entry, c.exit_rules?.trail_with_st]);
  useEffect(() => {
    if (!c.exit_rules_quick) return;
    setQTp(c.exit_rules_quick.tp1_pct ?? 1);
    setQTpRatio(c.exit_rules_quick.tp1_ratio ?? 100);
    setQSl(c.exit_rules_quick.sl_pct ?? 1);
    setQMoveSlToEntry(c.exit_rules_quick.move_sl_to_entry ?? false);
    setQTrailWithSt(c.exit_rules_quick.trail_with_st ?? true);
  }, [c.exit_rules_quick?.tp1_pct, c.exit_rules_quick?.tp1_ratio, c.exit_rules_quick?.sl_pct,
      c.exit_rules_quick?.move_sl_to_entry, c.exit_rules_quick?.trail_with_st]);

  return (
    <div style={{ ...sty.card, padding: '7px 9px', gap: 6,
                  borderColor: c.enabled ? '#00c9a755' : '#262626' }}>
      <div style={sty.rowBetween}>
        <button onClick={() => setOpen(!open)}
                style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0,
                         display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
          <span style={{ fontSize: 9, color: '#5a6270' }}>{open ? '▾' : '▸'}</span>
          <span style={{ fontSize: 11.5, fontWeight: 800, color: '#e9ecef' }}>{c.symbol}</span>
          {hasPos && <span style={{ ...sty.tag, color: '#f5a623' }}>持仓中</span>}
          {!c.history_loaded && <span style={{ ...sty.tag, color: '#4e8aff' }}>加载中…</span>}
        </button>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 10, color: '#8b93a0', fontFamily: 'var(--font-mono)' }}>
            {last ? fmt.price(last) : '—'}
          </span>
          <Toggle on={!!c.enabled}
                  onClick={() => onPatch({ enabled: !c.enabled },
                    c.enabled ? `${c.symbol} 已停用` : `${c.symbol} 已启用`)} />
        </div>
      </div>
      <div style={{ fontSize: 9, color: '#4a5058' }}>
        {c.sizing_mode === 'equity_pct'
          ? `净值 ${c.equity_pct ?? 10}%`
          : `${c.margin_usdt}U`} × {c.leverage}x　·　{(c.allow_tfs || []).join('/')}　·
        参数 {c.params?.periods}×{c.params?.multiplier}　·
        ER {c.er_hide_below ?? 0.10}/{c.er_weak_min ?? 0.12}/{c.er_min ?? 0.15}
      </div>

      {open && (
        <div style={{ borderTop: '1px solid #1e1e1e', paddingTop: 6,
                      display: 'flex', flexDirection: 'column', gap: 6 }}>
          <Row label="仓位大小" hint={sizingMode === 'equity_pct'
            ? '每次开仓用账户净值的 X% 作保证金，不超过 USDT 可用'
            : (swap ? `× ${c.leverage}x = 名义 ${(margin * c.leverage).toFixed(0)}U` : 'USDT')}>
            <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
              {[['fixed', '固定金额'], ['equity_pct', '净值%']].map(([v, l]) => (
                <button key={v}
                        onClick={() => {
                          setSizingMode(v);
                          onPatch({ sizing_mode: v, equity_pct: v === 'equity_pct' ? eqPct : c.equity_pct },
                            v === 'equity_pct' ? `${c.symbol} 改为净值 ${eqPct}%` : `${c.symbol} 改为固定金额`);
                        }}
                        style={{ ...sty.chip, opacity: sizingMode === v ? 1 : 0.3 }}>{l}</button>
              ))}
            </div>
          </Row>
          {sizingMode === 'equity_pct' ? (
            <Row label="净值比例 %" hint={`开仓保证金 = 账户净值 × ${eqPct}%`}>
              <div style={{ display: 'flex', gap: 4, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                {[5, 10, 20, 50, 100].map((n) => (
                  <button key={n} onClick={() => { setEqPct(n); onPatch({ sizing_mode: 'equity_pct', equity_pct: n }, `${c.symbol} 净值比例 ${n}%`); }}
                          style={{ ...sty.chip, fontSize: 10, padding: '2px 7px',
                                   opacity: eqPct === n ? 1 : 0.3 }}>{n}%</button>
                ))}
                <input type="number" min={1} max={100} step={1} value={eqPct}
                       onChange={(e) => setEqPct(+e.target.value)}
                       style={{ ...sty.input, width: 48 }} />
                <button onClick={() => onPatch({ sizing_mode: 'equity_pct', equity_pct: eqPct }, `${c.symbol} 净值比例 ${eqPct}%`)}
                        disabled={eqPct === c.equity_pct && c.sizing_mode === 'equity_pct'}
                        style={{ ...sty.smallBtn, opacity: eqPct === c.equity_pct && c.sizing_mode === 'equity_pct' ? 0.3 : 1 }}>改</button>
              </div>
            </Row>
          ) : (
            <Row label="每笔保证金" hint={swap ? `× ${c.leverage}x = 名义 ${(margin * c.leverage).toFixed(0)}U` : 'USDT'}>
              <div style={{ display: 'flex', gap: 4 }}>
                <input type="number" min={1} step={1} value={margin}
                       onChange={(e) => setMargin(+e.target.value)} style={sty.input} />
                <button onClick={() => onPatch({ sizing_mode: 'fixed', margin_usdt: margin }, `${c.symbol} 保证金改为 ${margin}U`)}
                        disabled={margin === c.margin_usdt && c.sizing_mode !== 'equity_pct'}
                        style={{ ...sty.smallBtn, opacity: margin === c.margin_usdt && c.sizing_mode !== 'equity_pct' ? 0.3 : 1 }}>改</button>
              </div>
            </Row>
          )}
          {swap && (
            <Row label="杠杆倍数">
              <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                {LEVERAGES.map((l) => (
                  <button key={l} onClick={() => onPatch({ leverage: l }, `${c.symbol} 杠杆改为 ${l}x`)}
                          style={{ ...sty.chip, opacity: c.leverage === l ? 1 : 0.3 }}>{l}x</button>
                ))}
              </div>
            </Row>
          )}
          <div style={{ padding: '2px 0' }}>
            <div style={{ fontSize: 11, color: '#c8ccd4', marginBottom: 4 }}>允许周期</div>
            <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap' }}>
              {ALL_TFS.map((t) => {
                const on = (c.allow_tfs || []).includes(t);
                return (
                  <button key={t} onClick={() => onPatch({
                    allow_tfs: on ? c.allow_tfs.filter((x) => x !== t) : [...c.allow_tfs, t],
                  })} style={{ ...sty.chip, opacity: on ? 1 : 0.25, fontSize: 9.5, padding: '2px 6px' }}>
                    {t}
                  </button>
                );
              })}
            </div>
          </div>
          <Row label="指标参数" hint="ATR 周期 × 倍数，换品种通常要用「参数寻优」重调">
            <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
              <input type="number" min={1} step={1} value={periods}
                     onChange={(e) => setPeriods(+e.target.value)}
                     style={{ ...sty.input, width: 42 }} />
              <span style={{ fontSize: 10, color: '#4a5058' }}>×</span>
              <input type="number" min={0.1} step={0.1} value={mult}
                     onChange={(e) => setMult(+e.target.value)}
                     style={{ ...sty.input, width: 48 }} />
            </div>
          </Row>
          <Row label="ER 阈值" hint="隐藏/弱档/标准档。交易周期上强度够的翻转：图上有箭头就会下单">
            <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
              <input type="number" min={0} max={1} step={0.01} value={erHide}
                     onChange={(e) => setErHide(+e.target.value)}
                     style={{ ...sty.input, width: 54 }} placeholder="隐藏" />
              <input type="number" min={0} max={1} step={0.01} value={erWeakMin}
                     onChange={(e) => setErWeakMin(+e.target.value)}
                     style={{ ...sty.input, width: 54 }} placeholder="弱档" />
              <input type="number" min={0} max={1} step={0.01} value={erMin}
                     onChange={(e) => setErMin(+e.target.value)}
                     style={{ ...sty.input, width: 54 }} placeholder="标准" />
            </div>
          </Row>

          {/* ── 组合过滤器 ── */}
          <div style={{ borderTop: '1px solid #1e1e1e', paddingTop: 6 }}>
            <div style={{ fontSize: 10, fontWeight: 800, color: '#4e8aff', letterSpacing: 0.4, marginBottom: 6 }}>
              ▸ 组合震荡过滤器
            </div>
            <div style={{ fontSize: 9, color: '#5a6270', lineHeight: 1.7, marginBottom: 6 }}>
              叠加在 ER 之上的额外过滤维度，各品种独立开关和阈值。
              全部关闭则退回仅 ER 过滤的原有逻辑。
            </div>

            {/* ATR 波动率过滤 */}
            <div style={sty.filterBlock}>
              <div style={sty.rowBetween}>
                <div>
                  <div style={{ fontSize: 10.5, color: '#c8ccd4', fontWeight: 700 }}>ATR 波动率过滤</div>
                  <div style={{ fontSize: 8.5, color: '#4a5058' }}>
                    当前ATR / 近期均值 &lt; 阈值 → 波动萎缩，拦截信号
                  </div>
                </div>
                <Toggle on={atrOn} onClick={() => setAtrOn(!atrOn)} color="#4e8aff" />
              </div>
              {atrOn && (
                <Row label="ATR比值下限" hint="典型值 0.7，低于此值视为波动萎缩">
                  <div style={{ display: 'flex', gap: 4 }}>
                    <input type="number" min={0.1} max={2} step={0.05} value={atrMin}
                           onChange={(e) => setAtrMin(+e.target.value)} style={{ ...sty.input, width: 58 }} />
                  </div>
                </Row>
              )}
            </div>

            {/* 区间震荡过滤 */}
            <div style={sty.filterBlock}>
              <div style={sty.rowBetween}>
                <div>
                  <div style={{ fontSize: 10.5, color: '#c8ccd4', fontWeight: 700 }}>区间震荡过滤</div>
                  <div style={{ fontSize: 8.5, color: '#4a5058' }}>
                    近期高低点区间小且反复触边 → 横盘震荡，拦截信号
                  </div>
                </div>
                <Toggle on={rangeOn} onClick={() => setRangeOn(!rangeOn)} color="#4e8aff" />
              </div>
              {rangeOn && (
                <>
                  <Row label="区间大小上限 %" hint="高低点差/低点 < 此值才判为区间震荡">
                    <input type="number" min={1} max={50} step={1} value={Math.round(rangeMax * 100)}
                           onChange={(e) => setRangeMax(+e.target.value / 100)}
                           style={{ ...sty.input, width: 58 }} />
                  </Row>
                  <Row label="触边次数下限" hint="近10根触碰高低点次数 ≥ 此值才拦截">
                    <div style={{ display: 'flex', gap: 3 }}>
                      {[2, 3, 4, 5].map((n) => (
                        <button key={n} onClick={() => setRangeTouches(n)}
                                style={{ ...sty.chip, fontSize: 10, padding: '2px 7px',
                                         opacity: rangeTouches === n ? 1 : 0.3 }}>{n}</button>
                      ))}
                    </div>
                  </Row>
                </>
              )}
            </div>

            {/* MTF 一致性过滤 */}
            <div style={sty.filterBlock}>
              <div style={sty.rowBetween}>
                <div>
                  <div style={{ fontSize: 10.5, color: '#c8ccd4', fontWeight: 700 }}>MTF 一致性过滤</div>
                  <div style={{ fontSize: 8.5, color: '#4a5058' }}>
                    多周期ST方向分歧 / 大周期频繁翻转 → 震荡，拦截信号
                  </div>
                </div>
                <Toggle on={mtfOn} onClick={() => setMtfOn(!mtfOn)} color="#4e8aff" />
              </div>
              {mtfOn && (
                <>
                  <Row label="一致性下限" hint="≥60%的周期ST方向一致才通过，典型值 0.6">
                    <div style={{ display: 'flex', gap: 3 }}>
                      {[0.4, 0.5, 0.6, 0.7].map((v) => (
                        <button key={v} onClick={() => setMtfMin(v)}
                                style={{ ...sty.chip, fontSize: 9.5, padding: '2px 6px',
                                         opacity: mtfMin === v ? 1 : 0.3 }}>{v}</button>
                      ))}
                    </div>
                  </Row>
                  <Row label="大周期翻转上限" hint="4h+1d近20根翻转次数 > 此值则拦截">
                    <div style={{ display: 'flex', gap: 3 }}>
                      {[3, 4, 5, 6, 8].map((n) => (
                        <button key={n} onClick={() => setMtfFlipMax(n)}
                                style={{ ...sty.chip, fontSize: 9.5, padding: '2px 6px',
                                         opacity: mtfFlipMax === n ? 1 : 0.3 }}>{n}</button>
                      ))}
                    </div>
                  </Row>
                </>
              )}
            </div>

            {/* ADX 趋势强度过滤 */}
            <div style={sty.filterBlock}>
              <div style={sty.rowBetween}>
                <div>
                  <div style={{ fontSize: 10.5, color: '#c8ccd4', fontWeight: 700 }}>ADX 趋势强度过滤</div>
                  <div style={{ fontSize: 8.5, color: '#4a5058' }}>
                    ADX &lt; 阈值 → 无趋势 / 震荡，拦截信号
                  </div>
                </div>
                <Toggle on={adxOn} onClick={() => setAdxOn(!adxOn)} color="#4e8aff" />
              </div>
              {adxOn && (
                <>
                  <Row label="ADX 下限" hint="典型值 20：&lt;20 无趋势，&gt;25 趋势确认">
                    <div style={{ display: 'flex', gap: 3 }}>
                      {[15, 20, 25, 30].map((n) => (
                        <button key={n} onClick={() => setAdxMin(n)}
                                style={{ ...sty.chip, fontSize: 9.5, padding: '2px 6px',
                                         opacity: adxMin === n ? 1 : 0.3 }}>{n}</button>
                      ))}
                    </div>
                  </Row>
                  <Row label="ADX 周期" hint="Wilder RMA 周期，典型值 14">
                    <div style={{ display: 'flex', gap: 3 }}>
                      {[10, 14, 20].map((n) => (
                        <button key={n} onClick={() => setAdxPeriod(n)}
                                style={{ ...sty.chip, fontSize: 9.5, padding: '2px 6px',
                                         opacity: adxPeriod === n ? 1 : 0.3 }}>{n}</button>
                      ))}
                    </div>
                  </Row>
                </>
              )}
            </div>
          </div>
          <div style={{ padding: '2px 0' }}>
            <div style={{ fontSize: 11, color: '#c8ccd4', marginBottom: 4 }}>标准档止盈止损</div>
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', alignItems: 'center' }}>
              <span style={{ fontSize: 9, color: '#6a7280' }}>TP1</span>
              <input type="number" min={0} step={0.1} value={tp1}
                     onChange={(e) => setTp1(+e.target.value)}
                     style={{ ...sty.input, width: 42 }} />
              <span style={{ fontSize: 9, color: '#6a7280' }}>%×</span>
              <input type="number" min={0} max={100} step={1} value={tpRatio}
                     onChange={(e) => setTpRatio(+e.target.value)}
                     style={{ ...sty.input, width: 42 }} />
              <span style={{ fontSize: 9, color: '#6a7280' }}>%　SL</span>
              <input type="number" min={0} step={0.1} value={slPct}
                     onChange={(e) => setSlPct(+e.target.value)}
                     style={{ ...sty.input, width: 42 }} />
              <span style={{ fontSize: 9, color: '#6a7280' }}>%</span>
              <label style={{ display: 'flex', alignItems: 'center', gap: 3, fontSize: 9, color: '#c8ccd4', cursor: 'pointer' }}>
                <input type="checkbox" checked={moveSlToEntry}
                       onChange={(e) => setMoveSlToEntry(e.target.checked)} />
                保本
              </label>
              <label style={{ display: 'flex', alignItems: 'center', gap: 3, fontSize: 9, color: '#c8ccd4', cursor: 'pointer' }}>
                <input type="checkbox" checked={trailWithSt}
                       onChange={(e) => setTrailWithSt(e.target.checked)} />
                跟踪
              </label>
            </div>
          </div>
          <div style={{ padding: '2px 0' }}>
            <div style={{ fontSize: 11, color: '#c8ccd4', marginBottom: 4 }}>快档止盈止损（弱信号）</div>
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', alignItems: 'center' }}>
              <span style={{ fontSize: 9, color: '#6a7280' }}>TP1</span>
              <input type="number" min={0} step={0.1} value={qTp}
                     onChange={(e) => setQTp(+e.target.value)}
                     style={{ ...sty.input, width: 42 }} />
              <span style={{ fontSize: 9, color: '#6a7280' }}>%×</span>
              <input type="number" min={0} max={100} step={1} value={qTpRatio}
                     onChange={(e) => setQTpRatio(+e.target.value)}
                     style={{ ...sty.input, width: 42 }} />
              <span style={{ fontSize: 9, color: '#6a7280' }}>%　SL</span>
              <input type="number" min={0} step={0.1} value={qSl}
                     onChange={(e) => setQSl(+e.target.value)}
                     style={{ ...sty.input, width: 42 }} />
              <span style={{ fontSize: 9, color: '#6a7280' }}>%</span>
              <label style={{ display: 'flex', alignItems: 'center', gap: 3, fontSize: 9, color: '#c8ccd4', cursor: 'pointer' }}>
                <input type="checkbox" checked={qMoveSlToEntry}
                       onChange={(e) => setQMoveSlToEntry(e.target.checked)} />
                保本
              </label>
              <label style={{ display: 'flex', alignItems: 'center', gap: 3, fontSize: 9, color: '#c8ccd4', cursor: 'pointer' }}>
                <input type="checkbox" checked={qTrailWithSt}
                       onChange={(e) => setQTrailWithSt(e.target.checked)} />
                跟踪
              </label>
            </div>
          </div>
          <button onClick={() => onPatch({
                    margin_usdt: margin,
                    sizing_mode: sizingMode,
                    equity_pct: eqPct,
                    leverage: c.leverage,
                    periods,
                    multiplier: mult,
                    er_hide_below: erHide,
                    er_weak_min: erWeakMin,
                    er_min: erMin,
                    // 组合过滤器
                    atr_filter_enabled: atrOn,
                    atr_vol_min: atrMin,
                    range_filter_enabled: rangeOn,
                    range_size_max: rangeMax,
                    range_touches_min: rangeTouches,
                    mtf_filter_enabled: mtfOn,
                    mtf_consistency_min: mtfMin,
                    mtf_flip_max: mtfFlipMax,
                    adx_filter_enabled: adxOn,
                    adx_min: adxMin,
                    adx_period: adxPeriod,
                    exit_rules: {
                      enabled: true,
                      tp1_pct: tp1,
                      tp1_ratio: tpRatio,
                      move_sl_to_entry: moveSlToEntry,
                      sl_mode: 'st',
                      sl_pct: slPct,
                      trail_with_st: trailWithSt,
                    },
                    exit_rules_quick: {
                      enabled: true,
                      tp1_pct: qTp,
                      tp1_ratio: qTpRatio,
                      move_sl_to_entry: qMoveSlToEntry,
                      sl_mode: 'st',
                      sl_pct: qSl,
                      trail_with_st: qTrailWithSt,
                    },
                  }, `${c.symbol} 配置已更新`)}
                  style={{ ...sty.smallBtn, alignSelf: 'flex-end', borderColor: '#00c9a755', color: '#00c9a7' }}>
            保存所有配置
          </button>
          <button onClick={onRemove} disabled={hasPos}
                  style={{ ...sty.smallBtn, alignSelf: 'flex-end',
                           borderColor: '#c2185b55', color: hasPos ? '#4a5058' : '#e05263' }}>
            {hasPos ? '有持仓，不可移除' : '移出交易列表'}
          </button>
        </div>
      )}
    </div>
  );
}

/** 添加交易品种：输入 instId 提交，后端校验合法性并后台拉历史。 */
function AddSymbol({ disabled, max, onAdd }) {
  const [val, setVal] = useState('');
  const submit = () => {
    const sym = val.trim().toUpperCase();
    if (!sym) return;
    onAdd(sym);
    setVal('');
  };
  if (disabled) {
    return <div style={{ fontSize: 9, color: '#4a5058' }}>已达上限（{max} 个品种）</div>;
  }
  return (
    <div style={{ display: 'flex', gap: 4 }}>
      <input value={val} placeholder="如 SOL-USDT / ETH-USDT-SWAP"
             onChange={(e) => setVal(e.target.value)}
             onKeyDown={(e) => e.key === 'Enter' && submit()}
             style={{ ...sty.input, width: '100%', flex: 1 }} />
      <button onClick={submit} disabled={!val.trim()}
              style={{ ...sty.smallBtn, borderColor: '#00c9a755', color: '#00c9a7',
                       opacity: val.trim() ? 1 : 0.3 }}>
        添加
      </button>
    </div>
  );
}

function Toggle({ on, onClick, color = '#00c9a7' }) {
  return (
    <button onClick={onClick} style={{
      width: 38, height: 20, borderRadius: 10, border: 'none', cursor: 'pointer',
      background: on ? color : '#2c2c2c', position: 'relative',
      transition: 'background .2s', flexShrink: 0,
    }}>
      <span style={{
        position: 'absolute', top: 2.5, left: on ? 21 : 2.5, width: 15, height: 15,
        borderRadius: '50%', background: '#fff', transition: 'left .2s',
      }} />
    </button>
  );
}

const sty = {
  wrap: { padding: '8px 10px 16px', display: 'flex', flexDirection: 'column', gap: 10 },
  empty: { padding: 20, color: 'var(--muted)', fontSize: 11, textAlign: 'center' },
  liveBanner: {
    background: '#8b000044', borderWidth: 1, borderStyle: 'solid', borderColor: '#c2185b',
    borderRadius: 5, padding: '7px 10px', fontSize: 10.5, color: '#ff8a9b', lineHeight: 1.6,
  },
  card: {
    borderWidth: 1, borderStyle: 'solid', borderColor: '#262626', borderRadius: 6,
    padding: '9px 11px', background: '#ffffff05',
    display: 'flex', flexDirection: 'column', gap: 8,
  },
  posGrid: { display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6 },
  events: {
    borderTop: '1px solid #1e1e1e', paddingTop: 5,
    display: 'flex', flexDirection: 'column', gap: 2, lineHeight: 1.6,
  },
  rowBetween: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  secTitle: {
    fontSize: 10, fontWeight: 800, color: '#00c9a7', letterSpacing: 0.4,
    padding: '4px 0 5px', borderBottom: '1px solid #262626', marginBottom: 3,
  },
  rulesFlow: {
    background: '#00c9a70d', borderWidth: 1, borderStyle: 'solid', borderColor: '#00c9a733',
    borderRadius: 5, padding: '7px 9px', fontSize: 9.5, color: '#8b93a0',
    lineHeight: 1.8, marginBottom: 4,
  },
  gateCard: {
    borderWidth: 1, borderStyle: 'solid', borderRadius: 5,
    padding: '7px 9px', background: '#ffffff05', marginBottom: 4,
  },
  input: {
    width: 58, background: '#161616', borderWidth: 1, borderStyle: 'solid',
    borderColor: '#2c2c2c', borderRadius: 3, color: '#e9ecef', fontSize: 11,
    padding: '4px 6px', fontFamily: 'var(--font-mono)',
  },
  smallBtn: {
    background: 'transparent', borderWidth: 1, borderStyle: 'solid', borderColor: '#2c2c2c',
    borderRadius: 3, color: '#8b93a0', fontSize: 10, fontWeight: 700,
    padding: '3px 8px', cursor: 'pointer', whiteSpace: 'nowrap',
  },
  chip: {
    background: '#00c9a71a', borderWidth: 1, borderStyle: 'solid', borderColor: '#00c9a755',
    borderRadius: 3, color: '#00c9a7', fontSize: 10, fontWeight: 700,
    padding: '2px 7px', cursor: 'pointer', transition: 'opacity .15s',
  },
  btn: {
    background: 'transparent', borderWidth: 1, borderStyle: 'solid', borderColor: '#00c9a755',
    borderRadius: 4, color: '#00c9a7', fontSize: 10.5, fontWeight: 700,
    padding: '6px 10px', cursor: 'pointer',
  },
  msg: { fontSize: 10.5, color: '#00c9a7', lineHeight: 1.6 },
  toast: {
    position: 'fixed', bottom: 24, left: '50%', transform: 'translateX(-50%)',
    background: '#1a2a1a', borderWidth: 1, borderStyle: 'solid', borderColor: '#00c9a755',
    borderRadius: 6, padding: '8px 16px', fontSize: 11.5, color: '#00c9a7',
    zIndex: 9999, whiteSpace: 'nowrap', pointerEvents: 'none',
    boxShadow: '0 4px 16px #00000088',
  },
  warn: {
    borderWidth: 1, borderStyle: 'solid', borderColor: '#f5a62355', borderRadius: 6,
    background: '#f5a62310', padding: '10px 12px', fontSize: 10.5,
  },
  inline: { background: '#0a0a0a', padding: '1px 4px', borderRadius: 2, fontSize: 9.5 },
  code: {
    background: '#0a0a0a', borderRadius: 4, padding: '8px 10px', marginTop: 8,
    fontSize: 9, color: '#8b93a0', fontFamily: 'var(--font-mono)',
    whiteSpace: 'pre-wrap', lineHeight: 1.7, overflowX: 'auto',
  },
  order: {
    background: '#ffffff05', borderLeftWidth: 2, borderLeftStyle: 'solid',
    borderLeftColor: 'transparent', borderRadius: 4, padding: '6px 8px',
    display: 'flex', flexDirection: 'column', gap: 3, marginBottom: 4,
  },
  tag: {
    fontSize: 9, color: '#8b93a0', background: '#ffffff0c',
    borderRadius: 3, padding: '1px 5px', fontFamily: 'var(--font-mono)',
  },
  filterBlock: {
    borderWidth: 1, borderStyle: 'solid', borderColor: '#4e8aff22',
    borderRadius: 5, padding: '7px 9px', background: '#4e8aff08',
    display: 'flex', flexDirection: 'column', gap: 5, marginBottom: 5,
  },
};
