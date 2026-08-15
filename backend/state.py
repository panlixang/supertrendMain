"""
全局共享状态

多品种并行架构：
    stores[symbol] = SymbolStore   交易品种，K线/持仓/信号/参数各自独立
    view_store                     仅看图的品种（不在交易列表里时临时加载）
    current_symbol                 图表当前显示哪个品种（view 指针，不影响交易）

AppState 上保留一组「视图委托」属性（ticker / params / signals / candles_by_tf…），
全部指向 current_symbol 对应的 store —— 图表 / 回测 / 参数页的代码不用感知多品种。

周期集合与 Pine 脚本 «Signal Engine Quantum Edge» 的 MTF Bias 表对齐：
5m / 15m / 30m / 1H / 4H / 1D / 1W / 1M，另加 1m 供短线看盘。
"""

from collections import deque
from dataclasses import dataclass, field, replace
import json
import logging
import os
from typing import Optional

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# 面板参数的持久化文件：改动即写、启动即读，重启不再丢配置
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

# 同时交易的品种上限（低配服务器实测 5 个以内 CPU/内存都很宽裕）
MAX_SYMBOLS = 5


@dataclass
class Ticker:
    symbol: str = "BTC-USDT"
    last: float = 0.0
    open24h: float = 0.0
    high24h: float = 0.0
    low24h: float = 0.0
    vol24h: float = 0.0
    change24h: float = 0.0
    ts: int = 0


@dataclass
class Candle:
    ts: int          # 毫秒
    o: float
    h: float
    l: float
    c: float
    vol: float
    confirm: bool = False   # 该 K 线是否已收盘


# maxlen / OKX bar / OKX WS channel / 前端显示名
TF_CONFIG = {
    "1m":  {"maxlen": 600, "okx_bar": "1m",  "okx_channel": "candle1m",  "label": "1m"},
    "5m":  {"maxlen": 600, "okx_bar": "5m",  "okx_channel": "candle5m",  "label": "5m"},
    "15m": {"maxlen": 800, "okx_bar": "15m", "okx_channel": "candle15m", "label": "15m"},
    "30m": {"maxlen": 800, "okx_bar": "30m", "okx_channel": "candle30m", "label": "30m"},
    "1h":  {"maxlen": 800, "okx_bar": "1H",  "okx_channel": "candle1H",  "label": "1H"},
    "4h":  {"maxlen": 800, "okx_bar": "4H",  "okx_channel": "candle4H",  "label": "4H"},
    "1d":  {"maxlen": 600, "okx_bar": "1D",  "okx_channel": "candle1D",  "label": "1D"},
    "1w":  {"maxlen": 300, "okx_bar": "1W",  "okx_channel": "candle1W",  "label": "1W"},
    "1M":  {"maxlen": 200, "okx_bar": "1M",  "okx_channel": "candle1M",  "label": "1M"},
}

TFS = list(TF_CONFIG.keys())

# MTF Bias 表的行（对应 Pine 里的 bias_5m … bias_1mo）
BIAS_TFS = ["5m", "15m", "30m", "1h", "4h", "1d", "1w", "1M"]


@dataclass
class Params:
    """对应 Pine 的 input 区块，前端可调，改动后全部重算。每个品种一份。"""
    # ▸ Supertrend Settings
    periods:    int   = 15        # ATR Period
    multiplier: float = 9.1       # ATR Multiplier
    src:        str   = "hl2"     # Source
    change_atr: bool  = True      # Change ATR Calculation Method (True=ta.atr/RMA)
    # ▸ MTF Bias Table
    fast_len:   int   = 20        # Fast MA Length
    slow_len:   int   = 50        # Slow MA Length
    ma_type:    str   = "EMA"     # EMA / SMA


@dataclass
class SymbolTradeConfig:
    """单个品种的下单参数。现在所有自动下单参数都品种独立（含 ER / 止盈止损）。

    生效开关 = 全局总开关 AND 这里的 enabled（见 AppState.cfg_for）。
    """
    symbol:      str
    enabled:     bool  = False
    margin_usdt: float = 10.0
    leverage:    int   = 3
    allow_tfs:   list  = field(default_factory=lambda: ["15m", "30m", "1h", "4h", "1d"])
    # ── ER 闸门（品种独立）──
    er_hide_below: float = 0.10
    er_weak_min:   float = 0.12
    er_min:        float = 0.15
    er_trend:      float = 0.30
    quick_enabled: bool  = False
    allow_grades:  list  = field(default_factory=lambda: ["A", "B"])
    min_score:     int   = 2
    cooldown_sec:  int   = 300
    # ── 组合过滤器（品种独立配置）──
    atr_filter_enabled:    bool  = False   # 是否启用ATR波动率过滤
    atr_vol_min:           float = 0.7     # ATR波动率最小值（当前ATR/近期均值）
    range_filter_enabled:  bool  = False   # 是否启用区间震荡过滤
    range_size_max:        float = 0.15    # 区间大小上限（高低点差/低点）
    range_touches_min:     int   = 3       # 触边次数下限（近期触碰上下轨次数）
    mtf_filter_enabled:    bool  = False   # 是否启用MTF一致性过滤
    mtf_consistency_min:   float = 0.6     # MTF一致性最小值（0~1）
    mtf_flip_max:          int   = 5       # 大周期翻转次数上限（4h+1d近期翻转）
    adx_filter_enabled:    bool  = False   # 是否启用ADX过滤
    adx_min:               float = 20.0    # ADX最小值（低于此值视为无趋势）
    adx_period:            int   = 14      # ADX计算周期


class SymbolStore:
    """一个品种的全部运行时数据。cfg=None 表示「仅看图」，结构上不可能下单。"""

    def __init__(self, symbol: str, cfg: Optional[SymbolTradeConfig] = None,
                 params: Optional[Params] = None, exit_rules = None, exit_rules_quick = None):
        self.symbol = symbol                 # 现货形式（BTC-USDT），全程作为字典键
        self.cfg = cfg
        self.params = params or Params()
        # 止盈止损规则（品种独立）
        from position import ExitRules
        self.exit_rules = exit_rules or ExitRules()
        self.exit_rules_quick = exit_rules_quick or ExitRules(
            tp1_pct=0.8, tp1_ratio=100.0, move_sl_to_entry=False,
            sl_mode="pct", sl_pct=1.0, trail_with_st=False,
        )
        self.candles: dict[str, deque] = {
            tf: deque(maxlen=c["maxlen"]) for tf, c in TF_CONFIG.items()
        }
        self.ticker = Ticker(symbol=symbol)
        self.signals: list[dict] = []
        # 当前持仓（每品种最多一笔）+ 已平仓历史
        self.position = None
        self.closed: list[dict] = []
        # {tf: 上次下单时间戳秒}，冷却按品种隔离
        self.last_order_at: dict[str, float] = {}
        self.history_loaded = False

    def candles_by_tf(self, tf: str) -> list[dict]:
        return [vars(c) for c in self.candles.get(tf, deque())]

    def all_candles(self) -> dict[str, list[dict]]:
        return {tf: self.candles_by_tf(tf) for tf in TF_CONFIG}

    def add_signal(self, sig: dict):
        key = (sig["tf"], sig["ts"])
        self.signals = [s for s in self.signals if (s["tf"], s["ts"]) != key]
        self.signals.append(sig)
        self.signals.sort(key=lambda s: s["ts"])
        self.signals = self.signals[-300:]


class AppState:
    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.current_symbol: str = "BTC-USDT"
        # 自动挂单全局配置 + 已下单记录（记录带 symbol）
        from position import ExitRules
        from regime import TradeConfig
        self.trade_cfg = TradeConfig()
        # 两套出场规则，开仓时按 ER 落在哪档选一套（见 regime.classify 的 profile）
        self.exit_rules = ExitRules()                    # 标准档：吃波段
        self.exit_rules_quick = ExitRules(               # 弱档：快进快出
            tp1_pct=0.8, tp1_ratio=100.0,   # 0.8% 一到就全平，不留尾仓
            move_sl_to_entry=False,         # 全平了没有剩余仓位，抬保本无意义
            sl_mode="pct", sl_pct=1.0,      # 固定 1%，不用超趋线
            trail_with_st=False,            # 不跟随 —— 这档就是死守 1%
        )
        self.orders: list[dict] = []
        # 多品种：交易 store + 仅看图 store + 每品种一个执行器
        self.stores: dict[str, SymbolStore] = {}
        self.view_store: Optional[SymbolStore] = None
        self.executors: dict = {}            # {symbol: Executor}，main/router 装配
        self.feed = None                     # 运行时由 main 注入
        self._load_settings()
        if not self.stores:
            # 全新启动：给默认品种建一个 store，enabled=False 等用户手动开
            self.stores[self.current_symbol] = SymbolStore(
                self.current_symbol, cfg=SymbolTradeConfig(symbol=self.current_symbol),
            )

    # ── 视图委托：图表 / 回测 / 参数页只关心 current_symbol ─────────
    @property
    def view(self) -> SymbolStore:
        s = self.stores.get(self.current_symbol)
        if s:
            return s
        if self.view_store is None or self.view_store.symbol != self.current_symbol:
            self.view_store = SymbolStore(self.current_symbol)
        return self.view_store

    @property
    def ticker(self) -> Ticker:
        return self.view.ticker

    @property
    def params(self) -> Params:
        return self.view.params

    @params.setter
    def params(self, value: Params):
        self.view.params = value

    @property
    def signals(self) -> list:
        return self.view.signals

    @signals.setter
    def signals(self, value: list):
        self.view.signals = value

    @property
    def position(self):
        return self.view.position

    @property
    def closed(self) -> list:
        return self.view.closed

    def candles_by_tf(self, tf: str) -> list[dict]:
        return self.view.candles_by_tf(tf)

    def all_candles(self) -> dict[str, list[dict]]:
        return self.view.all_candles()

    def add_signal(self, sig: dict):
        self.view.add_signal(sig)

    # ── 多品种工具 ──────────────────────────────────────────────
    def cfg_for(self, symbol: str):
        """全局配置 + 品种配置的合并视图。regime.evaluate / Executor 拿到的
        就是一份普通 TradeConfig，ER闸门/等级/周期来自品种配置。"""
        store = self.stores.get(symbol)
        sc = store.cfg if store else None
        if sc is None:
            return replace(self.trade_cfg, enabled=False)   # 仅看图/未知品种：永不下单
        return replace(
            self.trade_cfg,
            enabled=self.trade_cfg.enabled and sc.enabled,
            leverage=sc.leverage,
            amount_usdt=sc.margin_usdt,
            allow_tfs=list(sc.allow_tfs),
            # ER 闸门品种独立
            er_hide_below=sc.er_hide_below,
            er_weak_min=sc.er_weak_min,
            er_min=sc.er_min,
            er_trend=sc.er_trend,
            quick_enabled=sc.quick_enabled,
            allow_grades=list(sc.allow_grades),
            min_score=sc.min_score,
            cooldown_sec=sc.cooldown_sec,
            # 组合过滤器品种独立
            atr_filter_enabled=sc.atr_filter_enabled,
            atr_vol_min=sc.atr_vol_min,
            range_filter_enabled=sc.range_filter_enabled,
            range_size_max=sc.range_size_max,
            range_touches_min=sc.range_touches_min,
            mtf_filter_enabled=sc.mtf_filter_enabled,
            mtf_consistency_min=sc.mtf_consistency_min,
            mtf_flip_max=sc.mtf_flip_max,
        )

    async def broadcast(self, msg: dict):
        dead = set()
        for ws in self.clients:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.add(ws)
        self.clients -= dead

    def rules_for(self, profile: Optional[str], symbol: Optional[str] = None):
        """取该档的出场规则。symbol 指定则用品种独立规则，否则用全局规则。"""
        if symbol:
            store = self.stores.get(symbol)
            if store:
                return store.exit_rules_quick if profile == "quick" else store.exit_rules
        return self.exit_rules_quick if profile == "quick" else self.exit_rules

    def add_order(self, order: dict):
        self.orders.append(order)
        self.orders = self.orders[-200:]

    # ── 持久化 ──────────────────────────────────────────────────
    def save_settings(self):
        """把面板可调的配置落盘。任何一处改完调用即可，失败只警告不中断。"""
        try:
            data = {
                "trade_cfg": vars(self.trade_cfg),
                "exit_rules": vars(self.exit_rules),
                "exit_rules_quick": vars(self.exit_rules_quick),
                "current_symbol": self.current_symbol,
                "symbols": [
                    {
                        **vars(st.cfg),
                        "params": vars(st.params),
                        "exit_rules": vars(st.exit_rules),
                        "exit_rules_quick": vars(st.exit_rules_quick),
                    }
                    for st in self.stores.values() if st.cfg
                ],
                # 旧版字段：图表品种的指标参数，兼容旧代码读取
                "params": vars(self.view.params),
            }
            tmp = SETTINGS_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, SETTINGS_FILE)   # 原子替换，避免写一半断电留下残破文件
        except Exception as e:
            logger.warning(f"配置保存失败: {e}")

    def _load_settings(self):
        """启动时恢复上次的面板配置。只认识的字段才写回，代码升级加减字段不受影响。

        总开关 enabled 故意不恢复 —— 重启后必须人工确认再开，
        避免服务器重启后在没人盯的情况下自动继续下真金白银的单。
        品种级 enabled 可以恢复（被总开关压着，单独恢复无害）。
        """
        if not os.path.exists(SETTINGS_FILE):
            return
        try:
            with open(SETTINGS_FILE) as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(f"配置读取失败（忽略，用默认值）: {e}")
            return

        for key, obj in (("trade_cfg", self.trade_cfg),
                         ("exit_rules", self.exit_rules),
                         ("exit_rules_quick", self.exit_rules_quick)):
            saved = data.get(key)
            if not isinstance(saved, dict):
                continue
            for k, v in saved.items():
                if hasattr(obj, k):
                    setattr(obj, k, v)
        self.trade_cfg.enabled = False

        sym = data.get("current_symbol")
        if isinstance(sym, str) and sym:
            self.current_symbol = sym

        entries = data.get("symbols")
        if isinstance(entries, list) and entries:
            from position import ExitRules
            for e in entries[:MAX_SYMBOLS]:
                if not isinstance(e, dict) or not e.get("symbol"):
                    continue
                symbol = str(e["symbol"]).upper()
                # 从全局配置作为默认值，再用品种配置覆盖
                cfg = SymbolTradeConfig(
                    symbol=symbol,
                    margin_usdt=e.get("margin_usdt", self.trade_cfg.amount_usdt),
                    leverage=e.get("leverage", self.trade_cfg.leverage),
                    allow_tfs=e.get("allow_tfs", list(self.trade_cfg.allow_tfs)),
                    er_hide_below=e.get("er_hide_below", self.trade_cfg.er_hide_below),
                    er_weak_min=e.get("er_weak_min", self.trade_cfg.er_weak_min),
                    er_min=e.get("er_min", self.trade_cfg.er_min),
                    er_trend=e.get("er_trend", self.trade_cfg.er_trend),
                    quick_enabled=e.get("quick_enabled", self.trade_cfg.quick_enabled),
                    allow_grades=e.get("allow_grades", list(self.trade_cfg.allow_grades)),
                    min_score=e.get("min_score", self.trade_cfg.min_score),
                    cooldown_sec=e.get("cooldown_sec", self.trade_cfg.cooldown_sec),
                    # 组合过滤器（新字段，旧配置没有时用 dataclass 默认值）
                    atr_filter_enabled=e.get("atr_filter_enabled", False),
                    atr_vol_min=e.get("atr_vol_min", 0.7),
                    range_filter_enabled=e.get("range_filter_enabled", False),
                    range_size_max=e.get("range_size_max", 0.15),
                    range_touches_min=e.get("range_touches_min", 3),
                    mtf_filter_enabled=e.get("mtf_filter_enabled", False),
                    mtf_consistency_min=e.get("mtf_consistency_min", 0.6),
                    mtf_flip_max=e.get("mtf_flip_max", 5),
                    adx_filter_enabled=e.get("adx_filter_enabled", False),
                    adx_min=e.get("adx_min", 20.0),
                    adx_period=e.get("adx_period", 14),
                )
                # 覆盖其他字段
                if "enabled" in e:
                    cfg.enabled = e["enabled"]

                params = Params()
                for k, v in (e.get("params") or {}).items():
                    if hasattr(params, k):
                        setattr(params, k, v)

                # 品种独立止盈止损规则（旧配置没有则用全局默认）
                exit_rules = ExitRules()
                for k, v in (e.get("exit_rules") or vars(self.exit_rules)).items():
                    if hasattr(exit_rules, k):
                        setattr(exit_rules, k, v)
                exit_rules_quick = ExitRules(
                    tp1_pct=0.8, tp1_ratio=100.0, move_sl_to_entry=False,
                    sl_mode="pct", sl_pct=1.0, trail_with_st=False,
                )
                for k, v in (e.get("exit_rules_quick") or vars(self.exit_rules_quick)).items():
                    if hasattr(exit_rules_quick, k):
                        setattr(exit_rules_quick, k, v)

                self.stores[symbol] = SymbolStore(
                    symbol, cfg=cfg, params=params,
                    exit_rules=exit_rules, exit_rules_quick=exit_rules_quick
                )
        else:
            # 旧版单品种配置文件：从全局字段合成一条品种配置（enabled 保持 False）
            from position import ExitRules
            symbol = self.current_symbol
            cfg = SymbolTradeConfig(
                symbol=symbol,
                margin_usdt=self.trade_cfg.amount_usdt,
                leverage=self.trade_cfg.leverage,
                allow_tfs=list(self.trade_cfg.allow_tfs),
                # 旧版的 ER 阈值在 trade_cfg 里，迁移到品种级
                er_hide_below=self.trade_cfg.er_hide_below,
                er_weak_min=self.trade_cfg.er_weak_min,
                er_min=self.trade_cfg.er_min,
                er_trend=self.trade_cfg.er_trend,
                quick_enabled=self.trade_cfg.quick_enabled,
                allow_grades=list(self.trade_cfg.allow_grades),
                min_score=self.trade_cfg.min_score,
                cooldown_sec=self.trade_cfg.cooldown_sec,
            )
            params = Params()
            for k, v in (data.get("params") or {}).items():
                if hasattr(params, k):
                    setattr(params, k, v)
            # 旧版止盈止损在全局 exit_rules，迁移到品种级
            self.stores[symbol] = SymbolStore(
                symbol, cfg=cfg, params=params,
                exit_rules=self.exit_rules, exit_rules_quick=self.exit_rules_quick
            )
            logger.info(f"旧版配置已迁移为品种条目: {symbol}")

        logger.info(f"已从 {SETTINGS_FILE} 恢复配置，交易品种: "
                    f"{list(self.stores)}（总开关重置为关，需手动开启）")


state = AppState()
