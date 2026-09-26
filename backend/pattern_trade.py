"""形态识别页 · 独立自动下单（配置 / 凭据 / 持仓 / 执行循环全部独立于首页）。

与首页刻意「各自一套」：
- 配置文件 pattern_trade.json、凭据文件 pattern_credentials.json，都不进 settings.json
- 下单通过 creds 的 ContextVar 覆盖 —— Task 级隔离，两页可跑不同交易所 / 不同子账户
- 信号用基础周期的「原始 SuperTrend」（ATR 10 / factor 3.0），与首页 15/9.1 无关
- 过滤：block_4h（4h 形态反向拦截）+ 4 条品种独立过滤规则（趋势形态识别.md：连续翻转 / 波动异常 / 箱体错误位置 / 极端K）
- 出场用 position.ExitRules 默认档 = 回测验证过的 TP1 1.5% 平 70% + 保本 + 跟随 ST 跟踪

同一品种两页都可能下单时不校验冲突（用户用不同子账户各自管理）。
"""

from __future__ import annotations

import asyncio
import bisect
import json
import logging
import math
import os
import time

import numpy as np
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import creds
import history
import trade
from executor import Executor
from indicators import ma, super_trend, ta_adx, ta_atr, ta_ema, ta_sma
from pattern_recog import recognize as recognize_pattern
from position import ExitRules
from regime import TradeConfig
from state import SymbolStore, SymbolTradeConfig
import signal_v3

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(_DIR, "pattern_trade.json")
CRED_FILE = os.path.join(_DIR, "pattern_credentials.json")

EXCHANGES = ("okx", "bitget")
ALL_TFS = ["15m", "1h", "4h", "1d"]

# 形态识别页基础信号的 SuperTrend 参数（必须和 /api/pattern 一致）
ST_PERIODS = 10
ST_MULTIPLIER = 3.0


def _okx_symbol(s: str) -> str:
    """BTCUSDT / btc-usdt → BTC-USDT（前端两种写法都收）。"""
    s = (s or "").upper().strip()
    if "-" in s:
        return s
    for q in ("USDT", "USDC"):
        if s.endswith(q):
            return f"{s[:-len(q)]}-{q}"
    return s


@dataclass
class PatternConfig:
    """形态识别页交易的全局配置。"""
    enabled:      bool  = False    # 总开关
    paper:        bool  = True     # 模拟盘（默认模拟，切实盘必须显式改）
    category:     str   = "SWAP"
    margin_mode:  str   = "cross"
    price_offset: float = 0.05
    exchange:     str   = "okx"
    block_4h:     bool  = True     # True = 4h 形态反向则不下单

    # ── 过滤策略 ──
    # 品种独立开关 filter_v3，挂在 SymbolTradeConfig（见 state.py），默认关=不过滤；
    # 本全局档只保留 block_4h 硬门槛。过滤实现见 _allow_by_filter / _v3_allow（signal_v3 打分体系）。

    cooldown_sec: int   = 300      # 同一品种同一周期两次下单最小间隔
    poll_sec:     int   = 20       # 轮询间隔
    # 出场参数（回测验证档，面板可配置）
    tp1_pct:         float = 1.5
    tp1_ratio:       float = 70.0
    sl_pct:          float = 2.0    # 初始止损兜底（SuperTrend 轨道无效时用）
    move_sl_to_entry: bool  = True   # 止盈后止损移到开仓价保本
    trail_with_st:    bool  = True   # 剩余仓位跟随 SuperTrend 跟踪
    reverse_close:    bool  = False  # 反向平仓：True=只按同周期反向信号平仓，TP1/保本/跟踪/硬止损全失效


class PatternStateProxy:
    """给 Executor 用的状态代理：读形态识别页自己的配置，不碰首页 AppState。"""

    def __init__(self, trader: "PatternTrader"):
        self.trader = trader

    def _base(self) -> TradeConfig:
        c = self.trader.cfg
        return TradeConfig(
            enabled=c.enabled, paper=c.paper, category=c.category,
            margin_mode=c.margin_mode, price_offset=c.price_offset,
        )

    def cfg_for(self, symbol: str) -> TradeConfig:
        """全局 + 品种的合并视图（字段语义同 AppState.cfg_for）。"""
        from dataclasses import replace
        t = self.trader
        sc = t.symbols.get(symbol)
        if sc is None:
            return TradeConfig(enabled=False)
        return replace(
            self._base(),
            enabled=t.cfg.enabled and sc.enabled,
            leverage=sc.leverage,
            amount_usdt=sc.margin_usdt,
            allow_tfs=list(sc.allow_tfs),
            cooldown_sec=t.cfg.cooldown_sec,
        )

    def rules_for(self, profile: str, symbol: str):
        return self.trader.rules_for_symbol(symbol) if symbol else self.trader.rules

    def add_order(self, o: dict):
        self.trader.orders.append(o)
        self.trader.orders = self.trader.orders[-300:]

    async def broadcast(self, payload: dict):
        self.trader.events.append({**payload, "ts": int(time.time() * 1000)})


class PatternTrader:
    """形态识别页的自动交易：后台常驻任务，自己的配置 / 凭据 / 持仓状态。"""

    def __init__(self):
        self.cfg = PatternConfig()
        self.symbols: dict[str, SymbolTradeConfig] = {}
        self.stores: dict[str, SymbolStore] = {}
        self.executors: dict[str, Executor] = {}
        self.proxy = PatternStateProxy(self)
        self.orders: list[dict] = []
        self.events: deque = deque(maxlen=200)
        self._keys: dict = {"exchange": "okx", "okx": {}, "bitget": {}}
        self._last_flip: dict[tuple, int] = {}      # (sym, tf) -> 已处理的翻转 ts
        self._last_order_at: dict[tuple, float] = {}
        self._pat_cache: dict[str, tuple] = {}      # sym -> (最新4h ts, pts, pmap)
        self._running = False
        self._load()

    # ── 配置持久化（与首页 settings.json 完全分开）─────────────
    def _load(self):
        if os.path.exists(CRED_FILE):
            try:
                with open(CRED_FILE) as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._keys["exchange"] = (data.get("exchange") or "okx").lower()
                    for ex in ("okx", "bitget"):
                        if isinstance(data.get(ex), dict):
                            self._keys[ex] = dict(data[ex])
            except Exception as e:
                logger.warning(f"[形态下单] 读取凭据失败: {e}")
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE) as f:
                    data = json.load(f)
                for k, v in (data.get("cfg") or {}).items():
                    if hasattr(self.cfg, k):
                        setattr(self.cfg, k, v)
                for row in (data.get("symbols") or []):
                    sym = _okx_symbol(row.get("symbol") or "")
                    if not sym:
                        continue
                    self.symbols[sym] = SymbolTradeConfig(
                        symbol=sym,
                        enabled=bool(row.get("enabled")),
                        margin_usdt=float(row.get("margin_usdt") or 10.0),
                        leverage=int(row.get("leverage") or 3),
                        allow_tfs=list(row.get("allow_tfs") or ["1h"]),
                        tp1_pct=row.get("tp1_pct"),
                        tp1_ratio=row.get("tp1_ratio"),
                        sl_pct=row.get("sl_pct"),
                        move_sl_to_entry=row.get("move_sl_to_entry"),
                        trail_with_st=row.get("trail_with_st"),
                        reverse_close=row.get("reverse_close"),
                        filter_v3=bool(row.get("filter_v3") or False),
                    )
            except Exception as e:
                logger.warning(f"[形态下单] 读取配置失败: {e}")
        self._sync()

    def save(self):
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({
                "cfg": asdict(self.cfg),
                "symbols": [asdict(s) for s in self.symbols.values()],
            }, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_FILE)

    def _save_keys(self):
        tmp = CRED_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._keys, f)
        os.replace(tmp, CRED_FILE)
        try:
            os.chmod(CRED_FILE, 0o600)
        except Exception:
            pass

    # ── 品种管理 ──────────────────────────────────────────────
    def _sync(self):
        """按 symbols 建好 store / executor，并刷新每个品种的出场规则。"""
        for sym in list(self.stores):
            if sym not in self.symbols:
                self.stores.pop(sym, None)
                self.executors.pop(sym, None)
        for sym, sc in self.symbols.items():
            st = self.stores.get(sym)
            if st is None:
                st = SymbolStore(sym, cfg=sc)
                self.stores[sym] = st
                self.executors[sym] = Executor(self.proxy, st)
            st.cfg = sc
            # 品种独立出场：本品种设了就用本品种，没设回落全局默认
            st.exit_rules = self.rules_for_symbol(sym)

    def add_symbol(self, symbol: str, **kw) -> dict:
        sym = _okx_symbol(symbol)
        if not sym:
            return {"ok": False, "error": "品种为空"}
        self.symbols[sym] = SymbolTradeConfig(
            symbol=sym,
            enabled=bool(kw.get("enabled", False)),
            margin_usdt=float(kw.get("margin_usdt") or 10.0),
            leverage=int(kw.get("leverage") or 3),
            allow_tfs=list(kw.get("allow_tfs") or ["1h"]),
            tp1_pct=kw.get("tp1_pct"),
            tp1_ratio=kw.get("tp1_ratio"),
            sl_pct=kw.get("sl_pct"),
            move_sl_to_entry=kw.get("move_sl_to_entry"),
            trail_with_st=kw.get("trail_with_st"),
            reverse_close=kw.get("reverse_close"),
            filter_v3=kw.get("filter_v3"),
        )
        self._sync()
        self.save()
        return {"ok": True, "symbol": sym}

    def remove_symbol(self, symbol: str) -> dict:
        sym = _okx_symbol(symbol)
        if self.stores.get(sym) and self.stores[sym].position:
            return {"ok": False, "error": "该品种仍有持仓，先平仓再移除"}
        self.symbols.pop(sym, None)
        self._sync()
        self.save()
        return {"ok": True}

    def update_symbol(self, symbol: str, **kw) -> dict:
        sym = _okx_symbol(symbol)
        sc = self.symbols.get(sym)
        if not sc:
            return {"ok": False, "error": "品种不存在"}
        xr_keys = ("enabled", "margin_usdt", "leverage", "allow_tfs",
                   "tp1_pct", "tp1_ratio", "sl_pct",
                   "move_sl_to_entry", "trail_with_st", "reverse_close",
                   "filter_v3")
        for k in xr_keys:
            if k in kw and kw[k] is not None:
                setattr(sc, k, kw[k])
        self.save()
        self._sync()
        return {"ok": True}

    def update_cfg(self, **kw) -> dict:
        for k, v in kw.items():
            if v is not None and hasattr(self.cfg, k):
                setattr(self.cfg, k, v)
        if "exchange" in kw and kw["exchange"]:
            self._keys["exchange"] = str(kw["exchange"]).lower()
            self._save_keys()
        self.save()
        self._sync()
        return {"ok": True}

    # ── 凭据（独立的 Key 文件）─────────────────────────────────
    def set_keys(self, exchange_id: str, api_key="", api_secret="", passphrase="") -> dict:
        ex = (exchange_id or self._keys.get("exchange") or "okx").lower()
        if ex not in EXCHANGES:
            return {"ok": False, "error": f"不支持的交易所: {ex}"}
        b = dict(self._keys.get(ex) or {})
        if api_key:
            b["api_key"] = api_key.strip()
        if api_secret:
            b["api_secret"] = api_secret.strip()
        if passphrase:
            b["passphrase"] = passphrase.strip()
        self._keys[ex] = b
        self._keys["exchange"] = ex
        self.cfg.exchange = ex
        self._save_keys()
        self.save()
        return self.keys_public()

    def keys_public(self) -> dict:
        ex = (self.cfg.exchange or "okx").lower()
        b = dict(self._keys.get(ex) or {})
        ak = (b.get("api_key") or "").strip()
        masked = ""
        if ak:
            masked = "****" if len(ak) <= 8 else f"{ak[:4]}****{ak[-4:]}"
        return {
            "exchange": ex,
            "exchanges": list(EXCHANGES),
            "configured": bool(ak and b.get("api_secret") and b.get("passphrase")),
            "key_hint": masked,
            "has_secret": bool(b.get("api_secret")),
            "has_passphrase": bool(b.get("passphrase")),
            "paper": self.cfg.paper,
        }

    async def ping(self) -> dict:
        """连通性 + 密钥有效性自检（用本页独立凭据，与首页账户隔离）。"""
        c = self.current_creds()
        if not c.configured:
            return {"ok": False, "error": "未配置形态页 API 密钥"}
        try:
            with creds.use(c):
                r = await trade.ping(sim=self.cfg.paper)
            return r
        except Exception as e:
            return {"ok": False, "error": f"连通性检查失败: {e}"}

    async def test_order(self, symbol: str) -> dict:
        """手动挂一笔测试单，验证本页密钥 / 杠杆 / 下单链路是否通。

        挂在盘口 -3%（LimitPriceRatio 上限 5%），正常不成交，可撤单。
        不进入持仓状态机 —— 只验证链路，不参与止盈止损。
        """
        c = self.current_creds()
        if not c.configured:
            return {"ok": False, "error": "未配置形态页 API 密钥"}
        sym = _okx_symbol(symbol) or (symbol or "")
        if not sym:
            return {"ok": False, "error": "未指定测试品种"}
        price = await self._price(sym)
        if not price:
            return {"ok": False, "error": f"{sym} 暂无最新价"}
        paper = self.cfg.paper
        # 杠杆是品种级字段（存在 SymbolTradeConfig 上），全局 PatternConfig 没有 leverage，
        # 必须从品种配置取，不能用 self.cfg.leverage；未加入列表的品种默认 3 与 add_symbol 一致。
        sc = self.symbols.get(sym)
        lev = int(sc.leverage or 3) if sc else 3
        try:
            with creds.use(c):
                if self.cfg.category != "SPOT":
                    await trade.set_leverage(sym, lev,
                                             self.cfg.margin_mode, sim=paper)
                px = price * 0.97
                r = await trade.place_order(
                    sym, "buy", px,
                    margin_usdt=10.0,
                    leverage=lev,
                    category=self.cfg.category,
                    mgn_mode=self.cfg.margin_mode,
                    client_oid=f"ptest{int(time.time())}",
                    sim=paper,
                    ref_price=price,
                )
            r["note"] = "形态页测试单挂在盘口 -3%，正常不成交，可撤单；不进入持仓管理"
            if r.get("ok"):
                self.orders.append({**r, "kind": "test", "tf": "test",
                                    "sig_type": "buy", "sym": sym})
                self.orders = self.orders[-300:]
            return r
        except Exception as e:
            logger.exception(f"[形态测试单] {sym} 下单异常")
            return {"ok": False, "error": f"测试单失败: {e}"}

    def current_creds(self) -> creds.Creds:
        ex = (self.cfg.exchange or "okx").lower()
        b = dict(self._keys.get(ex) or {})
        return creds.Creds(
            exchange=ex,
            api_key=(b.get("api_key") or "").strip(),
            api_secret=(b.get("api_secret") or "").strip(),
            passphrase=(b.get("passphrase") or "").strip(),
            paper=self.cfg.paper,
            label="pattern",
        )

    @property
    def rules(self) -> ExitRules:
        """回测验证的那套出场参数（TP1 平部分 + 保本 + 跟随 ST + 轨道无效硬止损）。"""
        return ExitRules(
            tp1_pct=self.cfg.tp1_pct,
            tp1_ratio=self.cfg.tp1_ratio,
            sl_mode="st",
            sl_pct=self.cfg.sl_pct,
        move_sl_to_entry=self.cfg.move_sl_to_entry,
        trail_with_st=self.cfg.trail_with_st,
        reverse_close=self.cfg.reverse_close,
    )

    def rules_for_symbol(self, symbol: str) -> ExitRules:
        """该品种出场规则：品种独立覆盖优先，未设置则回落全局默认档。"""
        sc = self.symbols.get(symbol)
        c = self.cfg

        def _v(sym_v, def_v):
            return sym_v if sym_v is not None else def_v

        return ExitRules(
            tp1_pct=_v(sc.tp1_pct if sc else None, c.tp1_pct),
            tp1_ratio=_v(sc.tp1_ratio if sc else None, c.tp1_ratio),
            sl_mode="st",
            sl_pct=_v(sc.sl_pct if sc else None, c.sl_pct),
            move_sl_to_entry=_v(sc.move_sl_to_entry if sc else None, c.move_sl_to_entry),
            trail_with_st=_v(sc.trail_with_st if sc else None, c.trail_with_st),
            reverse_close=_v(sc.reverse_close if sc else None, c.reverse_close),
        )

    async def close_symbol(self, symbol: str, reason: str = "手动平仓") -> dict:
        """页面手动平仓。请求来自别的 Task，必须显式包成本页凭据再下单。"""
        sym = _okx_symbol(symbol)
        st = self.stores.get(sym)
        if not st or not st.position:
            return {"ok": False, "error": "无持仓"}
        px = await self._price(sym)
        with creds.use(self.current_creds()):
            await self.executors[sym]._close(st.position, reason, px or st.position.entry)
        self.save()
        return {"ok": True}

    # ── 后台循环 ──────────────────────────────────────────────
    async def run(self):
        """常驻任务。开头绑一次凭据，本 Task 的每次下单都用它，与首页互不影响。"""
        creds.bind(self.current_creds())
        self._running = True
        logger.info("[形态下单] 后台循环已启动（paper=%s，品种 %d 个）",
                    self.cfg.paper, len(self.symbols))
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[形态下单] 轮询异常: {e}")
            await asyncio.sleep(max(5, self.cfg.poll_sec))

    def stop(self):
        self._running = False

    async def _tick(self):
        if not self.cfg.enabled:
            return
        c = self.current_creds()
        creds.bind(c)  # 配置可能被改过，每次轮询刷新一次
        if not c.configured:
            return
        for sym, sc in list(self.symbols.items()):
            if not sc.enabled:
                continue
            try:
                await self._tick_symbol(sym, sc)
            except Exception as e:
                logger.error(f"[形态下单] {sym} 处理失败: {e}")

    async def _tick_symbol(self, sym: str, sc: SymbolTradeConfig):
        store, ex = self.stores[sym], self.executors[sym]
        price = await self._price(sym)
        if price:
            store.ticker.last = price
        # 1) 有持仓先管出场（TP1 分批 / 保本 / 跟踪止损 / 反向平仓）
        if store.position and price:
            await ex.on_price(price)
            return
        if store.position:
            return
        # 2) 无持仓：找允许周期内最新的 ST 翻转
        for tf in sc.allow_tfs:
            sig = await self._latest_signal(sym, tf)
            if not sig:
                continue
            key = (sym, tf)
            if self._last_flip.get(key) is None:
                self._last_flip[key] = sig["ts"]   # 首次仅记录，不给历史信号补单
                continue
            if sig["ts"] <= self._last_flip[key]:
                continue
            self._last_flip[key] = sig["ts"]
            if self.cfg.cooldown_sec > 0:
                last = self._last_order_at.get(key, 0)
                if time.time() - last < self.cfg.cooldown_sec:
                    continue
            # 3) 过滤器：4h 方向 + 4 条规则过滤（趋势形态识别.md，品种独立开关）
            if self.cfg.block_4h and not await self._allow_by_4h(sym, sig):
                continue
            if not await self._allow_by_filter(sym, sc, sig):
                continue
            self._last_order_at[key] = time.time()
            await ex.on_signal(sig, {"trade": True, "profile": "normal"})

    # ── 数据 ──────────────────────────────────────────────────
    async def _kline(self, sym: str, tf: str, limit: int = 500) -> list[dict]:
        kl = await asyncio.to_thread(history.fetch_candles, tf, limit, sym)
        return [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol}
                for c in (kl or [])]

    async def _price(self, sym: str) -> float:
        try:
            kl = await self._kline(sym, "1m", 2)
            return float(kl[-1]["c"]) if kl else 0.0
        except Exception as e:
            logger.warning(f"[形态下单] {sym} 取价失败: {e}")
            return 0.0

    async def _latest_signal(self, sym: str, tf: str) -> dict | None:
        """该周期最新一根 SuperTrend 翻转信号（原始参数 10/3.0）。"""
        try:
            cs = await self._kline(sym, tf, 500)
            if len(cs) < 50:
                return None
            st = super_trend(
                [c["o"] for c in cs], [c["h"] for c in cs],
                [c["l"] for c in cs], [c["c"] for c in cs],
                periods=ST_PERIODS, multiplier=ST_MULTIPLIER, change_atr=True,
            )
            flips = st.get("flips") or []
            if not flips:
                return None
            f = flips[-1]
            i = f["i"]
            if i >= len(cs):
                return None
            c = cs[i]
            typ = f["type"]
            return {
                "symbol": sym, "tf": tf, "type": typ, "ts": c["ts"],
                "price": c["c"],
                "line": (st["up_plot"][i] if typ == "buy" else st["dn_plot"][i]),
            }
        except Exception as e:
            logger.warning(f"[形态下单] {sym} {tf} 信号计算失败: {e}")
            return None

    async def _allow_by_4h(self, sym: str, sig: dict) -> bool:
        """True=放行。4h 无明显趋势 / 数据不足一律放行，只拦明确反向。"""
        import bisect
        cs = await self._kline(sym, "4h", 600)
        if not cs:
            return True
        cached = self._pat_cache.get(sym)
        if cached and cached[0] == cs[-1]["ts"]:
            _, pts, pmap = cached
        else:
            pat = recognize_pattern(
                [{"ts": c["ts"], "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"]} for c in cs]
            )["pattern"]
            pts = [p["ts"] for p in pat]
            pmap = {p["ts"]: p for p in pat}
            self._pat_cache[sym] = (cs[-1]["ts"], pts, pmap)
        if not pts:
            return True
        idx = bisect.bisect_right(pts, sig["ts"]) - 1
        if idx < 0:
            return True
        pdir = pmap[pts[idx]].get("dir")
        return pdir != -sig_dir(sig)

    async def _allow_by_filter(self, sym: str, sc: "SymbolTradeConfig", sig: dict) -> bool:
        """True=放行。当前唯一过滤策略是 V3 趋势过滤，品种独立开关，默认关=放行。

        V3（signal_v3 打分体系）：
          ST Signal → Trend Gate(成熟趋势) / Early Breakout V2(早期启动) → Trend Score
          → Range Penalty(两级 Fuse) → Execute
        数据不足 / 信号根对不上 → 一律放行。
        """
        if not getattr(sc, "filter_v3", False):
            return True
        ok, why = await self._v3_allow(sym, sc, sig)
        if not ok:
            logger.info("[形态下单] %s %s V3拦截: %s", sym, sig.get("tf") or "1h", why)
        return ok

    async def _v3_allow(self, sym: str, sc, sig: dict):
        """V3 趋势过滤：Trend Score 通过（成熟趋势 / 早期启动）才放行。

        与回测 build_signal_features_1h.py 共用 backend/signal_v3.py，口径单一。
        注意：V3 是在「1h 信号 + 4h 上下文」上拟合的，其它周期开启请谨慎。
        数据不足 / 信号根对不上 → 一律放行。
        返回 (是否放行, 说明)。
        """
        tf = sig.get("tf") or "1h"
        try:
            cs = await self._kline(sym, tf, 600)
            cs4 = await self._kline(sym, "4h", 300)
        except Exception as e:
            logger.warning(f"[形态下单] {sym} {tf} V3 取数失败: {e}")
            return True, "取数失败放行"
        if len(cs) < 120 or len(cs4) < 60:
            return True, "数据不足放行"

        st = super_trend([x["o"] for x in cs], [x["h"] for x in cs],
                         [x["l"] for x in cs], [x["c"] for x in cs],
                         periods=ST_PERIODS, multiplier=ST_MULTIPLIER, change_atr=True)
        flips = st.get("flips") or []
        if not flips:
            return True, "无翻转放行"
        fd = flips[-1]
        i = fd["i"]
        if i >= len(cs) or cs[i]["ts"] != sig.get("ts"):
            return True, "信号根对不上放行"   # 数据延迟，不拦

        sd = 1 if fd["type"] == "buy" else -1
        feats = signal_v3.features_from_candles(
            cs, i, sd, cs4, st_periods=ST_PERIODS, st_mult=ST_MULTIPLIER)
        if not feats:
            return True, "特征不足放行"
        v = signal_v3.v3_decide(sd, feats)
        return v["execute"], f"{v['path']} score={v['score']}"


def pattern_score_detail(closes, highs, lows, opens, vols, h4_closes=None, h4_highs=None,
                          h4_lows=None, sd=1, break_price=None) -> dict:
    """形态识别页综合评分明细，规则见 趋势形态识别.md（评分规则 2026-09 修订版，5 维度）。

    返回 dict：m1=4H趋势环境(≤30), m2=突破爆发力(≤25), m3=动量量能(≤25),
               m4=空间(≤20), pen=反转风险扣分(≤−30),
               total=m1+m2+m3+m4+pen（区间 [−30, 100]）。
    模块规则与 pattern_score 完全一致，此处额外返回各分项以便逐笔审计。
    """
    if len(closes) < 30:
        return {"m1": 0.0, "m2": 0.0, "m3": 0.0, "m4": 0.0, "pen": 0.0, "total": 0.0}
    atr = ta_atr(highs, lows, closes, 14)
    atr_v = atr[-1] or 0.0
    if atr_v <= 0:
        return {"m1": 0.0, "m2": 0.0, "m3": 0.0, "m4": 0.0, "pen": 0.0, "total": 0.0}

    N = 20
    win_h = highs[-N - 1:-1]          # 信号根前 N=20 根箱体（不含突破根本身）
    win_l = lows[-N - 1:-1]
    if not win_h or not win_l:
        return {"m1": 0.0, "m2": 0.0, "m3": 0.0, "m4": 0.0, "pen": 0.0, "total": 0.0}
    rng_h, rng_l = max(win_h), min(win_l)
    rng = rng_h - rng_l
    c = closes[-1]
    o = opens[-1]
    v = vols[-1] if vols else 0.0
    pos = (c - rng_l) / rng if rng > 0 else 0.5

    # ── 维度一：4H 趋势环境（30）──
    # 30：1H ST 与 4H ST 同向 且 价在 4H EMA20 同侧
    # 15：同向但价在 4H EMA20 异侧（回调突破）
    #  0：1H ST 与 4H ST 逆势（逆大势，归零）
    m1 = 0.0
    if h4_closes and len(h4_closes) >= 5:
        st4 = super_trend(h4_closes, h4_highs, h4_lows, h4_closes,
                          periods=ST_PERIODS, multiplier=ST_MULTIPLIER, change_atr=True)
        t4 = st4.get("trend") or []
        if t4:
            sd4 = 1 if t4[-1] > 0 else -1
            ema20_4h = ta_ema(h4_closes, 20)[-1] or 0.0
            if sd == sd4:
                same_side = (c > ema20_4h) if sd > 0 else (c < ema20_4h)
                m1 = 30.0 if same_side else 15.0
            else:
                m1 = 0.0
        else:
            m1 = 15.0
    else:
        m1 = 15.0   # 4H 数据不足，无法确认环境，给中性分

    # ── 维度二：突破爆发力与穿透（25）──
    # 25（绝对脱离）：突破箱体 且 实体幅度 ≥ 0.8×ATR
    # 15（温和脱离）：突破箱体 但实体幅度较小
    #  5（箱体边缘）：未完全突破但处箱体前 20% 区域（edge>0.8）
    #  0（箱体中轴）：0.3 ≤ Pos ≤ 0.7
    body = abs(c - o)
    body_atr = body / atr_v if atr_v > 0 else 0.0
    if sd > 0:
        breakout = c > rng_h
        edge = pos
    else:
        breakout = c < rng_l
        edge = 1 - pos
    if breakout:
        m2 = 25.0 if body_atr >= 0.8 else 15.0
    elif edge > 0.8:
        m2 = 5.0
    else:
        m2 = 0.0

    # ── 维度三：短线动量与量能（25）──
    # 方向对齐的「顺势动量」（严禁 abs 方向中性，否则会把下跌途中接飞刀误判为多头动量）：
    #   buy : Mom12_Signed = (C−C12)/C×100%； ≥+1.2%→15； +0.5%≤·<+1.2%→8； <+0.5%→0
    #   sell: Mom12_Signed ≤−1.2%→15； −1.2%<·≤−0.5%→8； >−0.5%→0
    # 强爆发豁免（顺向）：buy Mom12_Signed≥+2.0% 或 sell≤−2.0% → 本项直接满分 25
    # 量能配合（独立，与方向无关）：突破K成交量 Vol ≥ 1.3×MA(Vol,20) → +10
    mom12_signed = ((c - closes[-13]) / closes[-13] * 100.0
                    if len(closes) >= 14 and closes[-13] else 0.0)
    if sd > 0:
        exempt = mom12_signed >= 2.0
        m31 = 15.0 if mom12_signed >= 1.2 else (8.0 if mom12_signed >= 0.5 else 0.0)
    else:
        exempt = mom12_signed <= -2.0
        m31 = 15.0 if mom12_signed <= -1.2 else (8.0 if mom12_signed <= -0.5 else 0.0)
    if exempt:
        m3 = 25.0
    else:
        vol_ma20 = ta_sma(vols, 20)[-1] if vols else 0.0
        m32 = 10.0 if (vol_ma20 > 0 and v >= vol_ma20 * 1.3) else 0.0
        m3 = min(25.0, m31 + m32)

    # ── 维度四：盈亏比与空间（20）──
    # 20：距前方 24H 强阻力/支撑 > 2.5×ATR
    # 10：1.0 ~ 2.5×ATR
    #  0：< 1.0×ATR（空间极小）
    # 前方强阻力/支撑取最近摆动高低点（最近 48 根≈2 日, k=5；24H 近似）
    PN = min(len(highs), 48)
    ph_sub = highs[-PN:]
    pl_sub = lows[-PN:]
    phs = [ph_sub[i] for i in range(5, len(ph_sub) - 5)
           if ph_sub[i] == max(ph_sub[i - 5:i + 6])]
    pls = [pl_sub[i] for i in range(5, len(pl_sub) - 5)
           if pl_sub[i] == min(pl_sub[i - 5:i + 6])]
    if sd > 0:
        above = [p for p in phs if p > c]           # 上方最近压力位
        space_atr = (min(above) - c) / atr_v if above else 99.0
    else:
        below = [p for p in pls if p < c]           # 下方最近支撑
        space_atr = (c - max(below)) / atr_v if below else 99.0
    if space_atr > 2.5:
        m4 = 20.0
    elif space_atr >= 1.0:
        m4 = 10.0
    else:
        m4 = 0.0

    # ── 维度五：反转与追高风险扣分（≤−30）──
    # −15（追高/杀跌）：|Close − EMA20| > 2.0×ATR（严重偏离结构，开在极端位）
    # −15（缩量伪突破）：Vol < 0.7×MA(Vol,20)（无量拉升/砸盘）
    pen = 0.0
    ema20_1h = ta_ema(closes, 20)[-1] or 0.0
    if ema20_1h and abs(c - ema20_1h) > 2.0 * atr_v:
        pen -= 15
    vol_ma20 = ta_sma(vols, 20)[-1] if vols else 0.0
    if vol_ma20 > 0 and v < vol_ma20 * 0.7:
        pen -= 15
    pen = max(-30.0, pen)

    total = m1 + m2 + m3 + m4 + pen
    total = max(-30.0, min(100.0, total))
    return {"m1": round(m1, 1), "m2": round(m2, 1), "m3": round(m3, 1),
            "m4": round(m4, 1), "pen": round(pen, 1), "total": round(total, 1)}


def pattern_score(closes, highs, lows, opens, vols, h4_closes=None, h4_highs=None,
                 h4_lows=None, sd=1, break_price=None) -> float:
    """形态识别页综合评分（区间 [−30, 100]），规则见 趋势形态识别.md（评分规则 2026-09 修订版）。

    Score = 4H趋势环境(≤30) + 突破爆发力(≤25) + 动量量能(≤25) + 空间(≤20) − 反转风险(≤−30)
      维度一 4H趋势环境(30)：1H ST 与 4H ST 同向 且 价在 4H EMA20 同侧→30；
                            同向异侧(回调突破)→15；逆势→0
      维度二 突破爆发力(25)：突破箱体(Cross>RangeHigh/Low)且实体≥0.8ATR→25；温和突破→15；
                            edge>0.8(箱体前20%)→5；中轴(0.3≤Pos≤0.7)→0
      维度三 动量量能(25)：buy Mom12_Signed≥+1.2%→15，+0.5%≤·<+1.2%→8，<+0.5%→0（sell 对称取负）；
                             Vol≥1.3×MA20(带量)→10；buy Mom12_Signed≥+2.0%(或sell≤−2.0%)触发【强爆发豁免】直接 25
      维度四 空间(20)：前方摆动高低点距离 >2.5ATR→20；1.0~2.5ATR→10；<1.0ATR→0
      维度五 反转风险(≤−30)：|C−EMA20|>2.0ATR(追高/杀跌) −15；Vol<0.7×MA20(缩量) −15
    逐笔模块明细见 pattern_score_detail（返回 m1/m2/m3/m4/pen/total 字典）。
    """
    return pattern_score_detail(closes, highs, lows, opens, vols, h4_closes, h4_highs,
                                 h4_lows, sd, break_price)["total"]


def sig_dir(sig: dict) -> int:
    return 1 if sig["type"] == "buy" else -1


# 全局单例
trader = PatternTrader()
