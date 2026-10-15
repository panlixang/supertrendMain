"""
历史 K 线拉取 + 本地合并

支持 OKX 和 Bitget 两个数据源，根据品种配置的 data_source 字段自动选择。

长展示窗口（15m/1h/4h/1d）：启动时本地 SQLite ∪ REST，收盘持续落盘。
"""

import asyncio
import json
import logging
import time
import urllib.request

import candle_store
from state import Candle, SymbolStore, TF_CONFIG
from history_bitget import fetch_candles_bitget

logger = logging.getLogger(__name__)

REST_URLS = ["https://www.okx.com", "https://aws.okx.com"]
PAGE_LIMIT = 300


def _get(url: str, timeout: int = 12, retries: int = 3):
    """带退避重试的 REST GET；瞬时错误（SSL 握手超时 / EOF）会重试，全部失败才返回 None。"""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "supertrend-monitor/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except Exception as e:
            last = e
            logger.warning(f"REST 失败(第{attempt + 1}/{retries}次) {url.split('?')[0]}: {e}")
            if attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))
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
    pages = 0

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
        pages += 1
        if len(page) < page_n:                   # 没有更多历史了
            break
        # 大窗口翻页限速，避免 OKX 频控
        if pages % 5 == 0:
            time.sleep(0.12)

    out = sorted(collected.values(), key=lambda c: c.ts)
    logger.info(f"历史K线[{symbol} {tf}] 拉取 {len(out)} 根（{pages} 页）")
    return out


def _merge(local: list[Candle], remote: list[Candle], limit: int) -> list[Candle]:
    by_ts: dict[int, Candle] = {}
    for c in local:
        by_ts[c.ts] = c
    for c in remote:
        # 远端覆盖同 ts（含最新未收盘）
        by_ts[c.ts] = c
    out = sorted(by_ts.values(), key=lambda c: c.ts)
    if len(out) > limit:
        out = out[-limit:]
    return out


async def load_history(store: SymbolStore):
    """按 TF_CONFIG.maxlen 填满该品种各周期 deque；长周期优先本地再补 REST。

    数据源选择逻辑（优先级从高到低）：
    1. 品种配置的 data_source 字段（显式指定）
    2. 全局交易所配置（自动匹配交易所）
    3. 默认 OKX
    """
    loop = asyncio.get_event_loop()
    symbol = store.symbol

    # 获取数据源：优先品种配置，否则根据交易所自动选择
    data_source = getattr(store.cfg, "data_source", None)

    if not data_source:
        # 从全局交易所配置自动推断数据源
        try:
            import creds
            c = creds.get()
            if c and c.exchange:
                data_source = c.exchange.lower()
            else:
                # 如果没有显式凭据，尝试从 trade 模块获取
                import trade
                data_source = getattr(trade, "exchange", "okx").lower()
        except Exception:
            data_source = "okx"

    data_source = (data_source or "okx").lower()
    fetch_func = fetch_candles_bitget if data_source == "bitget" else fetch_candles

    logger.info(f"品种 {symbol} 使用数据源: {data_source.upper()}")

    for tf, cfg in TF_CONFIG.items():
        try:
            limit = cfg["maxlen"]
            local: list[Candle] = []
            if candle_store.should_persist(tf):
                local = await loop.run_in_executor(
                    None, candle_store.load_candles, symbol, tf, limit
                )

            need_fetch = limit
            if local:
                # 本地已有大半时，只补最近一段 + 少量重叠，减少翻页
                need_fetch = min(limit, max(300, limit - len(local) + 400))

            remote = await loop.run_in_executor(
                None, fetch_func, tf, need_fetch, symbol
            )
            merged = _merge(local, remote, limit)

            # 本地仍不够展示窗口：再向前翻满
            if len(merged) < limit * 0.9 and len(remote) >= need_fetch:
                more = await loop.run_in_executor(
                    None, fetch_func, tf, limit, symbol
                )
                merged = _merge(merged, more, limit)

            deq = store.candles[tf]
            deq.clear()
            deq.extend(merged)

            if candle_store.should_persist(tf):
                # 只落已收盘，避免把未完成 K 写死
                closed = [c for c in merged if c.confirm]
                await loop.run_in_executor(
                    None, candle_store.save_candles, symbol, tf, closed, True
                )
            logger.info(
                f"历史就绪[{symbol} {tf}] 内存 {len(merged)}/{limit}"
                f"（本地 {len(local)} + REST {len(remote)}）[{data_source.upper()}]"
            )
        except Exception as e:
            logger.error(f"历史K线[{symbol} {tf}] 异常: {e}")
        await asyncio.sleep(0.15)   # 轻微限速，避免触发频控
    store.history_loaded = True
