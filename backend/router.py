"""HTTP / WebSocket 路由"""

import asyncio
import logging
import time
from dataclasses import replace
from functools import partial
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

import backtest as bt
import instruments
import notify
import regime
import strategy
import trade
from history import fetch_candles, load_history
from indicators import compute
from state import BIAS_TFS, MAX_SYMBOLS, TF_CONFIG, TFS, state

router = APIRouter()
logger = logging.getLogger(__name__)


# ─── 基础数据 ────────────────────────────────────────────────────

@router.get("/api/ticker")
async def get_ticker():
    return vars(state.ticker)


@router.get("/api/tfs")
async def get_tfs():
    return [{"tf": tf, "label": cfg["label"]} for tf, cfg in TF_CONFIG.items()]


@router.get("/api/candles")
async def get_candles(tf: str = "15m"):
    return state.candles_by_tf(tf)


@router.get("/api/indicators")
async def get_indicators(tf: str = "15m"):
    """单周期 SuperTrend 全量：轨道 / 趋势 / 信号 / 状态。"""
    candles = state.candles_by_tf(tf)
    if not candles:
        return {}
    result = compute(candles, tf, vars(state.params))
    if result and result.get("signals"):
        # 把 state.signals 里的 hidden 字段注入到 compute 的信号里
        # state.signals 由 rescan_signals() 维护，含当前 ER 阈值评估结果
        hidden_set = {
            (s["tf"], s["ts"])
            for s in state.signals
            if s.get("hidden")
        }
        for sig in result["signals"]:
            sig["hidden"] = (sig.get("tf", tf), sig["ts"]) in hidden_set
    return result


@router.get("/api/overview")
async def get_overview():
    """MTF Bias 表（对应 Pine 右上角表格）+ 各周期 SuperTrend 方向。"""
    return strategy.overview(state.all_candles(), vars(state.params))


@router.get("/api/signals")
async def get_signals(tf: Optional[str] = None, limit: int = 100):
    sigs = state.signals
    if tf:
        sigs = [s for s in sigs if s["tf"] == tf]
    return sigs[-limit:]


# ─── 参数（对应 Pine 的 input 面板） ─────────────────────────────

class ParamsIn(BaseModel):
    periods:    Optional[int]   = None
    multiplier: Optional[float] = None
    src:        Optional[str]   = None
    change_atr: Optional[bool]  = None
    fast_len:   Optional[int]   = None
    slow_len:   Optional[int]   = None
    ma_type:    Optional[str]   = None


@router.get("/api/params")
async def get_params():
    return vars(state.params)


@router.post("/api/params")
async def set_params(body: ParamsIn):
    p = state.params
    if body.periods    is not None: p.periods    = max(1, body.periods)
    if body.multiplier is not None: p.multiplier = max(0.1, body.multiplier)
    if body.src        is not None: p.src        = body.src
    if body.change_atr is not None: p.change_atr = body.change_atr
    if body.fast_len   is not None: p.fast_len   = max(1, body.fast_len)
    if body.slow_len   is not None: p.slow_len   = max(1, body.slow_len)
    if body.ma_type    is not None: p.ma_type    = body.ma_type.upper()

    if state.feed:
        await state.feed.apply_params()
    state.save_settings()
    return {"ok": True, "params": vars(p)}


@router.post("/api/params/reset")
async def reset_params():
    """恢复 Pine 脚本的默认值：15 / 9.1 / hl2 / changeATR / EMA 20-50。"""
    from state import Params
    state.params = Params()
    if state.feed:
        await state.feed.apply_params()
    state.save_settings()
    return {"ok": True, "params": vars(state.params)}


# ─── 品种 ────────────────────────────────────────────────────────

@router.get("/api/symbol")
async def get_symbol():
    return {"symbol": state.current_symbol}


class SymbolIn(BaseModel):
    instId: str


@router.post("/api/symbol")
async def set_symbol(body: SymbolIn):
    inst = body.instId.strip().upper()
    # 多品种架构下切图安全：每个持仓由自己品种的 ticker 驱动止盈止损，
    # 图表看哪个品种与交易互不影响，无需再禁止切换。
    if not await instruments.is_valid(inst):
        return {"ok": False, "error": f"未知品种: {inst}"}
    if state.feed is None:
        return {"ok": False, "error": "feed 未初始化"}
    r = await state.feed.switch_symbol(inst)
    if r.get("ok"):
        state.save_settings()
    return r


@router.get("/api/instruments")
async def list_instruments(q: str = "", limit: int = 40):
    return await instruments.search(q, limit=limit)


# ─── 回测 ────────────────────────────────────────────────────────

class BacktestIn(BaseModel):
    tf:          str   = "1h"
    bars:        int   = 800
    init_cash:   float = 10000.0
    fee_rate:    float = 0.0005
    allow_short: bool  = True
    bias_filter: bool  = False
    # 不传则用当前全局参数
    periods:     Optional[int]   = None
    multiplier:  Optional[float] = None
    # ── 闸门 / 止盈止损 / 仓位规模（默认全关，等价于原来的「翻转即反手」）──
    er_min:          Optional[float] = None   # None = 不启用震荡闸门
    use_exit_rules:  bool  = False            # 用实盘那套止盈止损跑
    sizing:          str   = "equity"         # equity=满仓复利1x / fixed=固定保证金×杠杆
    margin_usdt:     float = 10.0
    leverage:        int   = 1
    # 弱档：er_min 和 use_exit_rules 都开时才有意义
    er_weak_min:     Optional[float] = None   # None = 不启用弱档
    quick_enabled:   bool  = False
    # 只在 use_exit_rules=True 时生效；不传的字段沿用面板上的实盘设置
    tp1_pct:          Optional[float] = None
    tp1_ratio:        Optional[float] = None
    move_sl_to_entry: Optional[bool]  = None
    sl_mode:          Optional[str]   = None
    sl_pct:           Optional[float] = None
    trail_with_st:    Optional[bool]  = None


def _params_with(body) -> dict:
    p = dict(vars(state.params))
    if getattr(body, "periods", None) is not None:
        p["periods"] = body.periods
    if getattr(body, "multiplier", None) is not None:
        p["multiplier"] = body.multiplier
    return p


def _exit_rules_of(body):
    """以面板上的实盘规则为基准，用 body 里显式传的字段覆盖。

    这样回测默认跑的就是「你实盘现在这套」，想试别的再单独传。
    """
    if not getattr(body, "use_exit_rules", False):
        return None
    r = replace(state.exit_rules, enabled=True)
    for f in ("tp1_pct", "tp1_ratio", "move_sl_to_entry", "sl_mode", "sl_pct", "trail_with_st"):
        v = getattr(body, f, None)
        if v is not None:
            r = replace(r, **{f: v})
    return r


def _engine_kw(body) -> dict:
    """回测引擎的公共入参，backtest / sweep / sweep-er 三个端点共用。"""
    quick_on = getattr(body, "quick_enabled", False)
    return {
        "exit_rules":  _exit_rules_of(body),
        "sizing":      "fixed" if getattr(body, "sizing", "equity") == "fixed" else "equity",
        "margin_usdt": max(0.1, getattr(body, "margin_usdt", 10.0)),
        "leverage":    max(1, min(125, getattr(body, "leverage", 1))),
        "er_weak_min": getattr(body, "er_weak_min", None) if quick_on else None,
        # 弱档规则始终用面板上的那套（state.exit_rules_quick）
        "exit_rules_quick": state.exit_rules_quick if quick_on else None,
    }


async def _candles_for(tf: str, bars: int) -> list[dict]:
    """优先用内存 deque；要的根数超出缓存时现拉 REST。"""
    if tf not in TF_CONFIG:
        return []
    cached = state.candles_by_tf(tf)
    if len(cached) >= bars:
        return cached[-bars:]
    loop = asyncio.get_event_loop()
    fetched = await loop.run_in_executor(
        None, fetch_candles, tf, bars, state.current_symbol
    )
    return [vars(c) for c in fetched] or cached


@router.post("/api/backtest")
async def backtest(body: BacktestIn):
    candles = await _candles_for(body.tf, body.bars)
    if not candles:
        return {"error": f"无 {body.tf} K线数据"}
    # 回测是纯 CPU 计算，8000 根要几十秒 —— 必须丢进线程，
    # 否则会把事件循环整个卡住，期间实盘的止盈止损检查也是停的
    loop = asyncio.get_event_loop()
    r = await loop.run_in_executor(None, partial(
        bt.run_backtest,
        candles, _params_with(body),
        init_cash=body.init_cash, fee_rate=body.fee_rate,
        allow_short=body.allow_short, bias_filter=body.bias_filter,
        er_min=body.er_min, **_engine_kw(body),
    ))
    rules = _exit_rules_of(body)
    r.update({"tf": body.tf, "symbol": state.current_symbol,
              "params": _params_with(body), "bias_filter": body.bias_filter,
              "allow_short": body.allow_short, "er_min": body.er_min,
              "sizing": body.sizing, "leverage": body.leverage,
              "exit_rules": vars(rules) if rules else None})
    return r


class SweepIn(BaseModel):
    tf:          str   = "1h"
    bars:        int   = 800
    fee_rate:    float = 0.0005
    allow_short: bool  = True
    bias_filter: bool  = False
    period_min:  int   = 7
    period_max:  int   = 21
    period_step: int   = 2
    mult_min:    float = 1.0
    mult_max:    float = 10.0
    mult_step:   float = 1.0
    er_min:          Optional[float] = None
    use_exit_rules:  bool  = False
    sizing:          str   = "equity"
    margin_usdt:     float = 10.0
    leverage:        int   = 1


@router.post("/api/sweep")
async def sweep(body: SweepIn):
    """参数寻优网格。脚本默认 15/9.1 换品种基本要重调，用这个找。"""
    candles = await _candles_for(body.tf, body.bars)
    if not candles:
        return {"error": f"无 {body.tf} K线数据"}

    periods = list(range(body.period_min, body.period_max + 1, max(1, body.period_step)))
    mults, m = [], body.mult_min
    while m <= body.mult_max + 1e-9:
        mults.append(round(m, 2))
        m += max(0.1, body.mult_step)
    if len(periods) * len(mults) > 400:
        return {"error": "网格过大（上限 400 组），请调大步长或收窄范围"}

    rows = await asyncio.get_event_loop().run_in_executor(None, partial(
        bt.sweep,
        candles, vars(state.params), periods, mults,
        fee_rate=body.fee_rate, allow_short=body.allow_short,
        bias_filter=body.bias_filter, er_min=body.er_min, **_engine_kw(body)))
    return {"tf": body.tf, "bars": len(candles), "symbol": state.current_symbol,
            "count": len(rows), "rows": rows[:60]}


class ErSweepIn(BaseModel):
    """扫 er_min 闸门阈值 —— 回答「震荡闸门该卡在哪」这个问题。"""
    tf:          str   = "15m"
    bars:        int   = 3000
    fee_rate:    float = 0.0005
    allow_short: bool  = True
    bias_filter: bool  = False
    er_list:     list  = [0.05, 0.10, 0.12, 0.15, 0.20, 0.25, 0.30]
    use_exit_rules:  bool  = False
    sizing:          str   = "equity"
    margin_usdt:     float = 10.0
    leverage:        int   = 1
    er_weak_min:     Optional[float] = None
    quick_enabled:   bool  = False
    periods:     Optional[int]   = None
    multiplier:  Optional[float] = None


@router.post("/api/sweep-er")
async def sweep_er(body: ErSweepIn):
    """ER 闸门阈值对比。第一行是「无闸门」基准，其余按阈值升序 ——

    要看的是「阈值升高时收益/回撤/交易数怎么走」的趋势，所以不按收益排序。
    单品种样本通常只有几十笔，别拿最高那一行当结论。
    """
    candles = await _candles_for(body.tf, body.bars)
    if not candles:
        return {"error": f"无 {body.tf} K线数据"}

    try:
        ers = sorted({round(float(e), 4) for e in body.er_list if 0 <= float(e) <= 1})
    except (TypeError, ValueError):
        return {"error": "er_list 需为 0~1 之间的数字列表"}
    if not ers:
        return {"error": "er_list 为空"}
    if len(ers) > 20:
        return {"error": "最多 20 档（当前 %d 档）" % len(ers)}

    rows = await asyncio.get_event_loop().run_in_executor(None, partial(
        bt.sweep_er,
        candles, _params_with(body), ers,
        fee_rate=body.fee_rate, allow_short=body.allow_short,
        bias_filter=body.bias_filter, **_engine_kw(body)))
    return {"tf": body.tf, "bars": len(candles), "symbol": state.current_symbol,
            "live_er_min": state.trade_cfg.er_min, "rows": rows}


# ─── 推送 ────────────────────────────────────────────────────────

@router.get("/api/notify/status")
async def notify_status():
    return {"enabled": notify.enabled, "min_grade": notify.MIN_GRADE}


@router.post("/api/notify/test")
async def notify_test():
    if not notify.enabled:
        return {"ok": False, "error": "未启用推送，请设置环境变量 SERVERCHAN_SENDKEY"}
    ok = await notify.push(
        "超级趋势监控台 测试推送",
        "## ✅ 推送配置成功\n买卖信号将推送到此。",
    )
    return {"ok": ok}


# ─── 自动挂单（OKX） ────────────────────────────────────────────

@router.get("/api/trade/config")
async def trade_config():
    cfg = state.trade_cfg
    return {
        **vars(cfg),
        "configured":  trade.configured,        # 是否已配 API 密钥
        "env_paper":   trade.SIMULATED,         # 环境变量里的默认环境
        "exchange":    "OKX",
    }


class TradeCfgIn(BaseModel):
    enabled:      Optional[bool]  = None
    paper:        Optional[bool]  = None
    category:     Optional[str]   = None
    leverage:     Optional[int]   = None
    margin_mode:  Optional[str]   = None
    amount_usdt:  Optional[float] = None
    price_offset: Optional[float] = None
    er_hide_below: Optional[float] = None
    er_min:       Optional[float] = None
    er_trend:     Optional[float] = None
    er_weak_min:  Optional[float] = None
    quick_enabled: Optional[bool]  = None
    allow_grades: Optional[list]  = None
    min_score:    Optional[int]   = None
    allow_tfs:    Optional[list]  = None
    cooldown_sec: Optional[int]   = None


@router.post("/api/trade/config")
async def set_trade_config(body: TradeCfgIn):
    cfg = state.trade_cfg
    if body.enabled is not None:
        if body.enabled and not trade.configured:
            return {"ok": False, "error": "未配置 OKX API 密钥，无法开启自动挂单"}
        cfg.enabled = body.enabled
    if body.paper        is not None: cfg.paper        = body.paper
    if body.category     is not None:
        if body.category not in ("SWAP", "SPOT"):
            return {"ok": False, "error": "category 仅支持 SWAP（合约）/ SPOT（现货）"}
        cfg.category = body.category
    if body.leverage     is not None: cfg.leverage     = max(1, min(125, body.leverage))
    if body.margin_mode  is not None:
        if body.margin_mode not in ("cross", "isolated"):
            return {"ok": False, "error": "margin_mode 仅支持 cross（全仓）/ isolated（逐仓）"}
        cfg.margin_mode = body.margin_mode
    # 两个 ER 下界互相约束，必须先算出「改完会是什么」再校验 ——
    # 边改边校验的话，返回错误时脏值已经写进 cfg 了
    new_er_min = max(0.0, min(1.0, body.er_min)) if body.er_min is not None else cfg.er_min
    new_weak = (max(0.0, min(1.0, body.er_weak_min))
                if body.er_weak_min is not None else cfg.er_weak_min)
    if new_weak > new_er_min:
        return {"ok": False,
                "error": f"弱档下界 {new_weak} 不能大于标准档下界 {new_er_min} —— "
                         f"否则弱档区间为空，开了也永远不触发"}
    cfg.er_min, cfg.er_weak_min = new_er_min, new_weak

    if body.amount_usdt  is not None: cfg.amount_usdt  = max(1.0, body.amount_usdt)
    if body.price_offset is not None: cfg.price_offset = max(0.0, min(5.0, body.price_offset))
    if body.er_hide_below is not None: cfg.er_hide_below = max(0.0, min(1.0, body.er_hide_below))
    if body.er_trend     is not None: cfg.er_trend     = max(0.0, min(1.0, body.er_trend))
    if body.allow_grades is not None: cfg.allow_grades = [g.upper() for g in body.allow_grades if g]
    if body.min_score    is not None: cfg.min_score    = max(0, min(3, body.min_score))
    if body.allow_tfs    is not None: cfg.allow_tfs    = [t for t in body.allow_tfs if t in TF_CONFIG]
    if body.cooldown_sec is not None: cfg.cooldown_sec = max(0, body.cooldown_sec)
    if body.quick_enabled is not None: cfg.quick_enabled = body.quick_enabled

    # 注意：leverage / amount_usdt / allow_tfs 已下放到品种级（/api/trade/symbols），
    # 这里改的只是「新品种默认值」，不影响已添加品种。

    if cfg.enabled and not cfg.paper:
        logger.warning("⚠️ 自动挂单已开启且指向【实盘】，将使用真实资金下单")
        if cfg.quick_enabled:
            logger.warning(f"⚠️ 弱档自动下单已开启：ER {cfg.er_weak_min}~{cfg.er_min} "
                           f"的信号也会用真实资金下单（快进快出规则）")

    await state.broadcast({"type": "trade_config", "data": vars(cfg)})
    state.save_settings()

    # ER 参数改变后重扫历史信号，更新 hidden 字段
    state.feed.rescan_signals()

    return {"ok": True, "config": vars(cfg)}


# ─── 止盈止损规则 ────────────────────────────────────────────────

class ExitRulesIn(BaseModel):
    symbol:           Optional[str]  = None   # 品种独立时指定品种，None=改全局默认
    profile:          str = "normal"    # normal 标准档 / quick 快进快出档
    enabled:          Optional[bool]  = None
    tp1_pct:          Optional[float] = None
    tp1_ratio:        Optional[float] = None
    move_sl_to_entry: Optional[bool]  = None
    sl_mode:          Optional[str]   = None
    sl_pct:           Optional[float] = None
    trail_with_st:    Optional[bool]  = None


def _all_rules(symbol: Optional[str] = None) -> dict:
    """symbol 指定则返回该品种的规则，否则返回全局默认规则。"""
    if symbol:
        st = state.stores.get(symbol)
        if st:
            return {"normal": vars(st.exit_rules), "quick": vars(st.exit_rules_quick)}
    return {"normal": vars(state.exit_rules), "quick": vars(state.exit_rules_quick)}


@router.get("/api/trade/exit-rules")
async def get_exit_rules(symbol: Optional[str] = None):
    return _all_rules(symbol)


@router.post("/api/trade/exit-rules")
async def set_exit_rules(body: ExitRulesIn):
    if body.profile not in ("normal", "quick"):
        return {"ok": False, "error": "profile 仅支持 normal（标准档）/ quick（快进快出档）"}

    # 品种独立止盈止损
    if body.symbol:
        st = state.stores.get(body.symbol)
        if not st:
            return {"ok": False, "error": f"品种 {body.symbol} 不存在"}
        r = st.exit_rules_quick if body.profile == "quick" else st.exit_rules
    else:
        # 全局默认规则
        r = state.rules_for(body.profile)

    if body.enabled          is not None: r.enabled = body.enabled
    if body.tp1_pct          is not None: r.tp1_pct = max(0.1, min(100.0, body.tp1_pct))
    if body.tp1_ratio        is not None: r.tp1_ratio = max(1.0, min(100.0, body.tp1_ratio))
    if body.move_sl_to_entry is not None: r.move_sl_to_entry = body.move_sl_to_entry
    if body.sl_mode          is not None:
        if body.sl_mode not in ("st", "pct"):
            return {"ok": False, "error": "sl_mode 仅支持 st（超趋线）/ pct（百分比）"}
        r.sl_mode = body.sl_mode
    if body.sl_pct           is not None: r.sl_pct = max(0.1, min(50.0, body.sl_pct))
    if body.trail_with_st    is not None: r.trail_with_st = body.trail_with_st

    await state.broadcast({"type": "exit_rules", "data": _all_rules(body.symbol)})
    state.save_settings()
    return {"ok": True, "rules": _all_rules(body.symbol)}


# ─── 交易品种管理（多品种） ──────────────────────────────────────

def _symbol_row(st) -> dict:
    return {
        **vars(st.cfg),
        "params": vars(st.params),
        "exit_rules": vars(st.exit_rules),
        "exit_rules_quick": vars(st.exit_rules_quick),
        "last": st.ticker.last,
        "history_loaded": st.history_loaded,
        "position": st.position.to_dict(st.ticker.last) if st.position else None,
    }


def _symbols_payload() -> list:
    return [_symbol_row(st) for st in state.stores.values() if st.cfg]


@router.get("/api/trade/symbols")
async def list_trade_symbols():
    return {"symbols": _symbols_payload(), "max": MAX_SYMBOLS}


class SymbolCfgIn(BaseModel):
    symbol:      str
    enabled:     Optional[bool]  = None
    margin_usdt: Optional[float] = None
    leverage:    Optional[int]   = None
    allow_tfs:   Optional[list]  = None
    # ER 闸门品种独立
    er_hide_below: Optional[float] = None
    er_weak_min:   Optional[float] = None
    er_min:        Optional[float] = None
    er_trend:      Optional[float] = None
    quick_enabled: Optional[bool]  = None
    allow_grades:  Optional[list]  = None
    min_score:     Optional[int]   = None
    cooldown_sec:  Optional[int]   = None
    # 指标参数
    periods:     Optional[int]   = None
    multiplier:  Optional[float] = None
    # 组合过滤器
    atr_filter_enabled:    Optional[bool]  = None
    atr_vol_min:           Optional[float] = None
    range_filter_enabled:  Optional[bool]  = None
    range_size_max:        Optional[float] = None
    range_touches_min:     Optional[int]   = None
    mtf_filter_enabled:    Optional[bool]  = None
    mtf_consistency_min:   Optional[float] = None
    mtf_flip_max:          Optional[int]   = None


async def _load_symbol_history(sym: str):
    """新品种历史在后台拉，不阻塞请求。完成后重扫信号并刷新前端。"""
    st = state.stores.get(sym)
    if st is None:
        return
    try:
        await load_history(st)
        if state.feed:
            state.feed.rescan_signals(st)
            await state.feed.push_snapshot()
        logger.info(f"[{sym}] 历史加载完成，进入实时监控")
    except Exception as e:
        logger.warning(f"[{sym}] 历史加载失败: {e}")


@router.post("/api/trade/symbols")
async def upsert_trade_symbol(body: SymbolCfgIn):
    """新增或修改一个交易品种的下单参数。新品种默认 enabled=False。"""
    sym = body.symbol.strip().upper()
    st = state.stores.get(sym)

    if st is None:                                   # ── 新增 ──
        if len(state.stores) >= MAX_SYMBOLS:
            return {"ok": False, "error": f"最多同时交易 {MAX_SYMBOLS} 个品种"}
        if not await instruments.is_valid(sym):
            return {"ok": False, "error": f"未知品种: {sym}"}
        from state import SymbolStore, SymbolTradeConfig
        cfg = SymbolTradeConfig(
            symbol=sym, margin_usdt=state.trade_cfg.amount_usdt,
            leverage=state.trade_cfg.leverage,
            allow_tfs=list(state.trade_cfg.allow_tfs),
        )
        vs = state.view_store
        if vs and vs.symbol == sym:                  # 正在看图的品种升级为交易品种
            st = vs
            st.cfg = cfg
            state.view_store = None
        else:
            st = SymbolStore(sym, cfg=cfg)
        state.stores[sym] = st
        from executor import Executor
        state.executors[sym] = Executor(state, st)
        if state.feed:
            await state.feed.add_symbol(sym)
        if not st.history_loaded:
            asyncio.get_event_loop().create_task(_load_symbol_history(sym))

    c = st.cfg
    if body.enabled is not None:
        if body.enabled and not trade.configured:
            return {"ok": False, "error": "未配置 OKX API 密钥，无法开启该品种"}
        c.enabled = body.enabled
    if body.margin_usdt is not None: c.margin_usdt = max(1.0, body.margin_usdt)
    if body.leverage    is not None: c.leverage    = max(1, min(125, body.leverage))
    if body.allow_tfs   is not None: c.allow_tfs   = [t for t in body.allow_tfs if t in TF_CONFIG]

    # ER 闸门品种独立
    if body.er_hide_below is not None: c.er_hide_below = max(0.0, min(1.0, body.er_hide_below))
    if body.er_min        is not None: c.er_min        = max(0.0, min(1.0, body.er_min))
    if body.er_weak_min   is not None: c.er_weak_min   = max(0.0, min(1.0, body.er_weak_min))
    if body.er_trend      is not None: c.er_trend      = max(0.0, min(1.0, body.er_trend))
    if body.quick_enabled is not None: c.quick_enabled = body.quick_enabled
    if body.allow_grades  is not None: c.allow_grades  = [g.upper() for g in body.allow_grades if g]
    if body.min_score     is not None: c.min_score     = max(0, min(3, body.min_score))
    if body.cooldown_sec  is not None: c.cooldown_sec  = max(0, body.cooldown_sec)

    # 组合过滤器品种独立
    if body.atr_filter_enabled   is not None: c.atr_filter_enabled   = body.atr_filter_enabled
    if body.atr_vol_min          is not None: c.atr_vol_min          = max(0.0, min(2.0, body.atr_vol_min))
    if body.range_filter_enabled is not None: c.range_filter_enabled = body.range_filter_enabled
    if body.range_size_max       is not None: c.range_size_max       = max(0.01, min(1.0, body.range_size_max))
    if body.range_touches_min    is not None: c.range_touches_min    = max(1, body.range_touches_min)
    if body.mtf_filter_enabled   is not None: c.mtf_filter_enabled   = body.mtf_filter_enabled
    if body.mtf_consistency_min  is not None: c.mtf_consistency_min  = max(0.0, min(1.0, body.mtf_consistency_min))
    if body.mtf_flip_max         is not None: c.mtf_flip_max         = max(1, body.mtf_flip_max)

    # 指标参数
    if body.periods     is not None: st.params.periods    = max(1, body.periods)
    if body.multiplier  is not None: st.params.multiplier = max(0.1, body.multiplier)

    # ER 参数或过滤器改变后重扫该品种历史信号
    if any(x is not None for x in [body.er_hide_below, body.er_min, body.er_weak_min,
                                     body.er_trend, body.allow_grades, body.min_score,
                                     body.atr_filter_enabled, body.range_filter_enabled,
                                     body.mtf_filter_enabled]):
        if state.feed:
            state.feed.rescan_signals(st)

    await state.broadcast({"type": "symbols", "data": _symbols_payload()})
    state.save_settings()
    return {"ok": True, "symbols": _symbols_payload()}


@router.delete("/api/trade/symbols/{symbol}")
async def delete_trade_symbol(symbol: str):
    sym = symbol.strip().upper()
    st = state.stores.get(sym)
    if st is None:
        return {"ok": False, "error": f"{sym} 不在交易列表"}
    if st.position and st.position.qty > 0:
        return {"ok": False, "error": f"{sym} 有未平仓位，先平仓再移除"}
    if len(state.stores) <= 1:
        return {"ok": False, "error": "至少保留一个品种"}

    state.stores.pop(sym, None)
    state.executors.pop(sym, None)
    if sym == state.current_symbol:
        st.cfg = None                      # 还在看图 → 降级为仅看图 store
        state.view_store = st
    elif state.feed:
        await state.feed.remove_symbol(sym)

    await state.broadcast({"type": "symbols", "data": _symbols_payload()})
    state.save_settings()
    return {"ok": True, "symbols": _symbols_payload()}


# ─── 持仓 ────────────────────────────────────────────────────────

@router.get("/api/trade/position")
async def get_position():
    """旧接口：图表品种的持仓（兼容保留）。全品种用 /api/trade/positions。"""
    pos = state.position
    return {
        "position": pos.to_dict(state.ticker.last) if pos else None,
        "closed":   state.closed[-20:],
    }


@router.get("/api/trade/positions")
async def get_positions_all():
    """全品种持仓 + 合并的已平仓历史（带 sym 字段，按时间升序）。"""
    closed = [row for st in state.stores.values() for row in st.closed]
    closed.sort(key=lambda r: r.get("entry_ts") or 0)
    return {
        "positions": {sym: st.position.to_dict(st.ticker.last)
                      for sym, st in state.stores.items() if st.position},
        "closed": closed[-30:],
    }


class CloseIn(BaseModel):
    symbol: Optional[str] = None    # 不传 = 图表当前品种


@router.post("/api/trade/close")
async def close_position(body: CloseIn = None):
    """手动平掉指定品种的全部持仓。"""
    sym = (body.symbol.strip().upper() if body and body.symbol
           else state.current_symbol)
    ex = state.executors.get(sym)
    if not ex:
        return {"ok": False, "error": f"{sym} 不在交易列表或执行器未初始化"}
    return await ex.close_manual("手动平仓")


@router.get("/api/trade/exchange-position")
async def exchange_position(symbol: Optional[str] = None):
    """查交易所实际持仓，用于和本地状态机对账。"""
    return await trade.get_positions(symbol or state.current_symbol,
                                     state.trade_cfg.category,
                                     sim=state.trade_cfg.paper)


class LeverageIn(BaseModel):
    leverage: int
    symbol:   Optional[str] = None


@router.post("/api/trade/leverage")
async def set_lev(body: LeverageIn):
    sym = (body.symbol or state.current_symbol).strip().upper()
    lev = max(1, min(125, body.leverage))
    r = await trade.set_leverage(sym, lev,
                                 state.trade_cfg.margin_mode, sim=state.trade_cfg.paper)
    if r.get("ok"):
        st = state.stores.get(sym)
        if st and st.cfg:
            st.cfg.leverage = lev
            await state.broadcast({"type": "symbols", "data": _symbols_payload()})
        else:
            state.trade_cfg.leverage = lev
            await state.broadcast({"type": "trade_config", "data": vars(state.trade_cfg)})
        state.save_settings()
    return r


@router.get("/api/trade/orders")
async def trade_orders(limit: int = 50):
    return state.orders[-limit:]


@router.get("/api/trade/ping")
async def trade_ping():
    """密钥自检：查一次账户资产，顺便确认走的是模拟盘还是实盘。"""
    return await trade.ping()


@router.get("/api/regime")
async def get_regime(tf: str = "15m"):
    """当前周期的行情状态（震荡 / 震荡边缘 / 弱趋势 / 趋势），闸门就是按这个判的。"""
    candles = state.candles_by_tf(tf)
    cfg = state.trade_cfg
    er = regime.efficiency_ratio(candles)
    return {"tf": tf,
            **regime.classify(er, cfg.er_min, cfg.er_trend,
                              cfg.er_weak_min, cfg.quick_enabled),
            "er_min": cfg.er_min, "er_trend": cfg.er_trend,
            "er_weak_min": cfg.er_weak_min, "quick_enabled": cfg.quick_enabled,
            "window": regime.ER_WINDOW}


class TestOrderIn(BaseModel):
    side:   str   = "buy"
    amount: Optional[float] = None
    paper:  Optional[bool]  = None


@router.post("/api/trade/test-order")
async def test_order(body: TestOrderIn):
    """手动挂一笔测试单，验证密钥、杠杆和下单链路是否通。

    挂在盘口 ±3%（合约 LimitPriceRatio 上限 5%），正常不成交，可撤单。
    这笔单不会进入持仓状态机 —— 只验证链路，不参与止盈止损。
    """
    if not trade.configured:
        return {"ok": False, "error": "未配置 OKX API 密钥"}
    last = state.ticker.last
    if not last:
        return {"ok": False, "error": "暂无最新价"}

    cfg = state.trade_cfg
    paper = cfg.paper if body.paper is None else body.paper
    if cfg.category != "SPOT":
        await trade.set_leverage(state.current_symbol, cfg.leverage,
                                 cfg.margin_mode, sim=paper)

    px = last * (0.97 if body.side == "buy" else 1.03)
    r = await trade.place_order(
        state.current_symbol, body.side, px,
        margin_usdt=body.amount or cfg.amount_usdt,
        leverage=cfg.leverage, category=cfg.category, mgn_mode=cfg.margin_mode,
        client_oid=f"t{int(time.time())}",
        sim=paper, ref_price=last,
    )
    r["note"] = "测试单挂在盘口 ±3%，正常不成交，可撤单；不进入持仓管理"
    if r.get("ok"):
        state.add_order({**r, "kind": "test", "tf": "test", "sig_type": body.side, "trigger": last})
    return r


class CancelIn(BaseModel):
    orderId: str
    symbol:  Optional[str] = None


@router.post("/api/trade/cancel")
async def cancel_order(body: CancelIn):
    return await trade.cancel(body.symbol or state.current_symbol, body.orderId,
                              state.trade_cfg.category, sim=state.trade_cfg.paper)


# ─── WebSocket ───────────────────────────────────────────────────

SNAPSHOT_CAP = 500      # 单周期快照最多带多少根，控制 payload 体积


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    state.clients.add(ws)
    logger.info(f"客户端连接，共 {len(state.clients)} 个")
    try:
        candles = {
            tf: (lst[-SNAPSHOT_CAP:] if len(lst) > SNAPSHOT_CAP else lst)
            for tf, lst in state.all_candles().items()
        }
        await ws.send_json({
            "type":    "snapshot",
            "symbol":  state.current_symbol,
            "ticker":  vars(state.ticker),
            "candles": candles,
            "signals": state.signals,
            "params":  vars(state.params),
            "tfs":     [{"tf": tf, "label": cfg["label"]} for tf, cfg in TF_CONFIG.items()],
            "bias_tfs": BIAS_TFS,
            "trade_config": {**vars(state.trade_cfg), "configured": trade.configured,
                             "exchange": "OKX"},
            "exit_rules": _all_rules(),
            "orders":  state.orders[-50:],
            "position": state.position.to_dict(state.ticker.last) if state.position else None,
            "closed":  state.closed[-20:],
            # 多品种：所有品种的最新价 / 持仓 / 品种配置
            "tickers":   {sym: vars(st.ticker) for sym, st in state.stores.items()},
            "positions": {sym: st.position.to_dict(st.ticker.last)
                          for sym, st in state.stores.items() if st.position},
            "symbols":   _symbols_payload(),
            "max_symbols": MAX_SYMBOLS,
        })
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        state.clients.discard(ws)
        logger.info(f"客户端断开，剩余 {len(state.clients)} 个")
