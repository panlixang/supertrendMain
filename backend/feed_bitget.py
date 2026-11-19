"""
Bitget 实时行情采集（WebSocket）

参考 Bitget WebSocket API：
https://www.bitget.com/api-doc/contract/websocket/public/Candlesticks-Channel
"""

import asyncio
import json
import logging
import time

import websockets
from websockets.exceptions import ConnectionClosed

from state import Candle, TF_CONFIG

logger = logging.getLogger(__name__)

WS_URL = "wss://ws.bitget.com/v2/ws/public"

# Bitget 周期映射
BITGET_CHANNELS = {
    "1m": "candle1m",
    "5m": "candle5m",
    "15m": "candle15m",
    "30m": "candle30m",
    "1h": "candle1H",
    "4h": "candle4H",
    "1d": "candle1D",
    "1w": "candle1W",
}


class BitgetFeed:
    """Bitget WebSocket 行情订阅"""

    def __init__(self, state):
        self.state = state
        self._ws = None
        self._running = False

    def _symbols(self) -> list[str]:
        """需要订阅的品种列表"""
        syms = list(self.state.stores)
        if self.state.current_symbol not in syms:
            syms.append(self.state.current_symbol)
        return syms

    async def run(self):
        """主循环：连接、订阅、接收消息"""
        self._running = True
        while self._running:
            try:
                async with websockets.connect(WS_URL) as ws:
                    self._ws = ws
                    logger.info("Bitget WebSocket 已连接")

                    # 订阅所有品种的所有周期
                    await self._subscribe()

                    # 接收消息循环
                    async for msg in ws:
                        await self._handle_message(msg)

            except ConnectionClosed:
                logger.warning("Bitget WebSocket 连接断开，3秒后重连")
                await asyncio.sleep(3)
            except Exception as e:
                logger.error(f"Bitget WebSocket 错误: {e}")
                await asyncio.sleep(3)

    async def _subscribe(self):
        """订阅所有品种和周期"""
        args = []
        for symbol in self._symbols():
            # 确保格式为 BTCUSDT (Bitget WS 格式)
            inst_id = symbol.replace("-", "").replace("SWAP", "")
            for tf in TF_CONFIG.keys():
                channel = BITGET_CHANNELS.get(tf)
                if channel:
                    args.append({
                        "instType": "USDT-FUTURES",
                        "channel": channel,
                        "instId": inst_id
                    })

        if args:
            msg = {
                "op": "subscribe",
                "args": args
            }
            await self._ws.send(json.dumps(msg))
            logger.info(f"Bitget 已订阅 {len(args)} 个频道")

    async def _handle_message(self, msg: str):
        """处理 WebSocket 消息"""
        try:
            data = json.loads(msg)

            # 心跳响应
            if data.get("event") == "ping":
                await self._ws.send(json.dumps({"op": "pong"}))
                return

            # 订阅确认
            if data.get("event") == "subscribe":
                logger.info(f"Bitget 订阅成功: {data.get('arg', {}).get('channel')}")
                return

            # K线数据
            if data.get("action") == "update" and "data" in data:
                await self._handle_candle_data(data)

        except Exception as e:
            logger.error(f"处理 Bitget 消息失败: {e}")

    async def _handle_candle_data(self, data: dict):
        """处理 K 线数据推送

        格式: {
            "action": "update",
            "arg": {"instType": "USDT-FUTURES", "channel": "candle1H", "instId": "BTCUSDT"},
            "data": [[timestamp, open, high, low, close, volume, ...]]
        }
        """
        try:
            arg = data.get("arg", {})
            channel = arg.get("channel", "")
            inst_id = arg.get("instId", "")

            # 解析周期
            tf = None
            for k, v in BITGET_CHANNELS.items():
                if v == channel:
                    tf = k
                    break

            if not tf:
                return

            # 解析品种（BTCUSDT -> BTC-USDT）
            symbol = inst_id[:3] + "-" + inst_id[3:]
            if not symbol.endswith("-SWAP"):
                symbol += "-SWAP"

            store = self.state.stores.get(symbol)
            if not store:
                return

            # 解析 K 线数据
            rows = data.get("data", [])
            for row in rows:
                candle = Candle(
                    ts=int(row[0]),
                    o=float(row[1]),
                    h=float(row[2]),
                    l=float(row[3]),
                    c=float(row[4]),
                    vol=float(row[5]) if len(row) > 5 else 0.0,
                    confirm=True  # Bitget 推送的是已收盘 K 线
                )

                # 添加到对应品种的 deque
                deq = store.candles[tf]
                if deq and (not deq or deq[-1].ts < candle.ts):
                    deq.append(candle)
                    logger.debug(f"Bitget K线更新: {symbol} {tf} @ {candle.c}")

                    # TODO: 触发信号检测逻辑
                    # 这里需要调用 SuperTrend 计算和信号判断

        except Exception as e:
            logger.error(f"处理 Bitget K线数据失败: {e}")

    def stop(self):
        """停止 WebSocket"""
        self._running = False
        if self._ws:
            asyncio.create_task(self._ws.close())
