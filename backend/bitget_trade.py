"""
Bitget 永续下单（API v2）

签名：timestamp + METHOD + requestPath(+query) + body
      → HMAC-SHA256(secret) → base64

模拟盘：Demo API Key + 请求头 paptrading: 1
       （与实盘 Key 不通用）

数量：USDT 永续 size 按【币】计（不是 OKX 张）。
内部统一成与 OKX 相同的返回字段：qty=币数、ct_val=1，便于执行器复用。

品种映射：BTC-USDT-SWAP / BTC-USDT → BTCUSDT
行情仍走 OKX；本模块只负责成交。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

BASE_URL = "https://api.bitget.com"
PRODUCT = "USDT-FUTURES"

API_KEY = ""
API_SECRET = ""
PASSPHRASE = ""
SIMULATED = True
configured = False

_spec_cache: dict = {}
_spec_ts = 0.0
_SPEC_TTL = 3600

_LIMIT_TYPES = ("limit", "ioc", "fok", "post_only")
_DEAD_STATES = ("canceled", "cancelled", "rejected", "expired")


def configure(api_key: str, api_secret: str, passphrase: str, simulated: bool = True):
    global API_KEY, API_SECRET, PASSPHRASE, SIMULATED, configured
    API_KEY = (api_key or "").strip()
    API_SECRET = (api_secret or "").strip()
    PASSPHRASE = (passphrase or "").strip()
    SIMULATED = bool(simulated)
    configured = bool(API_KEY and API_SECRET and PASSPHRASE)


def to_symbol(inst_id: str) -> str:
    """BTC-USDT-SWAP / BTC-USDT → BTCUSDT"""
    s = (inst_id or "").upper().replace("-SWAP", "").replace("-", "")
    return s


def to_swap(inst_id: str) -> str:
    """对外仍返回内部统一键风格，便于日志；实际下单用 to_symbol。"""
    s = (inst_id or "").upper()
    return s if s.endswith("-SWAP") else f"{s.replace('-SWAP', '')}-SWAP" if "-" in s else f"{s}-USDT-SWAP"


def to_spot(inst_id: str) -> str:
    return (inst_id or "").upper().replace("-SWAP", "")


def _num(x) -> float:
    try:
        return float(x) if x not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _dec(step: float) -> int:
    s = f"{step:.12f}".rstrip("0")
    return len(s.split(".")[1]) if "." in s and s.split(".")[1] else 0


def _snap(value: float, step: float) -> float:
    if step and step > 0:
        return round(round(value / step) * step, _dec(step))
    return value


def _fmt(v: float, step: float) -> str:
    return f"{v:.{_dec(step)}f}"


def _sign(ts: str, method: str, path: str, body: str) -> str:
    prehash = f"{ts}{method.upper()}{path}{body}"
    digest = hmac.new(API_SECRET.encode(), prehash.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _request(method: str, path: str, payload: dict | None = None,
             sim: bool | None = None, auth: bool = True) -> dict:
    body = ""
    url_path = path
    if method.upper() == "GET" and payload:
        q = urllib.parse.urlencode(payload)
        url_path = f"{path}?{q}"
        body = ""
    elif payload is not None:
        body = json.dumps(payload, separators=(",", ":"))

    headers = {
        "Content-Type": "application/json",
        "locale": "zh-CN",
        "User-Agent": "supertrend-monitor/1.0",
    }
    if auth:
        ts = str(int(time.time() * 1000))
        headers.update({
            "ACCESS-KEY": API_KEY,
            "ACCESS-SIGN": _sign(ts, method, url_path, body),
            "ACCESS-TIMESTAMP": ts,
            "ACCESS-PASSPHRASE": PASSPHRASE,
        })
    if SIMULATED if sim is None else sim:
        headers["paptrading"] = "1"

    req = urllib.request.Request(
        BASE_URL + url_path,
        data=body.encode() if body else None,
        headers=headers,
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            return {"code": str(e.code), "msg": f"HTTP {e.code}"}
    except Exception as e:
        return {"code": "-1", "msg": f"{type(e).__name__}: {e}"}


def _ok(r: dict) -> bool:
    return str(r.get("code")) in ("00000", "0")


def _err(r: dict) -> str:
    return f"{r.get('code')}: {r.get('msg') or r.get('data') or ''}"


def _fetch_specs() -> dict:
    r = _request("GET", "/api/v2/mix/market/contracts",
                 {"productType": PRODUCT}, auth=False, sim=False)
    if not _ok(r):
        logger.warning(f"拉取 Bitget 合约规格失败: {_err(r)}")
        return {}
    out = {}
    for row in r.get("data") or []:
        sym = (row.get("symbol") or "").upper()
        if not sym:
            continue
        price_place = int(_num(row.get("pricePlace")) or 2)
        vol_place = int(_num(row.get("volumePlace")) or 4)
        size_mult = _num(row.get("sizeMultiplier")) or (10 ** -vol_place)
        min_sz = _num(row.get("minTradeNum")) or size_mult
        tick = 10 ** (-price_place)
        out[sym] = {
            "tick_sz": tick,
            "lot_sz": size_mult,
            "min_sz": min_sz,
            "ct_val": 1.0,       # Bitget size 按币
            "ct_mult": 1.0,
            "ct_ccy": row.get("baseCoin") or "",
            "max_lev": _num(row.get("maxLever")) or 125,
            "state": "live" if (row.get("symbolStatus") or row.get("status") or "normal")
                     in ("normal", "listed", "live", "online", "") else row.get("symbolStatus"),
            "inst_type": "SWAP",
            "raw_symbol": sym,
        }
    logger.info(f"Bitget USDT 永续规格已缓存：{len(out)} 个")
    return out


async def get_spec(inst_id: str, inst_type: str = "SWAP") -> dict:
    global _spec_cache, _spec_ts
    if time.time() - _spec_ts > _SPEC_TTL or not _spec_cache:
        loop = asyncio.get_event_loop()
        new = await loop.run_in_executor(None, _fetch_specs)
        if new:
            _spec_cache = new
            _spec_ts = time.time()
    sym = to_symbol(inst_id)
    return _spec_cache.get(sym) or {
        "tick_sz": 0.1, "lot_sz": 0.001, "min_sz": 0.001,
        "ct_val": 1.0, "ct_mult": 1.0, "ct_ccy": "", "max_lev": 125,
        "state": "unknown", "inst_type": "SWAP", "raw_symbol": sym,
    }


async def get_market_price(inst_id: str, category: str = "SWAP",
                           sim: bool | None = None) -> float | None:
    """拉取 Bitget 指定品种的最新成交价。"""
    if not configured:
        return None
    if category != "SWAP":
        return None
    sym = to_symbol(inst_id)
    params = {"productType": PRODUCT, "symbol": sym}
    loop = asyncio.get_event_loop()
    r = await loop.run_in_executor(None, _request, "GET",
                                   "/api/v2/mix/market/symbol-price", params,
                                   False, False)
    if not _ok(r):
        logger.warning(f"Bitget 实时价格获取失败: {_err(r)}")
        return None
    data = r.get("data") or []
    if isinstance(data, dict):
        data = [data]
    row = data[0] if data else {}
    price = _num(row.get("price") or row.get("lastPr") or row.get("markPrice"))
    return price or None


async def set_leverage(inst_id: str, leverage: int, mgn_mode: str = "cross",
                       sim: bool | None = None) -> dict:
    if not configured:
        return {"ok": False, "error": "未配置 Bitget API 密钥"}
    payload = {
        "symbol": to_symbol(inst_id),
        "productType": PRODUCT,
        "marginCoin": "USDT",
        "leverage": str(leverage),
    }
    # Bitget：crossed / isolated
    mm = "crossed" if mgn_mode == "cross" else "isolated"
    payload["marginMode"] = mm
    loop = asyncio.get_event_loop()
    r = await loop.run_in_executor(None, _request, "POST",
                                   "/api/v2/mix/account/set-leverage", payload, sim)
    ok = _ok(r)
    if not ok:
        logger.warning(f"[Bitget 设置杠杆失败] {payload} → {_err(r)}")
    return {"ok": ok, "leverage": leverage, "error": None if ok else _err(r)}


async def query_order(inst_id: str, order_id: str, category: str = "SWAP",
                      sim: bool | None = None) -> dict:
    if not configured:
        return {"ok": False, "error": "未配置 Bitget API 密钥"}
    params = {
        "symbol": to_symbol(inst_id),
        "productType": PRODUCT,
        "orderId": order_id,
    }
    loop = asyncio.get_event_loop()
    r = await loop.run_in_executor(None, _request, "GET",
                                   "/api/v2/mix/order/detail", params, sim)
    data = r.get("data") or {}
    if isinstance(data, list):
        data = data[0] if data else {}
    # 归一成 OKX 风格字段，confirm_fill 可复用
    if data:
        state = (data.get("state") or data.get("status") or "").lower()
        # Bitget: live/new/partially_filled/filled/canceled
        mapped = {
            "new": "live", "live": "live",
            "partially_filled": "partially_filled",
            "filled": "filled",
            "canceled": "canceled", "cancelled": "canceled",
        }.get(state, state)
        data = {
            **data,
            "state": mapped,
            "accFillSz": data.get("accBaseVolume") or data.get("baseVolume")
                         or data.get("size") or data.get("accFillSz") or "0",
            "avgPx": data.get("priceAvg") or data.get("avgPx") or data.get("fillPrice") or "",
            "ordId": data.get("orderId") or order_id,
        }
    return {"ok": _ok(r), "data": data, "error": None if _ok(r) else _err(r)}


async def confirm_fill(
    inst_id: str,
    order_id: str,
    category: str = "SWAP",
    sim: bool | None = None,
    attempts: int = 8,
    delay: float = 0.3,
    cancel_on_timeout: bool = False,
) -> dict:
    last: dict = {}
    for _ in range(max(1, attempts)):
        qr = await query_order(inst_id, order_id, category, sim=sim)
        d = qr.get("data") or {}
        last = d
        state = (d.get("state") or "").lower()
        acc = _num(d.get("accFillSz"))
        avg = _num(d.get("avgPx"))
        if state == "filled" or (acc > 0 and state in _DEAD_STATES):
            return {"filled": True, "avg_px": avg or None, "fill_sz": acc or None,
                    "state": state, "data": d}
        if state in _DEAD_STATES and acc <= 0:
            return {"filled": False, "avg_px": None, "fill_sz": 0.0,
                    "state": state, "data": d}
        await asyncio.sleep(delay)

    if cancel_on_timeout and order_id:
        await cancel(inst_id, order_id, category, sim=sim)
        qr = await query_order(inst_id, order_id, category, sim=sim)
        last = qr.get("data") or last

    acc = _num(last.get("accFillSz"))
    avg = _num(last.get("avgPx"))
    state = (last.get("state") or ("canceled" if cancel_on_timeout else "")).lower()
    if acc > 0:
        return {"filled": True, "avg_px": avg or None, "fill_sz": acc,
                "state": state, "data": last}
    return {"filled": False, "avg_px": None, "fill_sz": 0.0,
            "state": state or "timeout", "data": last}


async def place_order(
    inst_id: str,
    side: str,
    price: float | None,
    sz: float | None = None,
    margin_usdt: float | None = None,
    leverage: int = 1,
    category: str = "SWAP",
    order_type: str = "limit",
    reduce_only: bool = False,
    pos_side: str | None = None,
    mgn_mode: str = "cross",
    client_oid: str | None = None,
    sim: bool | None = None,
    ref_price: float | None = None,
    wait_fill: bool = False,
    wait_sec: float = 8.0,
) -> dict:
    if not configured:
        return {"ok": False, "error": "未配置 Bitget API 密钥"}
    if category != "SWAP":
        return {"ok": False, "error": "Bitget 通道当前仅支持永续合约（SWAP）"}

    sym = to_symbol(inst_id)
    iid = to_swap(inst_id) if "-" in (inst_id or "") else f"{sym[:-4]}-{sym[-4:]}-SWAP" if sym.endswith("USDT") else sym
    # 内部统一用 OKX 风格 symbol 返回
    if not iid.endswith("-SWAP"):
        base = sym.replace("USDT", "")
        iid = f"{base}-USDT-SWAP"

    spec = await get_spec(sym, "SWAP")
    if spec.get("state") not in (None, "live", "unknown", "normal"):
        return {"ok": False, "error": f"{sym} 状态 {spec.get('state')}，不可交易"}

    px = None
    if order_type in _LIMIT_TYPES:
        if not price or price <= 0:
            return {"ok": False, "error": f"限价单委托价非法: {price}"}
        px = _snap(price, spec["tick_sz"])
    calc_px = px or ref_price or price
    if not calc_px or calc_px <= 0:
        return {"ok": False, "error": "无法确定换算价格（市价单需要 ref_price）"}

    # Bitget size = 币数
    if sz is None:
        if margin_usdt is None:
            return {"ok": False, "error": "需提供 sz 或 margin_usdt"}
        notional = margin_usdt * max(1, leverage)
        sz = notional / calc_px

    q = _snap(sz, spec["lot_sz"])
    if spec["min_sz"] and 0 < q < spec["min_sz"]:
        q = spec["min_sz"]
    if q <= 0:
        return {"ok": False, "error": "下单量为 0"}

    force = {"limit": "gtc", "ioc": "ioc", "fok": "fok", "post_only": "post_only"}.get(
        order_type, "gtc")
    bg_type = "market" if order_type == "market" else "limit"

    payload = {
        "symbol": sym,
        "productType": PRODUCT,
        "marginMode": "crossed" if mgn_mode == "cross" else "isolated",
        "marginCoin": "USDT",
        "size": _fmt(q, spec["lot_sz"]),
        "side": side.lower(),
        "orderType": bg_type,
        "force": force,
    }
    if bg_type == "limit":
        payload["price"] = _fmt(px, spec["tick_sz"])
    if reduce_only:
        payload["reduceOnly"] = "YES"
    if client_oid:
        payload["clientOid"] = "".join(ch for ch in client_oid if ch.isalnum())[:32]

    loop = asyncio.get_event_loop()
    r = await loop.run_in_executor(None, _request, "POST",
                                   "/api/v2/mix/order/place-order", payload, sim)

    use_sim = SIMULATED if sim is None else sim
    env = "模拟" if use_sim else "实盘"
    data = r.get("data") or {}
    if isinstance(data, list):
        data = data[0] if data else {}

    if _ok(r) and data.get("orderId"):
        oid = data.get("orderId")
        fill_px, fill_sz, fill_state = None, None, None
        if oid and (order_type == "market" or wait_fill):
            delay = 0.3
            attempts = max(3, int(wait_sec / delay)) if wait_fill else 3
            info = await confirm_fill(
                iid, oid, category, sim=sim,
                attempts=attempts, delay=delay,
                cancel_on_timeout=bool(wait_fill),
            )
            fill_px = info.get("avg_px")
            fill_sz = info.get("fill_sz") or None
            fill_state = info.get("state")
            if wait_fill and not info.get("filled"):
                logger.warning(f"[Bitget 未成交已撤销({env})] {sym} {side} ordId={oid}")
                return {
                    "ok": False,
                    "error": f"未成交已撤销（{fill_state or 'timeout'}）",
                    "orderId": oid, "clientOid": data.get("clientOid"),
                    "symbol": iid, "side": side, "price": px or calc_px, "qty": q,
                    "paper": use_sim, "ts": int(time.time() * 1000),
                    "fill_confirmed": False, "fill_state": fill_state,
                    "exchange": "bitget",
                }
        eff_px = fill_px or px or calc_px
        eff_q = fill_sz or q
        notional = eff_q * eff_px
        logger.info(
            f"[Bitget 下单成功({env})] {sym} {side}{'(平仓)' if reduce_only else ''} "
            f"{payload['size']}币 @ {fill_px or payload.get('price', '市价')} → {oid}"
        )
        return {
            "ok": True, "orderId": oid, "clientOid": data.get("clientOid"),
            "symbol": iid, "side": side, "price": eff_px,
            "qty": eff_q, "coin_qty": eff_q,
            "notional": round(notional, 4),
            "margin": round(notional / max(1, leverage), 4),
            "leverage": leverage, "reduce_only": reduce_only, "category": category,
            "ct_val": 1.0, "paper": use_sim, "ts": int(time.time() * 1000),
            "fill_confirmed": bool(fill_px) or bool(wait_fill and fill_sz),
            "fill_state": fill_state, "exchange": "bitget",
        }

    logger.warning(f"[Bitget 下单失败] {payload} → {_err(r)}")
    return {
        "ok": False, "error": _err(r),
        "symbol": iid, "side": side, "price": px, "qty": q,
        "paper": use_sim, "ts": int(time.time() * 1000), "exchange": "bitget",
    }


async def get_positions(inst_id: str | None = None, category: str = "SWAP",
                        sim: bool | None = None) -> dict:
    if not configured:
        return {"ok": False, "error": "未配置 Bitget API 密钥"}
    params = {"productType": PRODUCT, "marginCoin": "USDT"}
    if inst_id:
        params["symbol"] = to_symbol(inst_id)
    loop = asyncio.get_event_loop()
    r = await loop.run_in_executor(None, _request, "GET",
                                   "/api/v2/mix/position/all-position", params, sim)
    if not _ok(r):
        return {"ok": False, "data": [], "error": _err(r)}
    rows = []
    for row in r.get("data") or []:
        sym = (row.get("symbol") or "").upper()
        total = abs(_num(row.get("total") or row.get("available") or row.get("holdVolume")))
        # 归一成 OKX 字段，executor._exchange_qty 认 instId + pos
        base = sym.replace("USDT", "") if sym.endswith("USDT") else sym
        rows.append({
            **row,
            "instId": f"{base}-USDT-SWAP",
            "pos": str(total),
        })
    return {"ok": True, "data": rows, "error": None}


async def cancel(inst_id: str, order_id: str, category: str = "SWAP",
                 sim: bool | None = None) -> dict:
    if not configured:
        return {"ok": False, "error": "未配置 Bitget API 密钥"}
    payload = {
        "symbol": to_symbol(inst_id),
        "productType": PRODUCT,
        "orderId": order_id,
    }
    loop = asyncio.get_event_loop()
    r = await loop.run_in_executor(None, _request, "POST",
                                   "/api/v2/mix/order/cancel-order", payload, sim)
    return {"ok": _ok(r), "error": None if _ok(r) else _err(r)}


async def list_pending(inst_id: str, category: str = "SWAP",
                       sim: bool | None = None) -> dict:
    if not configured:
        return {"ok": False, "error": "未配置 Bitget API 密钥", "data": []}
    params = {
        "symbol": to_symbol(inst_id),
        "productType": PRODUCT,
    }
    loop = asyncio.get_event_loop()
    r = await loop.run_in_executor(None, _request, "GET",
                                   "/api/v2/mix/order/orders-pending", params, sim)
    rows = r.get("data") or []
    if isinstance(rows, dict):
        rows = rows.get("entrustedList") or rows.get("orderList") or []
    # 归一 ordId
    out = []
    for row in rows:
        out.append({**row, "ordId": row.get("orderId") or row.get("ordId")})
    return {"ok": _ok(r), "data": out, "error": None if _ok(r) else _err(r)}


async def cancel_pending(inst_id: str, category: str = "SWAP",
                         sim: bool | None = None) -> dict:
    listed = await list_pending(inst_id, category, sim=sim)
    if not listed.get("ok"):
        return {"ok": False, "cancelled": [], "error": listed.get("error")}
    cancelled, errors = [], []
    for row in listed.get("data") or []:
        oid = row.get("ordId") or row.get("orderId")
        if not oid:
            continue
        r = await cancel(inst_id, oid, category, sim=sim)
        if r.get("ok"):
            cancelled.append(oid)
        else:
            errors.append(f"{oid}: {r.get('error')}")
    if cancelled:
        logger.info(f"[Bitget 撤销未成交] {inst_id} {len(cancelled)} 笔")
    return {"ok": True, "cancelled": cancelled, "errors": errors}


async def ping(sim: bool | None = None) -> dict:
    if not configured:
        return {"ok": False, "error": "未配置 Bitget API 密钥"}
    params = {"productType": PRODUCT}
    loop = asyncio.get_event_loop()
    r = await loop.run_in_executor(None, _request, "GET",
                                   "/api/v2/mix/account/accounts", params, sim)
    use_sim = SIMULATED if sim is None else sim
    if not _ok(r):
        return {"ok": False, "paper": use_sim, "error": _err(r), "exchange": "bitget"}
    rows = r.get("data") or []
    if isinstance(rows, dict):
        rows = [rows]
    row = rows[0] if rows else {}
    equity = _num(row.get("accountEquity") or row.get("usdtEquity")
                  or row.get("equity") or row.get("crossedMaxAvailable"))
    avail = _num(row.get("available") or row.get("crossedMaxAvailable")
                 or row.get("maxOpenPosAvailable") or row.get("available"))
    return {
        "ok": True, "paper": use_sim,
        "equity": round(equity, 4),
        "usdt_avail": round(avail, 4),
        "exchange": "bitget",
    }
