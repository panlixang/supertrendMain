"""形态识别页 · 自动下单的接口（独立于首页的 /api/trade/*）。

配置与凭据都存在 pattern_trade.json / pattern_credentials.json，
与首页 settings.json / .okx_credentials.json 完全隔离。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

import pattern_trade

router = APIRouter()


class CfgIn(BaseModel):
    enabled:       Optional[bool]  = None
    paper:         Optional[bool]  = None
    category:      Optional[str]   = None
    margin_mode:   Optional[str]   = None
    price_offset:  Optional[float] = None
    exchange:      Optional[str]   = None
    block_4h:      Optional[bool]  = None

    cooldown_sec:  Optional[int]   = None
    poll_sec:      Optional[int]   = None
    tp1_pct:         Optional[float] = None
    tp1_ratio:       Optional[float] = None
    tp2_pct:         Optional[float] = None
    tp2_ratio:       Optional[float] = None
    tp3_pct:         Optional[float] = None   # 0 = 反向信号平仓
    tp3_ratio:       Optional[float] = None
    tp3_mode:        Optional[str]   = None   # pct | reverse_signal
    sl_pct:          Optional[float] = None
    move_sl_to_entry: Optional[bool]  = None
    trail_with_st:    Optional[bool]  = None
    reverse_close:    Optional[bool]  = None
    exit_mode:        Optional[str]   = None   # 全局档位：multi=三挡 | single=单档
    sl_mode:          Optional[str]   = None   # st=超趋线 | pct=固定百分比真实硬止损


class KeysIn(BaseModel):
    exchange_id: Optional[str] = None
    api_key:     Optional[str] = None
    api_secret:  Optional[str] = None
    passphrase:  Optional[str] = None


class SymbolIn(BaseModel):
    action:      str   = "add"          # add / update / remove
    symbol:      str
    enabled:     Optional[bool]  = None
    margin_usdt: Optional[float] = None
    leverage:    Optional[int]   = None
    allow_tfs:   Optional[list]  = None
    sizing_mode:     Optional[str]   = None   # 保证金口径：fixed=固定U | equity_pct=净值百分比
    equity_pct:      Optional[float] = None   # equity_pct 模式的百分比（1-100）
    tp1_pct:         Optional[float] = None
    tp1_ratio:       Optional[float] = None
    tp2_pct:         Optional[float] = None
    tp2_ratio:       Optional[float] = None
    tp3_pct:         Optional[float] = None   # 0 = 反向信号平仓
    tp3_ratio:       Optional[float] = None
    tp3_mode:        Optional[str]   = None   # pct | reverse_signal
    sl_pct:          Optional[float] = None
    move_sl_to_entry: Optional[bool]  = None
    trail_with_st:    Optional[bool]  = None
    reverse_close:    Optional[bool]  = None
    filter_v3:        Optional[bool]  = None
    exit_mode:        Optional[str]   = None   # 档位：multi=三挡 | single=单档
    sl_mode:          Optional[str]   = None   # st=超趋线 | pct=固定百分比真实硬止损


def _symbols_view() -> list[dict]:
    t = pattern_trade.trader
    out = []
    for sym, sc in t.symbols.items():
        st = t.stores.get(sym)
        row = {
            "symbol": sym, "enabled": sc.enabled,
            "margin_usdt": sc.margin_usdt, "leverage": sc.leverage,
            "allow_tfs": list(sc.allow_tfs),
            "sizing_mode": sc.sizing_mode, "equity_pct": sc.equity_pct,
            "tp1_pct": sc.tp1_pct, "tp1_ratio": sc.tp1_ratio,
            "tp2_pct": sc.tp2_pct, "tp2_ratio": sc.tp2_ratio,
            "tp3_pct": sc.tp3_pct, "tp3_ratio": sc.tp3_ratio,
            "tp3_mode": sc.tp3_mode,
            "exit_mode": sc.exit_mode,
            "sl_pct": sc.sl_pct,
            "sl_mode": sc.sl_mode,
            "move_sl_to_entry": sc.move_sl_to_entry,
            "trail_with_st": sc.trail_with_st, "reverse_close": sc.reverse_close,
            "filter_v3": sc.filter_v3,
            "position": None,
        }
        if st and st.position:
            row["position"] = st.position.to_dict(st.ticker.last)
        out.append(row)
    return out


@router.get("/api/pattern/trade/config")
async def get_config():
    t = pattern_trade.trader
    return {
        "cfg": t.cfg.__dict__,
        "keys": t.keys_public(),
        "symbols": _symbols_view(),
        "tfs": pattern_trade.ALL_TFS,
    }


@router.post("/api/pattern/trade/config")
async def set_config(body: CfgIn):
    t = pattern_trade.trader
    kw = {k: v for k, v in body.model_dump().items() if v is not None}
    t.update_cfg(**kw)
    return {"ok": True, "cfg": t.cfg.__dict__, "keys": t.keys_public()}


@router.get("/api/pattern/trade/keys")
async def get_keys():
    return pattern_trade.trader.keys_public()


@router.post("/api/pattern/trade/keys")
async def set_keys(body: KeysIn):
    t = pattern_trade.trader
    return t.set_keys(
        exchange_id=body.exchange_id or t.cfg.exchange,
        api_key=body.api_key or "",
        api_secret=body.api_secret or "",
        passphrase=body.passphrase or "",
    )


@router.post("/api/pattern/trade/symbols")
async def symbols(body: SymbolIn):
    t = pattern_trade.trader
    # 以前这里只转发 enabled/margin_usdt/leverage/allow_tfs，
    # 出场参数（tp1~tp3、sl_pct、保本、ST跟踪、reverse_close）和 filter_v3 全被丢掉，
    # 表现为「面板改了保存不了、刷新回默认值」。现在按 body 显式传值全量转发。
    fields = {k: v for k, v in body.model_dump().items()
              if k not in ("action", "symbol") and v is not None}
    if body.action == "add":
        r = t.add_symbol(body.symbol, **fields)
    elif body.action == "remove":
        r = t.remove_symbol(body.symbol)
    else:
        r = t.update_symbol(body.symbol, **fields)
    return {**r, "symbols": _symbols_view()}


@router.get("/api/pattern/trade/state")
async def get_state():
    t = pattern_trade.trader
    positions = []
    for sym, st in t.stores.items():
        if st.position:
            positions.append({**st.position.to_dict(st.ticker.last), "symbol": sym})
    return {
        "running": t._running,
        "positions": positions,
        "orders": t.orders[-100:],
        "events": list(t.events)[-50:],
    }


@router.post("/api/pattern/trade/close")
async def close_position(body: SymbolIn):
    return await pattern_trade.trader.close_symbol(body.symbol)


@router.get("/api/pattern/trade/ping")
async def pattern_ping():
    """用形态页独立凭据查账户，验证连通性 / 模拟盘 / 实盘。"""
    return await pattern_trade.trader.ping()


class PatternTestOrderIn(BaseModel):
    symbol: str = ""


@router.post("/api/pattern/trade/test-order")
async def pattern_test_order(body: PatternTestOrderIn):
    """手动挂一笔测试单，验证本页密钥 / 杠杆 / 下单链路是否通（不进入持仓）。"""
    return await pattern_trade.trader.test_order(body.symbol)
