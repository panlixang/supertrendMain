"""形态识别页 · 独立自动下单（配置 / 凭据 / 持仓 / 执行循环全部独立于首页）。

与首页刻意「各自一套」：
- 配置文件 pattern_trade.json、凭据文件 pattern_credentials.json，都不进 settings.json
- 下单通过 creds 的 ContextVar 覆盖 —— Task 级隔离，两页可跑不同交易所 / 不同子账户
- 信号用基础周期的「原始 SuperTrend」（ATR 10 / factor 3.0），与首页 15/9.1 无关
- 过滤两个开关：block_4h（4h 形态反向拦截）+ trend_filter（综合趋势过滤：Squeeze 死水区拦截 + Donchian 突破放行）
- 出场用 position.ExitRules 默认档 = 回测验证过的 TP1 1.5% 平 70% + 保本 + 跟随 ST 跟踪

同一品种两页都可能下单时不校验冲突（用户用不同子账户各自管理）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import creds
import history
import trade
from executor import Executor
from indicators import ma, super_trend, ta_adx, ta_atr, ta_sma
from pattern_recog import recognize as recognize_pattern
from position import ExitRules
from regime import TradeConfig
from state import SymbolStore, SymbolTradeConfig

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
    # 综合评分过滤（趋势形态识别.md 2026-09 评分规则）：有效ST分 = 结构40+动量25+突破质量25−反转风险(最多−20)
    # 两阶段机制：score>=score_min(默认50) 直接放行；<50 不立即交易，需下一根K突破确认才执行（见 _allow_by_score）
    score_filter:       bool  = True    # True = 开启综合评分过滤
    score_min:          float = 60.0    # 综合评分硬门槛（自定义，0-100；<50 须突破确认；寻优最优=60）
    # 综合趋势过滤器（替代原「无趋势拦截」），公式：
    #   Allow = Align4H AND NOT Squeeze AND Donchian
    #   Align4H 由 block_4h 负责；本过滤器负责 Squeeze 死水区拦截 + Donchian 突破放行
    trend_filter:       bool  = True    # True = 开启综合趋势过滤
    # 条件 1：Squeeze 极度窄幅死水区（极窄幅布林收口 AND 缩量，须同时满足才判死水）
    squeeze_bb_n:       int   = 20      # 布林带周期
    squeeze_bb_mult:    float = 2.0     # 布林带倍数
    squeeze_width_pct:  float = 1.5     # 带宽% 低于该值 = 极窄幅（死水）
    squeeze_vol_n:      int   = 20      # 量能 MA 周期
    squeeze_vol_mult:   float = 0.8     # 当前量 < MA(Vol,n)*该倍数 = 缩量
    # 条件 2：Donchian 通道突破放行（价格收在前 N 根高低点之外）
    donchian_n:         int   = 20      # 通道周期

    cooldown_sec: int   = 300      # 同一品种同一周期两次下单最小间隔
    poll_sec:     int   = 20       # 轮询间隔
    # 出场参数（回测验证档，面板可配置）
    tp1_pct:         float = 1.5
    tp1_ratio:       float = 70.0
    sl_pct:          float = 2.0    # 初始止损兜底（SuperTrend 轨道无效时用）
    move_sl_to_entry: bool  = True   # 止盈后止损移到开仓价保本
    trail_with_st:    bool  = True   # 剩余仓位跟随 SuperTrend 跟踪


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
                   "move_sl_to_entry", "trail_with_st")
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
            # 3) 过滤器：4h 方向 + 综合评分 + 综合趋势过滤
            if self.cfg.block_4h and not await self._allow_by_4h(sym, sig):
                continue
            if self.cfg.score_filter and not await self._allow_by_score(sym, sig):
                continue
            if self.cfg.trend_filter and not await self._allow_by_trend(sym, sig):
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

    async def _allow_by_trend(self, sym: str, sig: dict) -> bool:
        """True=放行。综合趋势过滤（替代原「无趋势拦截」）。

        公式：Allow = Align4H AND NOT Squeeze AND Donchian
          - Align4H 由 block_4h + _allow_by_4h 在调用处负责（顺大势硬门槛）
          - 条件 1 Squeeze：极窄幅(布林收口) AND 缩量 → 死水区，一律拦截
          - 条件 2 Donchian：价格收在前 N 根高低点之外 → 有效突破，放行
        数据不足一律放行，不拦。
        核心判定委托给模块级纯函数 trend_gate，与 /api/pattern 页面展示、回测三处共用。
        """
        tf = sig.get("tf") or "1h"
        try:
            cs = await self._kline(sym, tf, 600)
        except Exception as e:
            logger.warning(f"[形态下单] {sym} {tf} 趋势过滤取数失败: {e}")
            return True
        cfg = self.cfg
        need = max(cfg.squeeze_bb_n, cfg.squeeze_vol_n,
                   cfg.donchian_n + 1)
        if len(cs) < need + 5:
            return True
        closes = [c["c"] for c in cs]
        highs = [c["h"] for c in cs]
        lows = [c["l"] for c in cs]
        vols = [c["vol"] for c in cs]
        sd = sig_dir(sig)
        allow, reason, _ = trend_gate(closes, highs, lows, vols,
                                      len(closes) - 1, sd, cfg)
        logger.info("[形态下单] %s %s 趋势过滤: %s（%s）", sym, tf,
                    "放行" if allow else "拦截", reason)
        return allow

    async def _allow_by_score(self, sym: str, sig: dict) -> bool:
        """True=放行。综合评分过滤（趋势形态识别.md 评分规则）。
        score >= cfg.score_min 才放行；数据不足一律放行。
        """
        tf = sig.get("tf") or "1h"
        try:
            cs = await self._kline(sym, tf, 600)
            cs4h = await self._kline(sym, "4h", 600)
        except Exception as e:
            logger.warning(f"[形态下单] {sym} {tf} 综合评分取数失败: {e}")
            return True
        if len(cs) < 60 or len(cs4h) < 60:
            return True
        score = pattern_score(
            [c["c"] for c in cs], [c["h"] for c in cs], [c["l"] for c in cs],
            [c["o"] for c in cs], [c["vol"] for c in cs],
            [c["c"] for c in cs4h], [c["h"] for c in cs4h], [c["l"] for c in cs4h],
            sig_dir(sig), sig.get("price"),
        )
        if score >= self.cfg.score_min:
            logger.info("[形态下单] %s %s 综合评分放行: %.1f >= %.1f",
                        sym, tf, score, self.cfg.score_min)
            return True
        logger.info("[形态下单] %s %s 综合评分拦截: %.1f < %.1f",
                    sym, tf, score, self.cfg.score_min)
        return False


def trend_gate(closes: list[float], highs: list[float], lows: list[float],
               vols: list[float], idx: int, sd: int, cfg) -> tuple[bool, str, str]:
    """综合趋势过滤纯函数（页面 / 实盘 / 回测 三处共用，保证判定完全一致）。

    决策树（对应形态页过滤，idx 仅保留调用对称性——判定始终针对传入序列的
    最后一

根，调用方按需把序列截断到「当前 bar」即可）：
      ① 数据不足                          → 放行
      ② Squeeze 死水区(极窄幅 AND 缩量)    → 拦截
      ③ Donchian 突破(收在前 N 根高低点外) → 放行
      ④ 否则                              → 拦截
    返回 (是否放行, 原因, 决定闸门)。
    """
    n = len(closes)
    need = max(cfg.squeeze_bb_n, cfg.squeeze_vol_n,
               cfg.donchian_n + 1)
    if n < need + 5:
        return True, "数据不足(放行)", "data"
    # —— 条件 1：Squeeze 极度窄幅死水区（极窄幅 AND 缩量，须同时满足）——
    bb_n = cfg.squeeze_bb_n
    mid = ta_sma(closes, bb_n)[-1]
    win = closes[-bb_n:]
    var = sum((x - mid) ** 2 for x in win) / bb_n
    std = var ** 0.5
    upper = mid + cfg.squeeze_bb_mult * std
    lower = mid - cfg.squeeze_bb_mult * std
    width_pct = (upper - lower) / mid * 100.0 if mid else 0.0
    vn = cfg.squeeze_vol_n
    vol_ma = ta_sma(vols, vn)[-1]
    low_vol = vol_ma > 0 and vols[-1] < vol_ma * cfg.squeeze_vol_mult
    squeeze = (width_pct < cfg.squeeze_width_pct) and low_vol
    if squeeze:
        return False, (f"死水区(带宽{width_pct:.2f}%<{cfg.squeeze_width_pct}%"
                       f"且缩量)"), "squeeze"
    # —— 条件 2：Donchian 通道突破（价格收在前 N 根高低点之外）——
    N = cfg.donchian_n
    win_h = highs[-N - 1:-1]
    win_l = lows[-N - 1:-1]
    don = False
    if len(win_h) >= N:
        hh, ll = max(win_h), min(win_l)
        don = (closes[-1] > hh) if sd > 0 else (closes[-1] < ll)
    if don:
        return True, f"Donchian突破(+{N})", "donchian"
    return False, "无Donchian突破", "none"


def pattern_score(closes, highs, lows, opens, vols, h4_closes=None, h4_highs=None,
                 h4_lows=None, sd=1, break_price=None) -> float:
    """形态识别页综合评分（0-100），规则见 趋势形态识别.md（2026-09 修订）。

    有效ST分 = 结构突破(40) + 动量持续(25) + 突破质量(25) − 反转风险(最多−20)
      模块1 结构突破(40)：价格是否脱离原震荡箱体（Position = (Close-RangeLow)/(RangeHigh-RangeLow)，
                          Range 取信号根前 N=20 根 1h 的 High/Low）
                          多头：箱体外突破(Close>RangeHigh)+40 / 箱体边缘(Position>0.8)+25 / 中间 0
                          空头对称
      模块2 动量持续(25)：Momentum=(Close-Close12)/ATR（方向化）；>2→25, 1~2→15, 0.5~1→8, <0.5→0
                          单根K修正：当前K涨幅/12周期涨幅>70% → −10
      模块3 突破质量(25)：Volume Ratio=Vol/MA20Vol；>1.5→25, 1.2~1.5→15, 0.8~1.2→8, <0.8→0
      模块4 反转风险扣分(最多−20)：
                          A 长影线(上影/实体>1.5)→−10
                          B 远离结构(突破后离箱体>3ATR)→−15
                          C ADX未跟随(价格突破但ADX下降)→−10
    注：2026-09 修订后评分完全基于 1h 维度，不再含 4H 方向 / 市场环境正分模块（h4_* 保留仅为兼容旧调用）。
    sd: 信号方向 +1(buy)/-1(sell)。break_price: 信号触发价（用于远离结构判定）。
    """
    if len(closes) < 30:
        return 0.0
    atr = ta_atr(highs, lows, closes, 14)
    atr_v = atr[-1] or 0.0
    if atr_v <= 0:
        return 0.0
    N = 20
    win_h = highs[-N - 1:-1]
    win_l = lows[-N - 1:-1]
    if not win_h or not win_l:
        return 0.0
    rng_h, rng_l = max(win_h), min(win_l)
    rng = rng_h - rng_l
    c = closes[-1]
    pos = (c - rng_l) / rng if rng > 0 else 0.5

    # ── 模块1：结构突破（40）──
    if sd > 0:
        if c > rng_h:
            m1 = 40.0            # 箱体外突破（最强）
        elif pos > 0.8:
            m1 = 25.0            # 箱体边缘
        else:
            m1 = 0.0             # 箱体中间：ST翻转大概率是垃圾
    else:
        if c < rng_l:
            m1 = 40.0
        elif pos < 0.2:
            m1 = 25.0
        else:
            m1 = 0.0

    # ── 模块2：动量持续（25）+ 单根K修正 ──
    mom12 = (closes[-1] - closes[-13]) / atr_v * sd          # 方向化 12 周期动量
    if mom12 > 2:
        m2 = 25.0
    elif mom12 >= 1:
        m2 = 15.0
    elif mom12 >= 0.5:
        m2 = 8.0
    else:
        m2 = 0.0
    move12 = (closes[-1] - closes[-13]) * sd                 # 12 周期方向化总位移
    cur_k = (closes[-1] - opens[-1]) * sd                    # 当前K方向化位移
    if move12 > 0 and cur_k / move12 > 0.7:                  # 全靠一根K推动 → 扣10
        m2 = max(0.0, m2 - 10.0)

    # ── 模块3：突破质量（25）─ 成交量比 ──
    vol_ma = ta_sma(vols, 20)[-1]
    vr = (vols[-1] / vol_ma) if vol_ma else 1.0
    if vr > 1.5:
        m3 = 25.0
    elif vr >= 1.2:
        m3 = 15.0
    elif vr >= 0.8:
        m3 = 8.0
    else:
        m3 = 0.0

    # ── 模块4：反转风险扣分（最多−20）──
    pen = 0.0
    o, h, l = opens[-1], highs[-1], lows[-1]
    if sd > 0:
        body = max(c - o, 1e-9); shadow = h - max(o, c)       # A 长影线（上影）
    else:
        body = max(o - c, 1e-9); shadow = max(o, c) - l       #   空头看下影
    if shadow / body > 1.5:
        pen -= 10
    if sd > 0:
        gap = (c - rng_h) / atr_v                            # B 远离结构（突破后离箱体）
    else:
        gap = (rng_l - c) / atr_v
    if gap > 3:
        pen -= 15
    adx_s = ta_adx(highs, lows, closes, 14)                  # C ADX 未跟随（价格突破但下降）
    if len(adx_s) >= 6 and (adx_s[-1] or 0) < (adx_s[-6] or 0):
        pen -= 10
    pen = max(-20.0, pen)

    total = m1 + m2 + m3 + pen
    return max(0.0, min(100.0, total))


def sig_dir(sig: dict) -> int:
    return 1 if sig["type"] == "buy" else -1


# 全局单例
trader = PatternTrader()
