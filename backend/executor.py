"""
交易执行器：把信号 / 价格变动翻译成实际下单动作

三个入口：
    on_signal(sig)      新信号 → 反向信号先平仓，再按闸门决定是否开新仓
    on_price(price)     每次 ticker 推送 → 检查止盈止损（秒级，不等 K 线收盘）
    on_st_line(line)    超趋线更新 → 移动跟踪止损

持仓只在内存，重启会丢。真实持仓以交易所为准，面板上有「对账」按钮可查。
"""

from __future__ import annotations

import asyncio
import logging
import time

import position
import regime
import trade

logger = logging.getLogger(__name__)


class Executor:
    """一个品种一个实例。store 是该品种的 SymbolStore，持仓/价格/冷却都在里面 ——
    品种之间互不串行（各自有锁），品种内 on_price 与 on_signal 互斥。"""

    def __init__(self, state, store):
        self.state = state
        self.store = store
        # 同一时刻只允许一个下单流程，避免 ticker 与信号并发重复平仓
        self._lock = asyncio.Lock()
        self._lev_set: set = set()      # 已设置过杠杆的 (symbol, leverage)

    # ── 工具 ────────────────────────────────────────────────────
    @property
    def cfg(self):
        # 全局闸门 + 本品种下单参数的合并视图（enabled = 总开关 AND 品种开关）
        return self.state.cfg_for(self.store.symbol)

    @property
    def rules(self):
        """标准档规则，品种独立。"""
        return self.store.exit_rules

    def rules_of(self, pos):
        """该持仓开仓时定下的那档规则。持仓期间不随行情改档。"""
        return self.state.rules_for(getattr(pos, "profile", "normal"), self.store.symbol)

    async def _spec_of(self, pos) -> dict:
        """取该持仓对应的合约规格。instId 必须是交易品类对应的形式。"""
        swap = self.cfg.category == "SWAP"
        iid = trade.to_swap(pos.symbol) if swap else trade.to_spot(pos.symbol)
        return await trade.get_spec(iid, "SWAP" if swap else "SPOT")

    async def _record(self, order: dict, extra: dict):
        # sym 一律用现货形式（store 键），order 里交易所返回的 symbol 是合约形式
        o = {**order, **extra, "sym": self.store.symbol,
             "ts": order.get("ts") or int(time.time() * 1000)}
        self.state.add_order(o)
        await self.state.broadcast({"type": "order", "symbol": self.store.symbol, "data": o})
        return o

    async def _push_position(self):
        pos = self.store.position
        await self.state.broadcast({
            "type": "position",
            "symbol": self.store.symbol,
            "data": pos.to_dict(self.store.ticker.last) if pos else None,
        })

    async def _ensure_leverage(self, symbol: str) -> None:
        """开仓前确保杠杆已设置。同一 (symbol, leverage) 只设一次。"""
        if self.cfg.category == "SPOT":
            return
        key = (symbol, self.cfg.leverage, self.cfg.paper)
        if key in self._lev_set:
            return
        r = await trade.set_leverage(symbol, self.cfg.leverage,
                                     self.cfg.margin_mode, sim=self.cfg.paper)
        if r.get("ok"):
            self._lev_set.add(key)
        else:
            # 设置失败不阻断下单：可能账户已是该杠杆，或该品种不支持
            logger.warning(f"设置杠杆未成功（继续下单）: {r.get('error')}")

    # ── 信号入口 ────────────────────────────────────────────────
    async def on_signal(self, sig: dict, gate: dict) -> dict | None:
        """返回本次产生的开仓单（没下单则 None）。平仓单单独广播。"""
        async with self._lock:
            pos = self.store.position
            want = "long" if sig["type"] == "buy" else "short"

            # 1) 反向信号平仓：只认「与持仓同周期」或「自身通过闸门要下单」的信号。
            #    不加限制的话，1m 噪声翻转（不在允许周期、仅提醒级别）也会把
            #    15m 的仓位随手平掉 —— 实盘已出现过一次。
            if (pos and pos.qty > 0 and pos.side != want
                    and (sig["tf"] == pos.tf or gate.get("trade"))):
                await self._close(pos, f"{sig['tf']} 出现反向信号", sig.get("price"))

            # 2) 同向已有仓位就不加仓，避免信号密集时越滚越大
            pos = self.store.position
            if pos and pos.qty > 0 and pos.side == want:
                logger.info(f"[跳过开仓] {self.store.symbol} 已持有同向 {want} 仓位")
                return None

            if not gate.get("trade"):
                return None
            return await self._open(sig, gate.get("profile") or "normal")

    async def _resolve_margin(self) -> tuple[float | None, str]:
        """开仓保证金。fixed=固定金额；equity_pct=账户净值的 X%，且不超过 USDT 可用。"""
        cfg = self.cfg
        sc = self.store.cfg
        mode = getattr(sc, "sizing_mode", None) or "fixed"
        if mode != "equity_pct":
            m = float(cfg.amount_usdt)
            return m, f"{m}U × {cfg.leverage}x"

        info = await trade.ping(sim=cfg.paper)
        if not info.get("ok"):
            return None, info.get("error") or "查询账户净值失败"
        eq = float(info.get("equity") or 0)
        avail = float(info.get("usdt_avail") or 0)
        pct = max(1.0, min(100.0, float(getattr(sc, "equity_pct", 10) or 10)))
        want = eq * pct / 100.0
        cap = avail if avail > 0 else want
        margin = round(min(want, cap), 4)
        if margin < 1:
            return None, (
                f"按净值 {pct:.0f}% 算出 {margin}U，不足 1U"
                f"（净值 {eq:.2f} / 可用 {avail:.2f}）"
            )
        note = f"净值 {eq:.2f}U × {pct:.0f}% = {margin}U × {cfg.leverage}x"
        if avail > 0 and want > avail + 1e-6:
            note += f"（可用 {avail:.2f}U 封顶）"
        return margin, note

    async def _open(self, sig: dict, profile: str = "normal") -> dict | None:
        cfg, symbol = self.cfg, self.store.symbol
        rules = self.state.rules_for(profile, self.store.symbol)
        await self._ensure_leverage(symbol)

        px = regime.limit_price(sig, cfg)
        side = "buy" if sig["type"] == "buy" else "sell"
        extra = {
            "kind": "open", "tf": sig["tf"], "sig_ts": sig["ts"],
            "sig_type": sig["type"], "grade": sig.get("grade"), "trigger": sig["price"],
            "profile": profile,
        }
        margin, how = await self._resolve_margin()
        if margin is None:
            logger.warning(f"[跳过开仓] {symbol} {how}")
            return await self._record({"ok": False, "error": how, "price": px}, extra)

        r = await trade.place_order(
            symbol, side, px,
            margin_usdt=margin, leverage=cfg.leverage,
            category=cfg.category, mgn_mode=cfg.margin_mode,
            client_oid=f"o{sig['tf']}{sig['ts']}",
            sim=cfg.paper,
            ref_price=self.store.ticker.last or sig["price"],
        )
        order = await self._record(r, extra)
        if not r.get("ok"):
            return order

        stop = position.initial_stop(sig, rules)
        # 用交易所返回的 instId（已是 BTC-USDT-SWAP 这种合约形式），
        # 后续查规格/平仓都直接用它，避免现货/合约 form 混淆
        self.store.position = position.Position(
            symbol=r.get("symbol") or symbol, side="long" if side == "buy" else "short",
            tf=sig["tf"], entry=r["price"], qty=r["qty"], init_qty=r["qty"],
            stop=stop, leverage=cfg.leverage, entry_ts=r["ts"], order_id=r.get("orderId", ""),
            ct_val=r.get("ct_val", 1), profile=profile,
        )
        unit = "张" if cfg.category == "SWAP" else ""
        tag = "快进快出档" if profile == "quick" else "标准档"
        self.store.position.log(
            "open",
            f"开{'多' if side == 'buy' else '空'} {r['qty']}{unit} @ {r['price']}"
            f"（保证金 {how}，止损 {stop}，{tag}"
            f" 止盈 {rules.tp1_pct}% 平 {rules.tp1_ratio:.0f}%）",
        )
        logger.info(f"[持仓建立] {symbol} {self.store.position.side} @ {r['price']} "
                    f"保证金 {how} 止损 {stop} 档位 {profile}")
        await self._push_position()
        return order

    # ── 价格入口（止盈止损） ────────────────────────────────────
    async def on_price(self, price: float):
        pos = self.store.position
        if not pos or pos.qty <= 0 or not price:
            return
        act = position.check(pos, price, self.rules_of(pos))
        if not act:
            return
        async with self._lock:
            # 抢到锁后重新确认，避免与 on_signal 的平仓重复执行
            pos = self.store.position
            if not pos or pos.qty <= 0:
                return
            act = position.check(pos, price, self.rules_of(pos))
            if not act:
                return
            if act["action"] == "tp1":
                await self._take_profit(pos, price, act)
            else:
                await self._close(pos, act["reason"], price)

    async def _take_profit(self, pos, price: float, act: dict):
        # 按交易所的 lotSz 吸附，否则会带着浮点残渣（0.11200000000000002）去下单。
        # 注意 instId 要用合约form（BTC-USDT-SWAP），拿现货 form 会查不到规格、
        # 退回默认 lot_sz=1，把止盈量吸附成 0 —— 表现为止盈静默不执行。
        spec = await self._spec_of(pos)
        qty = trade._snap(pos.qty * act["ratio"] / 100, spec["lot_sz"])
        # 吸附后可能为 0（仓位太小），或反而超过持仓，两头都要夹住
        if qty <= 0:
            logger.info(f"[跳过止盈] 应平 {act['ratio']:.0f}% 不足最小变动单位 {spec['lot_sz']}")
            return
        qty = min(qty, pos.qty)
        r = await self._reduce(pos, qty, price, f"止盈 {act['ratio']:.0f}%")
        if not r.get("ok"):
            logger.warning(f"[止盈失败] {r.get('error')}")
            await self._record(r, {"kind": "tp1", "tf": pos.tf, "reason": act["reason"]})
            return
        position.apply_tp1(pos, r["price"], r["qty"], self.rules_of(pos))
        await self._record(r, {"kind": "tp1", "tf": pos.tf, "reason": act["reason"],
                               "profile": pos.profile})
        logger.info(f"[止盈] {pos.symbol} 平 {r['qty']} @ {r['price']}，止损→{pos.stop}")
        # 止盈比例 100%（弱档就是）会把仓位一次平光。不在这里收尾的话，
        # state.position 会挂着一个 qty=0 的空壳、这笔也不会进 closed 历史。
        if pos.qty <= 0:
            self._finalize(pos, r["price"], "止盈全平")
        await self._push_position()

    def _finalize(self, pos, price: float, reason: str):
        """仓位归零后的统一收尾：落进历史、清空当前持仓。"""
        logger.info(f"[持仓结束] {pos.symbol} {reason}，本笔已实现 {pos.realized:+.4f} USDT")
        self.store.closed.append({**pos.to_dict(price), "sym": self.store.symbol})
        self.store.closed = self.store.closed[-50:]
        self.store.position = None

    async def _close(self, pos, reason: str, price: float | None = None):
        if not pos or pos.qty <= 0:
            return
        r = await self._reduce(pos, pos.qty, price or self.store.ticker.last, "平仓")
        if not r.get("ok"):
            logger.warning(f"[平仓失败] {r.get('error')} — 请到交易所手动处理")
            await self._record(r, {"kind": "close", "tf": pos.tf, "reason": reason})
            return
        position.apply_close(pos, r["price"], r["qty"], reason)
        await self._record(r, {"kind": "close", "tf": pos.tf, "reason": reason,
                               "profile": pos.profile, "realized": round(pos.realized, 4)})
        self._finalize(pos, r["price"], reason)
        await self._push_position()

    async def _reduce(self, pos, qty: float, price: float | None, tag: str) -> dict:
        """减仓/平仓统一走市价 reduceOnly —— 止损止盈必须确保成交，
        限价挂上去不成交等于没止损。"""
        side = "sell" if pos.long else "buy"
        return await trade.place_order(
            pos.symbol, side, None, sz=qty,
            category=self.cfg.category, mgn_mode=self.cfg.margin_mode,
            order_type="market", reduce_only=True,
            client_oid=f"c{int(time.time()*1000)}",
            sim=self.cfg.paper,
            ref_price=price or self.store.ticker.last,
        )

    # ── 超趋线更新 → 移动止损 ───────────────────────────────────
    async def on_st_line(self, tf: str, line: float | None):
        pos = self.store.position
        if not pos or pos.qty <= 0 or pos.tf != tf or line is None:
            return
        # 已保本的仓位不再下调止损（trail 内部只朝有利方向移，这里是双保险）。
        # 弱档 trail_with_st=False，trail() 会直接返回 False，不用额外分支。
        if position.trail(pos, line, self.rules_of(pos)):
            await self._push_position()

    async def close_manual(self, reason: str = "手动平仓") -> dict:
        async with self._lock:
            pos = self.store.position
            if not pos or pos.qty <= 0:
                return {"ok": False, "error": "当前无持仓"}
            await self._close(pos, reason)
            return {"ok": True}
