"""
OKX 下单（v5 API）—— 合约 / 现货

签名（官方 REST Authentication）：
    prehash = timestamp + METHOD + requestPath + body
              GET 的 query 算在 requestPath 里，不算 body
    sign    = base64( hmac_sha256(secretKey, prehash) )
    timestamp 必须是 ISO8601 毫秒 UTC，如 2020-12-08T09:08:57.715Z
              （不是 Unix 毫秒！和 Bitget 不同）
              与服务器偏差 >30 秒会报 50102，本机时钟要准

模拟盘：加 header `x-simulated-trading: 1`，用模拟盘专用 API Key
       （实盘 key 与模拟盘 key 不通用）

⚠️ sz 单位是【张数】，不是币数：
    1 张 = ctVal 个 ctValCcy（BTC-USDT-SWAP 的 ctVal=0.01，即 1 张 = 0.01 BTC）
    张数 = 名义价值 / 价格 / ctVal
    现货 SPOT 的 sz 才是币数。
    这一点写错会导致下单量差几十倍，是 OKX 最容易踩的坑。

持仓模式：
    单向(net)   posSide 传 net 或不传，平仓用 reduceOnly
    双向(long/short)  开多=buy/long、开空=sell/short
                      平多=sell/long、平空=buy/short
默认按单向模式（net）下单。

环境变量（见 .env.example）：
    OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE
    OKX_SIMULATED   1=模拟盘（默认）/ 0=实盘
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# OKX 主域名。美国/欧盟账户需换成 us.okx.com / eea.okx.com
BASE_URL = os.environ.get("OKX_BASE_URL", "https://www.okx.com").rstrip("/")

PLACE_ORDER   = "/api/v5/trade/order"
CANCEL_ORDER  = "/api/v5/trade/cancel-order"
ORDER_INFO    = "/api/v5/trade/order"
PENDING_ORDERS = "/api/v5/trade/orders-pending"
POSITIONS     = "/api/v5/account/positions"
SET_LEVERAGE  = "/api/v5/account/set-leverage"
BALANCE       = "/api/v5/account/balance"
INSTRUMENTS   = "/api/v5/public/instruments"

API_KEY    = os.environ.get("OKX_API_KEY", "").strip()
API_SECRET = os.environ.get("OKX_API_SECRET", "").strip()
PASSPHRASE = os.environ.get("OKX_API_PASSPHRASE", "").strip()

# 默认模拟盘。要切实盘必须显式设 OKX_SIMULATED=0，避免手滑真下单。
SIMULATED = os.environ.get("OKX_SIMULATED", "1").strip() != "0"

configured = bool(API_KEY and API_SECRET and PASSPHRASE)

# 前端可改 Key / 交易所；落盘与 settings.json 分开，且永不经 WebSocket 下发明文。
CRED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".okx_credentials.json")
EXCHANGES = (
    {"id": "okx", "label": "OKX", "enabled": True},
    {"id": "bitget", "label": "Bitget", "enabled": True},
)
exchange = "okx"
_store: dict = {
    "okx": {"api_key": API_KEY, "api_secret": API_SECRET, "passphrase": PASSPHRASE},
    "bitget": {
        "api_key": os.environ.get("BITGET_API_KEY", "").strip(),
        "api_secret": os.environ.get("BITGET_API_SECRET", "").strip(),
        "passphrase": os.environ.get("BITGET_API_PASSPHRASE", "").strip(),
    },
}


def _mask_key(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    if len(s) <= 8:
        return "••••"
    return f"{s[:4]}••••{s[-4:]}"


def _active_bucket() -> dict:
    return _store.get(exchange) or _store["okx"]


def _sync_active_globals():
    """把当前交易所的密钥同步到模块级变量（OKX 路径直接读这些）。"""
    global API_KEY, API_SECRET, PASSPHRASE, configured
    b = _active_bucket()
    API_KEY = (b.get("api_key") or "").strip()
    API_SECRET = (b.get("api_secret") or "").strip()
    PASSPHRASE = (b.get("passphrase") or "").strip()
    configured = bool(API_KEY and API_SECRET and PASSPHRASE)
    if exchange == "bitget":
        try:
            import bitget_trade
            bitget_trade.configure(API_KEY, API_SECRET, PASSPHRASE, SIMULATED)
        except Exception as e:
            logger.warning(f"同步 Bitget 密钥失败: {e}")


def credentials_public() -> dict:
    b = _active_bucket()
    return {
        "exchange": exchange,
        "exchanges": [dict(x) for x in EXCHANGES],
        "configured": bool((b.get("api_key") and b.get("api_secret") and b.get("passphrase"))),
        "key_hint": _mask_key(b.get("api_key") or ""),
        "has_secret": bool(b.get("api_secret")),
        "has_passphrase": bool(b.get("passphrase")),
        "env_paper": SIMULATED,
    }


def apply_credentials(api_key: str | None = None, api_secret: str | None = None,
                      passphrase: str | None = None, persist: bool = True,
                      exchange_id: str | None = None) -> dict:
    """运行时替换密钥。空字符串表示不改该项。exchange_id 同时切换下单通道。"""
    global exchange
    ex = (exchange_id or exchange or "okx").strip().lower()
    if ex not in ("okx", "bitget"):
        raise ValueError(f"不支持的交易所: {ex}")
    exchange = ex
    if ex not in _store:
        _store[ex] = {"api_key": "", "api_secret": "", "passphrase": ""}
    b = _store[ex]
    if api_key is not None and str(api_key).strip():
        b["api_key"] = str(api_key).strip()
    if api_secret is not None and str(api_secret).strip():
        b["api_secret"] = str(api_secret).strip()
    if passphrase is not None and str(passphrase).strip():
        b["passphrase"] = str(passphrase).strip()
    _sync_active_globals()
    if persist and configured:
        _save_credentials()
    return credentials_public()


def set_exchange(exchange_id: str, persist: bool = True) -> dict:
    """只切换交易所（沿用该所已保存的密钥）。"""
    return apply_credentials(exchange_id=exchange_id, persist=persist)


def _save_credentials():
    data = {
        "exchange": exchange,
        "okx": dict(_store.get("okx") or {}),
        "bitget": dict(_store.get("bitget") or {}),
        # 兼容旧单层字段（= 当前激活交易所）
        "api_key": API_KEY,
        "api_secret": API_SECRET,
        "passphrase": PASSPHRASE,
    }
    tmp = CRED_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, CRED_FILE)
    try:
        os.chmod(CRED_FILE, 0o600)
    except Exception:
        pass
    logger.info(f"{exchange.upper()} 密钥已更新（仅服务器保存，前端只显示掩码）")


def load_saved_credentials():
    """面板保存的密钥覆盖 .env。文件不存在则继续用环境变量。"""
    global exchange
    if not os.path.exists(CRED_FILE):
        _sync_active_globals()
        return
    try:
        with open(CRED_FILE) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return
        if isinstance(data.get("okx"), dict):
            for k in ("api_key", "api_secret", "passphrase"):
                if data["okx"].get(k):
                    _store["okx"][k] = data["okx"][k]
        if isinstance(data.get("bitget"), dict):
            for k in ("api_key", "api_secret", "passphrase"):
                if data["bitget"].get(k):
                    _store["bitget"][k] = data["bitget"][k]
        # 旧格式：单层 key 视为 OKX
        if data.get("api_key") and not (data.get("okx") or {}).get("api_key"):
            _store["okx"]["api_key"] = data.get("api_key") or ""
            _store["okx"]["api_secret"] = data.get("api_secret") or ""
            _store["okx"]["passphrase"] = data.get("passphrase") or ""
        ex = (data.get("exchange") or "okx").strip().lower()
        if ex in ("okx", "bitget"):
            exchange = ex
        _sync_active_globals()
    except Exception as e:
        logger.warning(f"读取面板密钥失败，改用环境变量: {e}")
        _sync_active_globals()


load_saved_credentials()

if configured:
    logger.info(f"{exchange.upper()} 交易已配置（{'模拟盘' if SIMULATED else '⚠️ 实盘'}，Key {_mask_key(API_KEY)}）")
else:
    logger.info("交易未配置（可在自动下单页填写 OKX / Bitget Key）")


def _use_bitget() -> bool:
    return exchange == "bitget"


def _ts() -> str:
    """OKX 要求 ISO8601 毫秒 UTC：2020-12-08T09:08:57.715Z"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
        f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"


def _sign(ts: str, method: str, path: str, body: str) -> str:
    prehash = f"{ts}{method.upper()}{path}{body}"
    digest = hmac.new(API_SECRET.encode(), prehash.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _request(method: str, path: str, payload: dict | None = None,
             sim: bool | None = None) -> dict:
    """同步 HTTP（在 executor 线程里跑，不要直接在事件循环调用）。"""
    body = json.dumps(payload, separators=(",", ":")) if payload else ""
    ts = _ts()
    headers = {
        "OK-ACCESS-KEY":        API_KEY,
        "OK-ACCESS-SIGN":       _sign(ts, method, path, body),
        "OK-ACCESS-TIMESTAMP":  ts,
        "OK-ACCESS-PASSPHRASE": PASSPHRASE,
        "Content-Type":         "application/json",
        "User-Agent":           "supertrend-monitor/1.0",
    }
    if SIMULATED if sim is None else sim:
        headers["x-simulated-trading"] = "1"

    req = urllib.request.Request(
        BASE_URL + path,
        data=body.encode() if body else None,
        headers=headers,
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        # OKX 的业务错误也带 JSON body，读出来比只报 HTTP 码有用得多
        try:
            return json.loads(e.read())
        except Exception:
            return {"code": str(e.code), "msg": f"HTTP {e.code}"}
    except Exception as e:
        return {"code": "-1", "msg": f"{type(e).__name__}: {e}"}


def _first(r: dict) -> dict:
    """OKX 的 data 是数组，取第一条；里面还有自己的 sCode/sMsg。"""
    d = r.get("data") or []
    return d[0] if d else {}


def _err(r: dict) -> str:
    """OKX 错误可能在外层 code/msg，也可能在 data[0].sCode/sMsg。"""
    row = _first(r)
    if row.get("sCode") and row["sCode"] != "0":
        return f"{row['sCode']}: {row.get('sMsg') or ''}"
    return f"{r.get('code')}: {r.get('msg') or ''}"


def to_swap(inst_id: str) -> str:
    """现货 instId → 永续合约 instId：BTC-USDT → BTC-USDT-SWAP"""
    s = inst_id.upper()
    return s if s.endswith("-SWAP") else f"{s}-SWAP"


def to_spot(inst_id: str) -> str:
    return inst_id.upper().replace("-SWAP", "")


# ─── 合约规格（公开接口，无需签名，缓存 1 小时） ────────────────

_spec_cache: dict = {}      # {inst_type: {instId: spec}}
_spec_ts: dict = {}
_SPEC_TTL = 3600


def _fetch_specs(inst_type: str) -> dict:
    """拉某个品类的全量合约规格。精度/张面值写死会在别的币种上被拒单。"""
    try:
        req = urllib.request.Request(
            f"{BASE_URL}{INSTRUMENTS}?instType={inst_type}",
            headers={"User-Agent": "supertrend-monitor/1.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            d = json.loads(resp.read())
        if d.get("code") != "0":
            return {}
        out = {}
        for r in d.get("data", []):
            out[r["instId"]] = {
                "tick_sz":  float(r.get("tickSz") or 0.1),    # 价格最小变动
                "lot_sz":   float(r.get("lotSz") or 1),       # 数量最小变动（张）
                "min_sz":   float(r.get("minSz") or 1),       # 最小下单量（张）
                "ct_val":   float(r.get("ctVal") or 1),       # 1 张 = 多少个币
                "ct_mult":  float(r.get("ctMult") or 1),
                "ct_ccy":   r.get("ctValCcy") or "",
                "max_lev":  float(r.get("lever") or 1),
                "state":    r.get("state"),
                "inst_type": r.get("instType"),
            }
        logger.info(f"OKX {inst_type} 合约规格已缓存：{len(out)} 个")
        return out
    except Exception as e:
        logger.warning(f"拉取 OKX {inst_type} 规格失败: {e}")
        return {}


async def get_spec(inst_id: str, inst_type: str = "SWAP") -> dict:
    """取合约规格，带缓存。取不到时给保守默认值。"""
    if _use_bitget():
        import bitget_trade
        return await bitget_trade.get_spec(inst_id, inst_type)
    if inst_type not in _spec_ts or time.time() - _spec_ts.get(inst_type, 0) > _SPEC_TTL:
        loop = asyncio.get_event_loop()
        new = await loop.run_in_executor(None, _fetch_specs, inst_type)
        if new:
            _spec_cache[inst_type] = new
            _spec_ts[inst_type] = time.time()
    return (_spec_cache.get(inst_type) or {}).get(inst_id) or {
        "tick_sz": 0.1, "lot_sz": 1, "min_sz": 1, "ct_val": 1, "ct_mult": 1,
        "ct_ccy": "", "max_lev": 1, "state": "unknown", "inst_type": inst_type,
    }


def _dec(step: float) -> int:
    """由最小变动单位反推小数位数，用于格式化。"""
    s = f"{step:.12f}".rstrip("0")
    return len(s.split(".")[1]) if "." in s and s.split(".")[1] else 0


def _snap(value: float, step: float) -> float:
    """吸附到 step 的整数倍（价格 tickSz / 数量 lotSz 都要求）。"""
    if step and step > 0:
        return round(round(value / step) * step, _dec(step))
    return value


def _fmt(v: float, step: float) -> str:
    """定点格式化，避免 1e-05 这类科学计数法混进请求体。"""
    return f"{v:.{_dec(step)}f}"


def _num(x) -> float:
    try:
        return float(x) if x not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


# ─── 下单 ────────────────────────────────────────────────────────

async def set_leverage(inst_id: str, leverage: int, mgn_mode: str = "cross",
                       sim: bool | None = None) -> dict:
    """设置杠杆。开仓前调一次，重复设同一值不报错。"""
    if _use_bitget():
        import bitget_trade
        return await bitget_trade.set_leverage(inst_id, leverage, mgn_mode, sim=sim)
    if not configured:
        return {"ok": False, "error": "未配置 OKX API 密钥"}
    payload = {"instId": to_swap(inst_id), "lever": str(leverage), "mgnMode": mgn_mode}
    loop = asyncio.get_event_loop()
    r = await loop.run_in_executor(None, _request, "POST", SET_LEVERAGE, payload, sim)
    ok = r.get("code") == "0"
    if not ok:
        logger.warning(f"[设置杠杆失败] {payload} → {_err(r)}")
    return {"ok": ok, "leverage": leverage, "error": None if ok else _err(r)}


_LIMIT_TYPES = ("limit", "ioc", "fok", "post_only")
_DEAD_STATES = ("canceled", "mmp_canceled", "expired")


async def confirm_fill(
    inst_id: str,
    order_id: str,
    category: str = "SWAP",
    sim: bool | None = None,
    attempts: int = 8,
    delay: float = 0.3,
    cancel_on_timeout: bool = False,
) -> dict:
    """轮询订单直到终态。返回 filled / avg_px / fill_sz / state。

    开仓必须等成交确认：OKX 下单成功只代表委托进簿，不代表成交。
    """
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
    side: str,                          # buy / sell
    price: float | None,                # 限价单必填；市价单传 None
    sz: float | None = None,            # 直接指定张数（合约）/ 币数（现货），平仓用
    margin_usdt: float | None = None,   # 或按保证金开仓，内部换算
    leverage: int = 1,
    category: str = "SWAP",             # SWAP 合约 / SPOT 现货
    order_type: str = "limit",          # limit / market / ioc
    reduce_only: bool = False,
    pos_side: str | None = None,        # 双向持仓模式才需要
    mgn_mode: str = "cross",            # cross 全仓 / isolated 逐仓
    client_oid: str | None = None,
    sim: bool | None = None,
    ref_price: float | None = None,
    wait_fill: bool = False,
    wait_sec: float = 8.0,
) -> dict:
    """统一下单入口。

    开仓：传 margin_usdt + leverage
          名义价值 = 保证金 × 杠杆
          合约张数 = 名义价值 / 价格 / ctVal   ← ctVal 这一步别漏
    平仓：传 sz + reduce_only=True
    wait_fill=True 时轮询成交；超时撤剩余，未成交返回 ok=False（不记持仓）。
    """
    if _use_bitget():
        import bitget_trade
        return await bitget_trade.place_order(
            inst_id, side, price, sz=sz, margin_usdt=margin_usdt, leverage=leverage,
            category=category, order_type=order_type, reduce_only=reduce_only,
            pos_side=pos_side, mgn_mode=mgn_mode, client_oid=client_oid, sim=sim,
            ref_price=ref_price, wait_fill=wait_fill, wait_sec=wait_sec,
        )
    if not configured:
        return {"ok": False, "error": "未配置 OKX API 密钥"}

    is_swap = category == "SWAP"
    iid = to_swap(inst_id) if is_swap else to_spot(inst_id)
    spec = await get_spec(iid, "SWAP" if is_swap else "SPOT")
    if spec.get("state") not in (None, "live", "unknown"):
        return {"ok": False, "error": f"{iid} 状态 {spec.get('state')}，不可交易"}

    # 定价。ioc/fok 也是限价（立刻成交或撤销），必须带 px。
    px = None
    if order_type in _LIMIT_TYPES:
        if not price or price <= 0:
            return {"ok": False, "error": f"限价单委托价非法: {price}"}
        px = _snap(price, spec["tick_sz"])
    calc_px = px or ref_price or price
    if not calc_px or calc_px <= 0:
        return {"ok": False, "error": "无法确定换算价格（市价单需要 ref_price）"}

    # 定量
    if sz is None:
        if margin_usdt is None:
            return {"ok": False, "error": "需提供 sz 或 margin_usdt"}
        notional = margin_usdt * max(1, leverage)     # 名义价值 = 保证金 × 杠杆
        if is_swap:
            # 张数 = 名义价值 / 价格 / 每张面值
            sz = notional / calc_px / (spec["ct_val"] * spec["ct_mult"])
        else:
            sz = notional / calc_px                   # 现货直接是币数

    q = _snap(sz, spec["lot_sz"])
    if spec["min_sz"] and 0 < q < spec["min_sz"]:
        q = spec["min_sz"]
    if q <= 0:
        hint = ""
        if is_swap and margin_usdt:
            need = spec["min_sz"] * spec["ct_val"] * spec["ct_mult"] * calc_px / max(1, leverage)
            hint = f"（最小 {spec['min_sz']} 张 ≈ 需保证金 {need:.2f} USDT，请调大金额或杠杆）"
        return {"ok": False, "error": f"下单量为 0{hint}"}

    payload = {
        "instId":  iid,
        "tdMode":  mgn_mode if is_swap else "cash",   # 现货用 cash
        "side":    side,
        "ordType": order_type,
        "sz":      _fmt(q, spec["lot_sz"]),
    }
    if order_type in _LIMIT_TYPES:
        payload["px"] = _fmt(px, spec["tick_sz"])
    if is_swap:
        if pos_side:                     # 双向持仓模式
            payload["posSide"] = pos_side
        if reduce_only:
            payload["reduceOnly"] = True
    # 带 clOrdId：超时/未知错误时可反查，避免重复下单
    if client_oid:
        # OKX 要求字母数字，长度 1-32
        payload["clOrdId"] = "".join(ch for ch in client_oid if ch.isalnum())[:32]

    loop = asyncio.get_event_loop()
    r = await loop.run_in_executor(None, _request, "POST", PLACE_ORDER, payload, sim)

    use_sim = SIMULATED if sim is None else sim
    env = "模拟" if use_sim else "实盘"
    row = _first(r)
    if r.get("code") == "0" and row.get("sCode") == "0":
        fill_px, fill_sz = None, None
        fill_state = None
        oid = row.get("ordId")
        # 市价 / 要求等成交：即时响应里没有 avgPx，必须反查。
        # 拿 ref_price 记账出过真实事故（ticker 串品种，盈亏虚高几十倍）。
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
                logger.warning(
                    f"[未成交已撤销({env})] {iid} {side} ordId={oid} "
                    f"state={fill_state}"
                )
                return {
                    "ok": False,
                    "error": f"未成交已撤销（{fill_state or 'timeout'}）",
                    "orderId": oid, "clientOid": row.get("clOrdId"),
                    "symbol": iid, "side": side, "price": px or calc_px, "qty": q,
                    "paper": use_sim, "ts": int(time.time() * 1000),
                    "fill_confirmed": False, "fill_state": fill_state,
                }
            if order_type == "market" and not fill_px:
                logger.warning(f"[市价单成交价未查到] {iid} ordId={oid}，"
                               f"退回参考价 {calc_px} 记账（盈亏统计可能不准）")
        eff_px = fill_px or px or calc_px
        eff_q = fill_sz or q
        # 合约的名义价值要换算回 USDT 显示
        notional = eff_q * (spec["ct_val"] * spec["ct_mult"] if is_swap else 1) * eff_px
        logger.info(
            f"[下单成功({env})] {iid} {side}{'(平仓)' if reduce_only else ''} "
            f"{payload['sz']}{'张' if is_swap else ''} @ "
            f"{fill_px or payload.get('px', '市价')} → ordId={oid}"
            f"{' 已成交' if fill_px else ''}"
        )
        return {
            "ok": True, "orderId": oid, "clientOid": row.get("clOrdId"),
            "symbol": iid, "side": side, "price": eff_px,
            "qty": eff_q,                                # 张数（合约）/ 币数（现货）
            "coin_qty": eff_q * spec["ct_val"] * spec["ct_mult"] if is_swap else eff_q,
            "notional": round(notional, 4),
            "margin": round(notional / max(1, leverage), 4),
            "leverage": leverage, "reduce_only": reduce_only, "category": category,
            "ct_val": spec["ct_val"], "paper": use_sim, "ts": int(time.time() * 1000),
            "fill_confirmed": bool(fill_px) or bool(wait_fill and fill_sz),
            "fill_state": fill_state,
        }

    logger.warning(f"[下单失败] {payload} → {_err(r)}")
    return {
        "ok": False, "error": _err(r),
        "symbol": iid, "side": side, "price": px, "qty": q,
        "paper": use_sim, "ts": int(time.time() * 1000),
    }


async def get_positions(inst_id: str | None = None, category: str = "SWAP",
                        sim: bool | None = None) -> dict:
    """查交易所实际持仓，用于和本地状态机对账。"""
    if _use_bitget():
        import bitget_trade
        return await bitget_trade.get_positions(inst_id, category, sim=sim)
    if not configured:
        return {"ok": False, "error": "未配置 OKX API 密钥"}
    path = f"{POSITIONS}?instType={category}"
    if inst_id:
        path += f"&instId={to_swap(inst_id) if category == 'SWAP' else to_spot(inst_id)}"
    loop = asyncio.get_event_loop()
    r = await loop.run_in_executor(None, _request, "GET", path, None, sim)
    return {"ok": r.get("code") == "0", "data": r.get("data"), "error": None if r.get("code") == "0" else _err(r)}


async def cancel(inst_id: str, order_id: str, category: str = "SWAP",
                 sim: bool | None = None) -> dict:
    if _use_bitget():
        import bitget_trade
        return await bitget_trade.cancel(inst_id, order_id, category, sim=sim)
    if not configured:
        return {"ok": False, "error": "未配置 OKX API 密钥"}
    iid = to_swap(inst_id) if category == "SWAP" else to_spot(inst_id)
    payload = {"instId": iid, "ordId": order_id}
    loop = asyncio.get_event_loop()
    r = await loop.run_in_executor(None, _request, "POST", CANCEL_ORDER, payload, sim)
    ok = r.get("code") == "0" and _first(r).get("sCode") == "0"
    return {"ok": ok, "error": None if ok else _err(r)}


async def list_pending(inst_id: str, category: str = "SWAP",
                       sim: bool | None = None) -> dict:
    """该品种当前未成交委托。"""
    if _use_bitget():
        import bitget_trade
        return await bitget_trade.list_pending(inst_id, category, sim=sim)
    if not configured:
        return {"ok": False, "error": "未配置 OKX API 密钥", "data": []}
    iid = to_swap(inst_id) if category == "SWAP" else to_spot(inst_id)
    path = f"{PENDING_ORDERS}?instType={category}&instId={iid}"
    loop = asyncio.get_event_loop()
    r = await loop.run_in_executor(None, _request, "GET", path, None, sim)
    ok = r.get("code") == "0"
    return {"ok": ok, "data": r.get("data") or [],
            "error": None if ok else _err(r)}


async def cancel_pending(inst_id: str, category: str = "SWAP",
                         sim: bool | None = None) -> dict:
    """撤销该品种全部未成交委托，避免旧限价单事后成交变成幽灵仓。"""
    if _use_bitget():
        import bitget_trade
        return await bitget_trade.cancel_pending(inst_id, category, sim=sim)
    listed = await list_pending(inst_id, category, sim=sim)
    if not listed.get("ok"):
        return {"ok": False, "cancelled": [], "error": listed.get("error")}
    cancelled, errors = [], []
    for row in listed.get("data") or []:
        oid = row.get("ordId")
        if not oid:
            continue
        r = await cancel(inst_id, oid, category, sim=sim)
        if r.get("ok"):
            cancelled.append(oid)
        else:
            errors.append(f"{oid}: {r.get('error')}")
    if cancelled:
        logger.info(f"[撤销未成交] {inst_id} {len(cancelled)} 笔 {cancelled}")
    return {"ok": True, "cancelled": cancelled, "errors": errors}


async def query_order(inst_id: str, order_id: str, category: str = "SWAP",
                      sim: bool | None = None) -> dict:
    """反查订单状态。超时/未知错误后用这个确认到底成没成。"""
    if _use_bitget():
        import bitget_trade
        return await bitget_trade.query_order(inst_id, order_id, category, sim=sim)
    if not configured:
        return {"ok": False, "error": "未配置 OKX API 密钥"}
    iid = to_swap(inst_id) if category == "SWAP" else to_spot(inst_id)
    # GET 的 query 要算进签名的 requestPath，_request 传的 path 已含 query
    path = f"{ORDER_INFO}?instId={iid}&ordId={order_id}"
    loop = asyncio.get_event_loop()
    r = await loop.run_in_executor(None, _request, "GET", path, None, sim)
    return {"ok": r.get("code") == "0", "data": _first(r), "error": None if r.get("code") == "0" else _err(r)}


async def get_market_price(inst_id: str, category: str = "SWAP",
                           sim: bool | None = None) -> float | None:
    """当前交易所的实时价格。Bitget 走交易所公共行情，OKX 由调用方直接用本地 ticker。"""
    if _use_bitget():
        import bitget_trade
        return await bitget_trade.get_market_price(inst_id, category, sim=sim)
    return None


async def ping(sim: bool | None = None) -> dict:
    """连通性 + 密钥有效性自检：查账户余额。"""
    if _use_bitget():
        import bitget_trade
        return await bitget_trade.ping(sim=sim)
    if not configured:
        return {"ok": False, "error": "未配置 OKX API 密钥（OKX_API_KEY / SECRET / PASSPHRASE）"}
    loop = asyncio.get_event_loop()
    r = await loop.run_in_executor(None, _request, "GET", BALANCE, None, sim)
    if r.get("code") == "0":
        row = _first(r)
        usdt = next((d for d in row.get("details", []) if d.get("ccy") == "USDT"), {})

        def _num(x) -> float:
            try:
                return float(x) if x not in (None, "") else 0.0
            except (TypeError, ValueError):
                return 0.0

        return {"ok": True, "paper": SIMULATED if sim is None else sim,
                "equity": round(_num(row.get("totalEq")), 4),
                "usdt_avail": round(_num(usdt.get("availBal")), 4),
                "exchange": "okx"}
    return {"ok": False, "paper": SIMULATED if sim is None else sim, "error": _err(r),
            "exchange": "okx"}
