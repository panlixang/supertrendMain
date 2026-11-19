"""
OKX 品种列表缓存 + 搜索（SPOT + SWAP，含美股合约）
缓存 6 小时刷新一次。
"""

import asyncio
import json
import logging
import time
import urllib.request

logger = logging.getLogger(__name__)

REST_URLS = ["https://www.okx.com", "https://aws.okx.com"]
CACHE_TTL = 6 * 3600

_cache: list = []
_cache_ts: float = 0.0
_lock = asyncio.Lock()


def _get(url: str, timeout: int = 10):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "supertrend-monitor/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.warning(f"instruments REST 失败: {e}")
        return None


def _fetch_all() -> list[dict]:
    out = []
    for inst_type in ("SPOT", "SWAP"):
        data = None
        for base in REST_URLS:
            data = _get(f"{base}/api/v5/public/instruments?instType={inst_type}")
            if data and data.get("code") == "0":
                break
        if not data or data.get("code") != "0":
            continue
        for r in data.get("data", []):
            if r.get("state") != "live":
                continue
            parts = (r.get("instId") or "").split("-")
            out.append({
                "instId":   r.get("instId"),
                "instType": r.get("instType"),
                "baseCcy":  r.get("baseCcy") or r.get("ctValCcy") or (parts[0] if parts else ""),
                "quoteCcy": r.get("quoteCcy") or r.get("settleCcy") or (parts[1] if len(parts) > 1 else ""),
                "isStock":  r.get("instCategory") == "3",   # OKX 用 '3' 标记美股合约
            })
    logger.info(f"OKX instruments 缓存刷新：{len(out)} 个活跃品种")
    return out


async def _refresh():
    global _cache, _cache_ts
    async with _lock:
        if _cache and time.time() - _cache_ts < CACHE_TTL:
            return
        loop = asyncio.get_event_loop()
        try:
            new = await loop.run_in_executor(None, _fetch_all)
            if new:
                _cache, _cache_ts = new, time.time()
        except Exception as e:
            logger.warning(f"instruments 刷新失败: {e}")


async def search(q: str = "", limit: int = 50) -> list[dict]:
    await _refresh()
    q = (q or "").strip().upper()
    if not q:
        hot = {"BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT", "XRP-USDT",
               "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP",
               "NVDA-USDT-SWAP", "TSLA-USDT-SWAP", "AAPL-USDT-SWAP"}
        hits = [r for r in _cache if r["instId"] in hot]
        hits.sort(key=lambda r: (r["isStock"], len(r["instId"])))
        return hits[:limit]

    hits = [r for r in _cache if q in r["instId"].upper()]
    QUOTE_RANK = {"USDT": 0, "USD": 1, "USDC": 2}

    def rank(r):
        s = r["instId"].upper()
        if s == q:                   pre = 0
        elif s.startswith(q + "-"):  pre = 1
        elif s.startswith(q):        pre = 2
        else:                        pre = 3
        return (pre, QUOTE_RANK.get((r.get("quoteCcy") or "").upper(), 9), len(s))

    hits.sort(key=rank)
    return hits[:limit]


async def is_valid(inst_id: str) -> bool:
    await _refresh()
    return any(r["instId"] == inst_id for r in _cache)
