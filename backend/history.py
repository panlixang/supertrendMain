"""
OKX REST 历史 K 线拉取

/api/v5/market/candles 单次最多 300 根，需要更长历史时用 after 游标翻页
（history-candles 端点给更久远的数据，这里按需回退到它）。
"""

import asyncio
import json
import logging
import urllib.request

from state import AppState, Candle, TF_CONFIG

logger = logging.getLogger(__name__)

REST_URLS = ["https://www.okx.com", "https://aws.okx.com"]
PAGE_LIMIT = 300


def _get(url: str, timeout: int = 12):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "supertrend-monitor/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.warning(f"REST 失败 {url.split('?')[0]}: {e}")
        return None


def _rows_to_candles(rows: list) -> list[Candle]:
    out = []
    for row in rows:
        try:
            out.append(Candle(
                ts=int(row[0]), o=float(row[1]), h=float(row[2]),
                l=float(row[3]), c=float(row[4]), vol=float(row[5]),
                confirm=(row[8] == "1") if len(row) > 8 else True,
            ))
        except (IndexError, ValueError):
            continue
    return out


def fetch_candles(tf: str, limit: int = 300, symbol: str = "BTC-USDT") -> list[Candle]:
    """拉最近 limit 根（含正在形成的当前 K），按 ts 升序返回。

    limit > 300 时用 after 游标向更早翻页；最近一页走 /market/candles
    （含未收盘 K），更早的页走 /market/history-candles。
    """
    collected: dict[int, Candle] = {}
    bar = TF_CONFIG[tf]["okx_bar"]
    after = None

    while len(collected) < limit:
        page_n = min(PAGE_LIMIT, limit - len(collected))
        endpoint = "history-candles" if after else "candles"
        qs = f"instId={symbol}&bar={bar}&limit={page_n}"
        if after:
            qs += f"&after={after}"

        data = None
        for base in REST_URLS:
            data = _get(f"{base}/api/v5/market/{endpoint}?{qs}")
            if data and data.get("code") == "0":
                break
        if not data or data.get("code") != "0" or not data.get("data"):
            break

        rows = data["data"]                      # OKX 返回按时间倒序
        page = _rows_to_candles(rows)
        if not page:
            break
        for c in page:
            collected[c.ts] = c
        after = min(c.ts for c in page)          # 下一页取更早的
        if len(page) < page_n:                   # 没有更多历史了
            break

    out = sorted(collected.values(), key=lambda c: c.ts)
    logger.info(f"历史K线[{symbol} {tf}] 拉取 {len(out)} 根")
    return out


async def load_history(state: AppState, symbol: str):
    """按 TF_CONFIG.maxlen 填满各周期 deque。"""
    loop = asyncio.get_event_loop()
    for tf, cfg in TF_CONFIG.items():
        try:
            candles = await loop.run_in_executor(
                None, fetch_candles, tf, cfg["maxlen"], symbol
            )
            deq = state.candles[tf]
            deq.clear()
            deq.extend(candles)
        except Exception as e:
            logger.error(f"历史K线[{symbol} {tf}] 异常: {e}")
        await asyncio.sleep(0.15)   # 轻微限速，避免触发 OKX 频控
