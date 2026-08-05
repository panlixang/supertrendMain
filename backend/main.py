"""
超级趋势监控台 — 后端入口
启动: uvicorn main:app --port 8000
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from executor import Executor
from feed import OKXFeed
from history import load_history
from router import router
from state import state

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    sym = state.current_symbol
    logger.info(f"拉取历史K线 [{sym}] …")
    await load_history(state, sym)

    feed = OKXFeed(state)
    state.feed = feed
    state.executor = Executor(state)
    feed.rescan_signals()      # 用历史K线先把信号表填好，前端一连上就有内容
    logger.info("历史加载完成，启动实时行情")
    task = asyncio.create_task(feed.run())
    yield
    task.cancel()
    logger.info("行情采集已停止")


app = FastAPI(title="SuperTrend Monitor", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
