"""
OKX 实时行情采集（多品种）

两条 WS：
  public   → tickers（最新价 / 24h 统计）× 所有品种
  business → candle{1m,5m,15m,30m,1H,4H,1D,1W,1M} × 所有品种

订阅集合 = 交易品种（state.stores）∪ 图表品种（current_symbol，可能只是看图）。
消息按 instId 路由到对应 SymbolStore，各品种的信号检测 / 止盈止损互不干扰。

广播策略：
  candle   只发图表品种（前端只画一张图）
  ticker   图表品种每 tick 发；其它品种节流 ~1 秒 1 次（前端持仓卡算浮盈用）
  signal / order / position  所有品种都发，消息带 symbol

每根 K 线收盘时重算该周期 SuperTrend；若最后一次翻转正好落在这根，
就产生 Buy/Sell 信号，补上 MTF Bias 上下文后广播 + 推送。

只在 confirm=1（收盘）时判信号，和 Pine 里 `barstate.isconfirmed` 的做法一致 ——
未收盘的价格来回穿轨会导致信号反复出现又消失（repaint）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time

import websockets
from websockets.exceptions import ConnectionClosed

import notify
import regime
import strategy
from history import fetch_candles, load_history
from indicators import st_signals, super_trend
from state import AppState, Candle, SymbolStore, Ticker, TF_CONFIG
from integration import enhanced_signal_handler

logger = logging.getLogger(__name__)

PUBLIC_ENDPOINTS   = ["wss://ws.okx.com:8443/ws/v5/public",   "wss://ws.okx.com/ws/v5/public"]
BUSINESS_ENDPOINTS = ["wss://ws.okx.com:8443/ws/v5/business", "wss://ws.okx.com/ws/v5/business"]

CHANNEL_TF = {cfg["okx_channel"]: tf for tf, cfg in TF_CONFIG.items()}
RECONNECT_DELAY = 3
SUB_CHUNK = 20            # 单次 subscribe 请求最多带多少个频道参数
TICK_BC_INTERVAL = 1.0    # 非图表品种 ticker 广播节流（秒）

# 触发微信推送的周期（短周期翻转频繁，默认只推 15m 以上）
NOTIFY_TFS = [t.strip() for t in os.environ.get("NOTIFY_TFS", "15m,30m,1h,4h,1d").split(",") if t.strip()]


class OKXFeed:
    def __init__(self, state: AppState):
        self.state = state
        self._pub_idx = self._biz_idx = 0
        self._biz_started = False
        self._ws: dict = {"public": None, "business": None}
        self._tick_bc: dict[str, float] = {}   # {symbol: 上次 ticker 广播时刻}

    # ── 品种集合 ────────────────────────────────────────────────
    def _symbols(self) -> list[str]:
        """需要订阅行情的全部品种：交易列表 ∪ 图表品种。"""
        syms = list(self.state.stores)
        if self.state.current_symbol not in syms:
            syms.append(self.state.current_symbol)
        return syms

    def _store_of(self, symbol: str) -> SymbolStore | None:
        s = self.state.stores.get(symbol)
        if s:
            return s
        vs = self.state.view_store
        return vs if vs and vs.symbol == symbol else None

    # ── 连接管理 ────────────────────────────────────────────────
    async def run(self):
        await asyncio.gather(self._loop("public"), self._loop("business"))

    async def _loop(self, kind: str):
        retries = 0
        while True:
            try:
                eps = PUBLIC_ENDPOINTS if kind == "public" else BUSINESS_ENDPOINTS
                idx = self._pub_idx if kind == "public" else self._biz_idx
                url = eps[idx % len(eps)]
                logger.info(f"[{kind}] 连接 {url}")
                await self._connect(url, kind)
                retries = 0
            except (ConnectionClosed, OSError, asyncio.TimeoutError) as e:
                retries += 1
                # ConnectionResetError 之类的 str(e) 是空的，带上类型名才看得出断因
                reason = str(e) or type(e).__name__
                logger.warning(f"[{kind}] 断开({retries}): {reason}")
                if retries % 3 == 0:      # 连续失败换备用域名
                    if kind == "public":
                        self._pub_idx += 1
                    else:
                        self._biz_idx += 1
                await asyncio.sleep(min(RECONNECT_DELAY * retries, 30))
            except Exception as e:
                logger.error(f"[{kind}] 错误: {e}")
                await asyncio.sleep(RECONNECT_DELAY)

    async def _send_op(self, ws, op: str, args: list):
        """subscribe/unsubscribe 分批发送，避免单条请求参数过多被拒。"""
        for i in range(0, len(args), SUB_CHUNK):
            await ws.send(json.dumps({"op": op, "args": args[i:i + SUB_CHUNK]}))

    async def _connect(self, url: str, kind: str):
        async with websockets.connect(url, ping_interval=20, ping_timeout=10, close_timeout=5) as ws:
            self._ws[kind] = ws
            try:
                syms = self._symbols()
                await self._send_op(ws, "subscribe", self._sub_args(kind, syms))
                logger.info(f"[{kind}] 订阅 {syms}")

                # 首连历史由 main 的 load_history 负责；重连说明断过网，REST 补缺口
                if kind == "business":
                    if self._biz_started:
                        await self._backfill()
                    else:
                        self._biz_started = True

                async for raw in ws:
                    try:
                        await self._dispatch(json.loads(raw), kind)
                    except json.JSONDecodeError:
                        pass
            finally:
                self._ws[kind] = None

    def _sub_args(self, kind: str, symbols: list[str]) -> list:
        if kind == "public":
            return [{"channel": "tickers", "instId": s} for s in symbols]
        return [{"channel": cfg["okx_channel"], "instId": s}
                for s in symbols for cfg in TF_CONFIG.values()]

    async def _resub(self, op: str, symbol: str):
        """对单个品种在两条连接上执行 subscribe / unsubscribe。"""
        for kind in ("public", "business"):
            ws = self._ws.get(kind)
            if ws is None:
                continue
            try:
                await self._send_op(ws, op, self._sub_args(kind, [symbol]))
            except Exception as e:
                logger.warning(f"[{kind}] {op} {symbol} 失败: {e}")

    async def _backfill(self):
        """重连后拉 REST 补断网期间的 K 线，再整段刷新前端。"""
        for symbol in self._symbols():
            store = self._store_of(symbol)
            if store is None:
                continue
            logger.info(f"[business] 重连，回补 [{symbol}] K线…")
            loop = asyncio.get_event_loop()
            for tf in TF_CONFIG:
                try:
                    fetched = await loop.run_in_executor(None, fetch_candles, tf, 300, symbol)
                    if fetched:
                        self._merge(store, tf, fetched)
                except Exception as e:
                    logger.warning(f"[business] 回补 {symbol} {tf} 失败: {e}")
                await asyncio.sleep(0.15)
            self.rescan_signals(store)
        await self.push_snapshot()

    def _merge(self, store: SymbolStore, tf: str, fetched: list[Candle]):
        """REST 数据补缺口，重叠 ts 以内存实时数据为准。"""
        deq = store.candles.get(tf)
        if deq is None:
            return
        by_ts = {c.ts: c for c in fetched}
        for c in deq:
            by_ts[c.ts] = c
        merged = sorted(by_ts.values(), key=lambda c: c.ts)
        if deq.maxlen:
            merged = merged[-deq.maxlen:]
        deq.clear()
        deq.extend(merged)

    # ── 消息分发 ────────────────────────────────────────────────
    async def _dispatch(self, msg: dict, kind: str):
        if "event" in msg:
            if msg.get("event") == "error":
                logger.error(f"[{kind}] {msg}")
            return

        arg = msg.get("arg") or {}
        inst = arg.get("instId")
        # 按 instId 路由；退订后的残留消息路由不到 store，自然丢弃
        store = self._store_of(inst) if inst else None
        if store is None:
            return
        data = msg.get("data") or []
        if not data:
            return

        ch = arg.get("channel", "")
        if ch == "tickers":
            await self._on_ticker(store, data[0])
        elif ch in CHANNEL_TF:
            tf = CHANNEL_TF[ch]
            for row in data:
                await self._on_candle(store, tf, row)

    async def _on_ticker(self, store: SymbolStore, d: dict):
        last = float(d.get("last", 0) or 0)
        open24h = float(d.get("open24h", 0) or 0)
        store.ticker = Ticker(
            symbol=store.symbol,
            last=last,
            open24h=open24h,
            high24h=float(d.get("high24h", 0) or 0),
            low24h=float(d.get("low24h", 0) or 0),
            vol24h=float(d.get("vol24h", 0) or 0),
            change24h=last - open24h,
            ts=int(d.get("ts", time.time() * 1000)),
        )
        # 图表品种每 tick 广播；其它品种节流（前端只用来刷持仓卡的浮盈）
        now = time.time()
        if (store.symbol == self.state.current_symbol
                or now - self._tick_bc.get(store.symbol, 0) >= TICK_BC_INTERVAL):
            self._tick_bc[store.symbol] = now
            await self.state.broadcast(
                {"type": "ticker", "symbol": store.symbol, "data": vars(store.ticker)})

        # 止盈止损按 tick 检查（秒级，不受广播节流影响）—— 等 K 线收盘再止损就晚了
        ex = self.state.executors.get(store.symbol)
        if ex and store.position:
            try:
                await ex.on_price(store.ticker.last)
            except Exception as e:
                logger.warning(f"[{store.symbol} 止盈止损检查失败] {e}")

    async def _on_candle(self, store: SymbolStore, tf: str, row: list):
        try:
            candle = Candle(
                ts=int(row[0]), o=float(row[1]), h=float(row[2]),
                l=float(row[3]), c=float(row[4]), vol=float(row[5]),
                confirm=(row[8] == "1") if len(row) > 8 else False,
            )
        except (IndexError, ValueError):
            return

        deq = store.candles.get(tf)
        if deq is None:
            return
        if deq and deq[-1].ts == candle.ts:
            deq[-1] = candle
        else:
            deq.append(candle)

        if store.symbol == self.state.current_symbol:
            await self.state.broadcast({
                "type": "candle", "symbol": store.symbol, "tf": tf, "data": vars(candle),
            })

        if candle.confirm:
            await self._check_flip(store, tf, candle.ts)
            # 收盘后超趋线已定，跟随它移动止损
            ex = self.state.executors.get(store.symbol)
            if ex and store.position:
                try:
                    await ex.on_st_line(tf, self._st_line(store, tf))
                except Exception as e:
                    logger.warning(f"[{store.symbol} 移动止损失败] {e}")

    def _st_line(self, store: SymbolStore, tf: str) -> float | None:
        """该品种该周期当前的超趋线值（多头取 up、空头取 dn）。"""
        candles = store.candles_by_tf(tf)
        if len(candles) < store.params.periods + 2:
            return None
        st = self._st_of(store, candles)
        trend = st.get("trend") or []
        if not trend or trend[-1] is None:
            return None
        return st["up"][-1] if trend[-1] == 1 else st["dn"][-1]

    # ── 信号判定 ────────────────────────────────────────────────
    def _st_of(self, store: SymbolStore, candles: list[dict]) -> dict:
        p = vars(store.params)
        return super_trend(
            [c["o"] for c in candles], [c["h"] for c in candles],
            [c["l"] for c in candles], [c["c"] for c in candles],
            periods=p["periods"], multiplier=p["multiplier"],
            src=p["src"], change_atr=p["change_atr"],
        )

    def _flip_at(self, store: SymbolStore, tf: str, ts: int) -> dict | None:
        """最后一次翻转恰好落在 ts 这根 → 返回信号，否则 None。"""
        candles = store.candles_by_tf(tf)
        if len(candles) < store.params.periods + 2:
            return None
        st = self._st_of(store, candles)
        if not st.get("flips"):
            return None
        sigs = st_signals(candles, st, tf)
        return sigs[-1] if sigs and sigs[-1]["ts"] == ts else None

    async def _check_flip(self, store: SymbolStore, tf: str, ts: int):
        try:
            sig = self._flip_at(store, tf, ts)
        except Exception as e:
            logger.warning(f"[{store.symbol} {tf}] 信号计算失败: {e}")
            return
        if not sig:
            return

        try:
            full = strategy.evaluate(store.all_candles(), vars(store.params), sig)
        except Exception as e:
            logger.warning(f"[{store.symbol} {tf}] Bias 评估失败: {e}")
            full = sig

        # ── 行情状态闸门：震荡市只提醒不下单 ──
        # cfg 是「全局闸门 + 本品种下单参数」的合并视图；仅看图品种 enabled 恒为 False
        cfg = self.state.cfg_for(store.symbol)
        candles = store.candles_by_tf(tf)
        try:
            # 使用增强版信号评估
            gate = enhanced_signal_handler(
                full, candles, cfg,
                candles_by_tf=store.all_candles(),
                p=store.params,
                use_momentum=True,      # 启用动量突破检测
                use_false_filter=True,  # 启用假突破过滤
                use_adaptive=True       # 启用自适应阈值
            )
        except Exception as e:
            logger.warning(f"[{store.symbol} {tf}] 增强版行情判定失败，回退原版: {e}")
            # 出错时回退到原版
            try:
                gate = regime.evaluate(full, candles, cfg,
                                      candles_by_tf=store.all_candles(),
                                      p=store.params)
            except Exception as e2:
                logger.warning(f"[{store.symbol} {tf}] 行情判定完全失败: {e2}")
                gate = {"trade": False, "regime": {"label": "判定失败"}, "reasons": [str(e2)]}

        full["regime"] = gate["regime"]
        full["gate_reasons"] = gate["reasons"]
        full["will_trade"] = gate["trade"]
        full["hidden"] = gate.get("hidden", False)    # ER过低的静默信号
        full["profile"] = gate.get("profile")
        full["filters"] = gate.get("filters", {})     # 过滤器详情
        full["symbol"] = store.symbol

        store.add_signal(full)
        # ER太低的信号不弹窗、不提醒、静默入库。
        # 但若正好是当前持仓的开仓周期反向，仍要交给执行器平仓，否则 15m 仓会一直挂着。
        if gate.get("hidden"):
            logger.info(f"[静默信号] {store.symbol} {tf} ER {full.get('er', '?')} < {cfg.er_hide_below}")
            ex = self.state.executors.get(store.symbol)
            pos = store.position
            if ex and pos and pos.tf == tf:
                try:
                    await ex.on_signal(full, {**gate, "trade": False})
                except Exception as e:
                    logger.error(f"[{store.symbol} 静默反向平仓异常] {e}")
            return
        prof = {"quick": " 弱档", "normal": " 标准档"}.get(gate.get("profile"), "")
        logger.info(
            f"[{'BUY ' if full['type'] == 'buy' else 'SELL'}] {store.symbol} {tf} "
            f"@ {full['price']} 等级={full.get('grade','?')} 强度={full.get('score')}/3 "
            f"Bias={full.get('bias_label','')} 行情={gate['regime'].get('label')} "
            f"{'→ 挂单' + prof if gate['trade'] else '→ 仅提醒: ' + '; '.join(gate['reasons'][:2])}"
        )
        await self.state.broadcast({"type": "signal", "symbol": store.symbol, "data": full})

        # 冷却：同品种同周期短时间内不重复开仓（按品种隔离）
        order = None
        if gate["trade"]:
            last = store.last_order_at.get(tf, 0)
            if time.time() - last < cfg.cooldown_sec:
                full["will_trade"] = False
                full["gate_reasons"] = [f"冷却中（{cfg.cooldown_sec}s 内已下过单）"]
                gate = {**gate, "trade": False}

        # 交给该品种的执行器：反向信号会先平仓，再按闸门决定是否开新仓
        ex = self.state.executors.get(store.symbol)
        if ex:
            try:
                order = await ex.on_signal(full, gate)
                if order and order.get("ok"):
                    store.last_order_at[tf] = time.time()
            except Exception as e:
                logger.error(f"[{store.symbol} 执行器异常] {e}")

        if notify.enabled and tf in NOTIFY_TFS:
            asyncio.create_task(notify.push_signal(store.symbol, full, order))

    def rescan_signals(self, store: SymbolStore | None = None):
        """用内存 K 线重扫历史翻转，重建 store.signals。store=None 时扫全部。"""
        stores = [store] if store else [self._store_of(s) for s in self._symbols()]
        for st_store in stores:
            if st_store is None:
                continue
            p = vars(st_store.params)
            all_c = st_store.all_candles()
            try:
                verdict = strategy.mtf_bias(all_c, p)["verdict"]
            except Exception:
                verdict = "mixed"

            out = []
            cfg = self.state.cfg_for(st_store.symbol)   # 用当前 ER 阈值
            for tf, candles in all_c.items():
                if len(candles) < p["periods"] + 2:
                    continue
                try:
                    st = self._st_of(st_store, candles)
                    if not st.get("flips"):
                        continue
                    for s in st_signals(candles, st, tf)[-25:]:
                        s["grade"] = strategy.grade(s, verdict)
                        s["symbol"] = st_store.symbol
                        # 用信号出现时刻的K线切片算ER，而非当前最新K线
                        sig_idx = next(
                            (j for j, c in enumerate(candles) if c["ts"] == s["ts"]),
                            None,
                        )
                        if sig_idx is not None and sig_idx + 1 >= 61:
                            candles_at_signal = candles[:sig_idx + 1]
                        else:
                            candles_at_signal = candles
                        gate = regime.evaluate(s, candles_at_signal, cfg,
                                             candles_by_tf=st_store.all_candles(),
                                             p=st_store.params)
                        s["hidden"] = gate.get("hidden", False)
                        s["will_trade"] = gate.get("trade", False)
                        s["gate_reasons"] = gate.get("reasons", [])
                        s["regime"] = gate.get("regime")
                        s["filters"] = gate.get("filters", {})
                        out.append(s)
                except Exception as e:
                    logger.warning(f"[{st_store.symbol} {tf}] 历史信号扫描失败: {e}")
            out.sort(key=lambda s: s["ts"])
            st_store.signals = out[-300:]

    async def push_snapshot(self):
        s = self.state
        await s.broadcast({
            "type":    "snapshot",
            "symbol":  s.current_symbol,
            "ticker":  vars(s.ticker),
            "candles": s.all_candles(),
            "signals": s.signals,
            "params":  vars(s.params),
            # 多品种视图（旧前端会忽略这些键）
            "tickers":   {sym: vars(st.ticker) for sym, st in s.stores.items()},
            "positions": {sym: st.position.to_dict(st.ticker.last)
                          for sym, st in s.stores.items() if st.position},
            "symbols":   [{**vars(st.cfg), "params": vars(st.params),
                           "exit_rules": vars(st.exit_rules),
                           "exit_rules_quick": vars(st.exit_rules_quick),
                           "last": st.ticker.last, "history_loaded": st.history_loaded}
                          for st in s.stores.values() if st.cfg],
        })

    # ── 运行时变更 ──────────────────────────────────────────────
    async def switch_symbol(self, new: str) -> dict:
        """切换图表品种。交易列表内 → 秒切；列表外 → 建仅看图 store 拉历史。"""
        old = self.state.current_symbol
        if new == old:
            return {"ok": True, "symbol": new, "changed": False}

        logger.info(f"图表切换 {old} → {new}")
        old_view = self.state.view_store
        self.state.current_symbol = new

        if new in self.state.stores:
            # 交易中的品种：数据本来就在内存里，退订旧的仅看图品种即可
            if old_view and old_view.symbol not in self.state.stores:
                await self._resub("unsubscribe", old_view.symbol)
                self.state.view_store = None
            await self.push_snapshot()
            return {"ok": True, "symbol": new, "changed": True}

        # 列表外的品种：建 view store、订阅、拉历史
        if old_view and old_view.symbol != new and old_view.symbol not in self.state.stores:
            await self._resub("unsubscribe", old_view.symbol)
            self.state.view_store = None
        vs = self.state.view          # 创建/复用 view_store
        await self._resub("subscribe", new)
        try:
            await load_history(vs)
        except Exception as e:
            logger.warning(f"加载 {new} 历史失败: {e}")
        self.rescan_signals(vs)
        await self.push_snapshot()
        return {"ok": True, "symbol": new, "changed": True}

    async def add_symbol(self, symbol: str):
        """品种加入交易列表后调用：订阅行情（若还没订过）。历史加载由调用方安排。"""
        # view_store 升级为交易 store 的场景已在 router 处理，这里只保证订阅存在
        await self._resub("subscribe", symbol)

    async def remove_symbol(self, symbol: str):
        """品种移出交易列表后调用：若也不是图表品种则退订。"""
        if symbol != self.state.current_symbol:
            await self._resub("unsubscribe", symbol)

    async def apply_params(self, store: SymbolStore | None = None):
        """参数变更后重扫该品种信号并整段刷新前端。默认作用于图表品种。"""
        self.rescan_signals(store or self.state.view)
        await self.push_snapshot()
