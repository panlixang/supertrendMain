"""
全局共享状态

单品种可切换架构：内存里只保留 current_symbol 的各周期 K 线 deque，
切换品种时清空重新加载（见 feed.switch_symbol）。

周期集合与 Pine 脚本 «Signal Engine Quantum Edge» 的 MTF Bias 表对齐：
5m / 15m / 30m / 1H / 4H / 1D / 1W / 1M，另加 1m 供短线看盘。
"""

from collections import deque
from dataclasses import dataclass
import json
import logging
import os

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# 面板参数的持久化文件：改动即写、启动即读，重启不再丢配置
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")


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
    """对应 Pine 的 input 区块，前端可调，改动后全部重算。"""
    # ▸ Supertrend Settings
    periods:    int   = 15        # ATR Period
    multiplier: float = 9.1       # ATR Multiplier
    src:        str   = "hl2"     # Source
    change_atr: bool  = True      # Change ATR Calculation Method (True=ta.atr/RMA)
    # ▸ MTF Bias Table
    fast_len:   int   = 20        # Fast MA Length
    slow_len:   int   = 50        # Slow MA Length
    ma_type:    str   = "EMA"     # EMA / SMA


class AppState:
    def __init__(self):
        self.ticker = Ticker()
        self.candles: dict[str, deque] = {
            tf: deque(maxlen=cfg["maxlen"]) for tf, cfg in TF_CONFIG.items()
        }
        self.clients: set[WebSocket] = set()
        self.current_symbol: str = "BTC-USDT"
        self.params = Params()
        # 最近的买卖信号（跨周期，新的在后，最多 300 条）
        self.signals: list[dict] = []
        # 自动挂单配置 + 已下单记录（运行时内存，重启清空）
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
        # 当前持仓（同一时刻最多一笔）+ 已平仓历史
        self.position = None
        self.closed: list[dict] = []
        # {tf: 上次下单时间戳秒}，用于冷却
        self.last_order_at: dict[str, float] = {}
        self.feed = None      # 运行时由 main 注入
        self.executor = None
        self._load_settings()

    def save_settings(self):
        """把面板可调的配置落盘。任何一处改完调用即可，失败只警告不中断。"""
        try:
            data = {
                "params": vars(self.params),
                "trade_cfg": vars(self.trade_cfg),
                "exit_rules": vars(self.exit_rules),
                "exit_rules_quick": vars(self.exit_rules_quick),
                "current_symbol": self.current_symbol,
            }
            tmp = SETTINGS_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, SETTINGS_FILE)   # 原子替换，避免写一半断电留下残破文件
        except Exception as e:
            logger.warning(f"配置保存失败: {e}")

    def _load_settings(self):
        """启动时恢复上次的面板配置。只认识的字段才写回，代码升级加减字段不受影响。

        enabled（自动挂单总开关）故意不恢复 —— 重启后必须人工确认再开，
        避免服务器重启后在没人盯的情况下自动继续下真金白银的单。
        """
        if not os.path.exists(SETTINGS_FILE):
            return
        try:
            with open(SETTINGS_FILE) as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(f"配置读取失败（忽略，用默认值）: {e}")
            return
        for key, obj in (("params", self.params), ("trade_cfg", self.trade_cfg),
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
        logger.info(f"已从 {SETTINGS_FILE} 恢复面板配置"
                    f"（自动挂单开关重置为关，需手动开启）")

    async def broadcast(self, msg: dict):
        dead = set()
        for ws in self.clients:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.add(ws)
        self.clients -= dead

    def rules_for(self, profile: str | None):
        """取该档的出场规则。未知档位一律退回标准档 —— 宁可用保守的老规则，
        也不要因为拼错一个字符串就跑出一套没人预期的止盈止损。"""
        return self.exit_rules_quick if profile == "quick" else self.exit_rules

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

    def add_order(self, order: dict):
        self.orders.append(order)
        self.orders = self.orders[-200:]


state = AppState()
