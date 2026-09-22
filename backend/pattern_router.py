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
    trend_filter:       Optional[bool]  = None
    squeeze_bb_n:       Optional[int]   = None
    squeeze_bb_mult:    Optional[float] = None
    squeeze_width_pct:  Optional[float] = None
    squeeze_vol_n:      Optional[int]   = None
    squeeze_vol_mult:   Optional[float] = None
    donchian_n:         Optional[int]   = None
    waive_mom_n:        Optional[int]   = None
    waive_mom_pct:      Optional[float] = None
    cooldown_sec:  Optional[int]   = None
    poll_sec:      Optional[int]   = None
    tp1_pct:         Optional[float] = None
    tp1_ratio:       Optional[float] = None
    sl_pct:          Optional[float] = None
    move_sl_to_entry: Optional[bool]  = None
    trail_with_st:    Optional[bool]  = None


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
    tp1_pct:         Optional[float] = None
    tp1_ratio:       Optional[float] = None
    sl_pct:          Optional[float] = None
    move_sl_to_entry: Optional[bool]  = None
    trail_with_st:    Optional[bool]  = None


def _symbols_view() -> list[dict]:
    t = pattern_trade.trader
    out = []
    for sym, sc in t.symbols.items():
        st = t.stores.get(sym)
        row = {
            "symbol": sym, "enabled": sc.enabled,
            "margin_usdt": sc.margin_usdt, "leverage": sc.leverage,
            "allow_tfs": list(sc.allow_tfs),
            "tp1_pct": sc.tp1_pct, "tp1_ratio": sc.tp1_ratio,
            "sl_pct": sc.sl_pct,
            "move_sl_to_entry": sc.move_sl_to_entry,
            "trail_with_st": sc.trail_with_st,
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
    if body.action == "add":
        r = t.add_symbol(body.symbol, enabled=body.enabled, margin_usdt=body.margin_usdt,
                         leverage=body.leverage, allow_tfs=body.allow_tfs)
    elif body.action == "remove":
        r = t.remove_symbol(body.symbol)
    else:
        r = t.update_symbol(body.symbol, enabled=body.enabled,
                            margin_usdt=body.margin_usdt, leverage=body.leverage,
                            allow_tfs=body.allow_tfs)
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
