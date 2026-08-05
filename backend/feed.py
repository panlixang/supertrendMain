"""
OKX 实时行情采集

两条 WS：
  public   → tickers（最新价 / 24h 统计）
  business → candle{1m,5m,15m,30m,1H,4H,1D,1W,1M}

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
import trade
from history import fetch_candles, load_history
from indicators import st_signals, super_trend
from state import AppState, Candle, Ticker, TF_CONFIG

logger = logging.getLogger(__name__)

PUBLIC_ENDPOINTS   = ["wss://ws.okx.com:8443/ws/v5/public",   "wss://ws.okx.com/ws/v5/public"]
BUSINESS_ENDPOINTS = ["wss://ws.okx.com:8443/ws/v5/business", "wss://ws.okx.com/ws/v5/business"]

CHANNEL_TF = {cfg["okx_channel"]: tf for tf, cfg in TF_CONFIG.items()}
RECONNECT_DELAY = 3

# 触发微信推送的周期（短周期翻转频繁，默认只推 15m 以上）
NOTIFY_TFS = [t.strip() for t in os.environ.get("NOTIFY_TFS", "15m,30m,1h,4h,1d").split(",") if t.strip()]


class OKXFeed:
    def __init__(self, state: AppState):
        self.state = state
        self.symbol = state.current_symbol
        self._pub_idx = self._biz_idx = 0
        self._biz_started = False
        self._ws: dict = {"public": None, "business": None}

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

    async def _connect(self, url: str, kind: str):
        async with websockets.connect(url, ping_interval=20, ping_timeout=10, close_timeout=5) as ws:
            self._ws[kind] = ws
            try:
                args = self._sub_args(kind, self.symbol)
                await ws.send(json.dumps({"op": "subscribe", "args": args}))
                logger.info(f"[{kind}] 订阅 {self.symbol}（{len(args)} 个频道）")

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

    def _sub_args(self, kind: str, symbol: str) -> list:
        if kind == "public":
            return [{"channel": "tickers", "instId": symbol}]
        return [{"channel": cfg["okx_channel"], "instId": symbol} for cfg in TF_CONFIG.values()]

    async def _backfill(self):
        """重连后拉 REST 补断网期间的 K 线，再整段刷新前端。"""
        logger.info(f"[business] 重连，回补 [{self.symbol}] K线…")
        loop = asyncio.get_event_loop()
        for tf in TF_CONFIG:
            try:
                fetched = await loop.run_in_executor(None, fetch_candles, tf, 300, self.symbol)
                if fetched:
                    self._merge(tf, fetched)
            except Exception as e:
                logger.warning(f"[business] 回补 {tf} 失败: {e}")
            await asyncio.sleep(0.15)
        self.rescan_signals()
        await self.push_snapshot()

    def _merge(self, tf: str, fetched: list[Candle]):
        """REST 数据补缺口，重叠 ts 以内存实时数据为准。"""
        deq = self.state.candles.get(tf)
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
        # 切换品种瞬间会收到旧品种残留消息，按 instId 丢弃
        if arg.get("instId") and arg["instId"] != self.symbol:
            return
        data = msg.get("data") or []
        if not data:
            return

        ch = arg.get("channel", "")
        if ch == "tickers":
            await self._on_ticker(data[0])
        elif ch in CHANNEL_TF:
            tf = CHANNEL_TF[ch]
            for row in data:
                await self._on_candle(tf, row)

    async def _on_ticker(self, d: dict):
        s = self.state
        last = float(d.get("last", 0) or 0)
        open24h = float(d.get("open24h", 0) or 0)
        s.ticker = Ticker(
            symbol=self.symbol,
            last=last,
            open24h=open24h,
            high24h=float(d.get("high24h", 0) or 0),
            low24h=float(d.get("low24h", 0) or 0),
            vol24h=float(d.get("vol24h", 0) or 0),
            change24h=last - open24h,
            ts=int(d.get("ts", time.time() * 1000)),
        )
        await s.broadcast({"type": "ticker", "symbol": self.symbol, "data": vars(s.ticker)})

        # 止盈止损按 tick 检查（秒级）—— 等 K 线收盘再止损就晚了
        if s.executor and s.position:
            try:
                await s.executor.on_price(s.ticker.last)
            except Exception as e:
                logger.warning(f"[止盈止损检查失败] {e}")

    async def _on_candle(self, tf: str, row: list):
        try:
            candle = Candle(
                ts=int(row[0]), o=float(row[1]), h=float(row[2]),
                l=float(row[3]), c=float(row[4]), vol=float(row[5]),
                confirm=(row[8] == "1") if len(row) > 8 else False,
            )
        except (IndexError, ValueError):
            return

        deq = self.state.candles.get(tf)
        if deq is None:
            return
        if deq and deq[-1].ts == candle.ts:
            deq[-1] = candle
        else:
            deq.append(candle)

        await self.state.broadcast({
            "type": "candle", "symbol": self.symbol, "tf": tf, "data": vars(candle),
        })

        if candle.confirm:
            await self._check_flip(tf, candle.ts)
            # 收盘后超趋线已定，跟随它移动止损
            if self.state.executor and self.state.position:
                try:
                    await self.state.executor.on_st_line(tf, self._st_line(tf))
                except Exception as e:
                    logger.warning(f"[移动止损失败] {e}")

    def _st_line(self, tf: str) -> float | None:
        """该周期当前的超趋线值（多头取 up、空头取 dn）。"""
        candles = self.state.candles_by_tf(tf)
        if len(candles) < self.state.params.periods + 2:
            return None
        st = self._st_of(candles)
        trend = st.get("trend") or []
        if not trend or trend[-1] is None:
            return None
        return st["up"][-1] if trend[-1] == 1 else st["dn"][-1]

    # ── 信号判定 ────────────────────────────────────────────────
    def _st_of(self, candles: list[dict]) -> dict:
        p = vars(self.state.params)
        return super_trend(
            [c["o"] for c in candles], [c["h"] for c in candles],
            [c["l"] for c in candles], [c["c"] for c in candles],
            periods=p["periods"], multiplier=p["multiplier"],
            src=p["src"], change_atr=p["change_atr"],
        )

    def _flip_at(self, tf: str, ts: int) -> dict | None:
        """最后一次翻转恰好落在 ts 这根 → 返回信号，否则 None。"""
        candles = self.state.candles_by_tf(tf)
        if len(candles) < self.state.params.periods + 2:
            return None
        st = self._st_of(candles)
        if not st.get("flips"):
            return None
        sigs = st_signals(candles, st, tf)
        return sigs[-1] if sigs and sigs[-1]["ts"] == ts else None

    async def _check_flip(self, tf: str, ts: int):
        try:
            sig = self._flip_at(tf, ts)
        except Exception as e:
            logger.warning(f"[{tf}] 信号计算失败: {e}")
            return
        if not sig:
            return

        try:
            full = strategy.evaluate(self.state.all_candles(), vars(self.state.params), sig)
        except Exception as e:
            logger.warning(f"[{tf}] Bias 评估失败: {e}")
            full = sig

        # ── 行情状态闸门：震荡市只提醒不下单 ──
        cfg = self.state.trade_cfg
        candles = self.state.candles_by_tf(tf)
        try:
            gate = regime.evaluate(full, candles, cfg)
        except Exception as e:
            logger.warning(f"[{tf}] 行情判定失败: {e}")
            gate = {"trade": False, "regime": {"label": "判定失败"}, "reasons": [str(e)]}

        full["regime"] = gate["regime"]
        full["gate_reasons"] = gate["reasons"]
        full["will_trade"] = gate["trade"]
        full["profile"] = gate.get("profile")

        self.state.add_signal(full)
        prof = {"quick": " 弱档", "normal": " 标准档"}.get(gate.get("profile"), "")
        logger.info(
            f"[{'BUY ' if full['type'] == 'buy' else 'SELL'}] {self.symbol} {tf} "
            f"@ {full['price']} 等级={full.get('grade','?')} 强度={full.get('score')}/3 "
            f"Bias={full.get('bias_label','')} 行情={gate['regime'].get('label')} "
            f"{'→ 挂单' + prof if gate['trade'] else '→ 仅提醒: ' + '; '.join(gate['reasons'][:2])}"
        )
        await self.state.broadcast({"type": "signal", "symbol": self.symbol, "data": full})

        # 冷却：同周期短时间内不重复开仓
        order = None
        if gate["trade"]:
            last = self.state.last_order_at.get(tf, 0)
            if time.time() - last < cfg.cooldown_sec:
                full["will_trade"] = False
                full["gate_reasons"] = [f"冷却中（{cfg.cooldown_sec}s 内已下过单）"]
                gate = {**gate, "trade": False}

        # 交给执行器：反向信号会先平仓，再按闸门决定是否开新仓
        if self.state.executor:
            try:
                order = await self.state.executor.on_signal(full, gate)
                if order and order.get("ok"):
                    self.state.last_order_at[tf] = time.time()
            except Exception as e:
                logger.error(f"[执行器异常] {e}")

        if notify.enabled and tf in NOTIFY_TFS:
            asyncio.create_task(notify.push_signal(self.symbol, full, order))

    def rescan_signals(self):
        """用当前内存 K 线重扫各周期历史翻转，重建 state.signals。"""
        p = vars(self.state.params)
        all_c = self.state.all_candles()
        try:
            verdict = strategy.mtf_bias(all_c, p)["verdict"]
        except Exception:
            verdict = "mixed"

        out = []
        for tf, candles in all_c.items():
            if len(candles) < p["periods"] + 2:
                continue
            try:
                st = self._st_of(candles)
                if not st.get("flips"):
                    continue
                for s in st_signals(candles, st, tf)[-25:]:
                    s["grade"] = strategy.grade(s, verdict)
                    out.append(s)
            except Exception as e:
                logger.warning(f"[{tf}] 历史信号扫描失败: {e}")
        out.sort(key=lambda s: s["ts"])
        self.state.signals = out[-300:]

    async def push_snapshot(self):
        await self.state.broadcast({
            "type":    "snapshot",
            "symbol":  self.symbol,
            "ticker":  vars(self.state.ticker),
            "candles": self.state.all_candles(),
            "signals": self.state.signals,
            "params":  vars(self.state.params),
        })

    # ── 运行时变更 ──────────────────────────────────────────────
    async def switch_symbol(self, new: str) -> dict:
        old = self.symbol
        if new == old:
            return {"ok": True, "symbol": new, "changed": False}

        logger.info(f"切换品种 {old} → {new}")
        self.symbol = new
        self.state.current_symbol = new

        for kind in ("public", "business"):
            ws = self._ws.get(kind)
            if ws is None:
                continue
            try:
                await ws.send(json.dumps({"op": "unsubscribe", "args": self._sub_args(kind, old)}))
                await ws.send(json.dumps({"op": "subscribe",   "args": self._sub_args(kind, new)}))
            except Exception as e:
                logger.warning(f"[{kind}] 重订阅失败: {e}")

        for tf in TF_CONFIG:
            self.state.candles[tf].clear()
        self.state.ticker = Ticker(symbol=new)
        self.state.signals = []
        try:
            await load_history(self.state, new)
        except Exception as e:
            logger.warning(f"加载 {new} 历史失败: {e}")

        self.rescan_signals()
        await self.push_snapshot()
        return {"ok": True, "symbol": new, "changed": True}

    async def apply_params(self):
        """参数变更后重扫信号并整段刷新前端。"""
        self.rescan_signals()
        await self.push_snapshot()
