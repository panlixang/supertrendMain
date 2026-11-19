"""
Bitget REST 历史 K 线拉取

参考 Bitget API 文档：
https://www.bitget.com/api-doc/contract/market/Get-Candle-Data
"""

import json
import logging
import time
import urllib.request

from state import Candle, TF_CONFIG

logger = logging.getLogger(__name__)

REST_URL = "https://api.bitget.com"
PAGE_LIMIT = 200  # Bitget 单次最多返回 200 根

# Bitget 时间粒度映射
BITGET_GRANULARITY = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1H",
    "4h": "4H",
    "1d": "1D",
    "1w": "1W",
}


def _get(url: str, timeout: int = 12, retries: int = 3):
    """带退避重试的 REST GET"""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "supertrend-monitor/1.0"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except Exception as e:
            last = e
            logger.warning(
                f"Bitget REST 失败(第{attempt + 1}/{retries}次) {url.split('?')[0]}: {e}"
            )
            if attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))
    return None


def _rows_to_candles(rows: list) -> list[Candle]:
    """Bitget API 返回格式：[timestamp, open, high, low, close, volume, ...]"""
    out = []
    for row in rows:
        try:
            out.append(
                Candle(
                    ts=int(row[0]),
                    o=float(row[1]),
                    h=float(row[2]),
                    l=float(row[3]),
                    c=float(row[4]),
                    vol=float(row[5]) if len(row) > 5 else 0.0,
                    confirm=True,  # REST 返回的都是已收盘的
                )
            )
        except (IndexError, ValueError):
            continue
    return out


def fetch_candles_bitget(
    tf: str, limit: int = 200, symbol: str = "BTC-USDT"
) -> list[Candle]:
    """从 Bitget 拉取历史 K 线，按 ts 升序返回

    参数：
        tf: 时间周期 (1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w)
        limit: 需要的 K 线数量
        symbol: 交易对 (格式: BTC-USDT-SWAP 或 BTC-USDT)
    """
    collected: dict[int, Candle] = {}
    granularity = BITGET_GRANULARITY.get(tf)

    if not granularity:
        logger.error(f"不支持的 Bitget 时间周期: {tf}")
        return []

    # 确保 symbol 格式正确
    if not symbol.endswith("-SWAP"):
        symbol = f"{symbol}-SWAP"

    # Bitget 需要 endTime 参数，从当前时间开始往前拉
    end_time = int(time.time() * 1000)

    while len(collected) < limit:
        page_n = min(PAGE_LIMIT, limit - len(collected))

        # 构建请求 URL
        url = (
            f"{REST_URL}/api/v2/mix/market/candles"
            f"?symbol={symbol}"
            f"&granularity={granularity}"
            f"&limit={page_n}"
            f"&endTime={end_time}"
        )

        data = _get(url)
        if not data or data.get("code") != "00000" or not data.get("data"):
            break

        rows = data["data"]
        if not rows:
            break

        candles = _rows_to_candles(rows)
        for c in candles:
            collected[c.ts] = c

        # 更新 endTime 为最早一根的时间，继续向前拉
        oldest_ts = min(int(row[0]) for row in rows)
        if oldest_ts >= end_time:
            break
        end_time = oldest_ts - 1

        if len(rows) < page_n:
            break

    result = sorted(collected.values(), key=lambda x: x.ts)
    logger.info(f"Bitget 拉取 {symbol} {tf} K线: {len(result)} 根")
    return result
