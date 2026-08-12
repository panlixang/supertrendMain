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
    # 图表品种优先加载，其余交易品种随后 —— 全部就绪后才起实时行情，
    # 保证每个品种的信号判定一开始就有完整历史
    order = [state.current_symbol] + [s for s in state.stores if s != state.current_symbol]
    for sym in order:
        store = state.stores.get(sym) or state.view
        logger.info(f"拉取历史K线 [{sym}] …")
        await load_history(store)

    feed = OKXFeed(state)
    state.feed = feed
    for sym, st in state.stores.items():
        state.executors[sym] = Executor(state, st)
    feed.rescan_signals()      # 用历史K线先把信号表填好，前端一连上就有内容
    logger.info(f"历史加载完成（{len(order)} 个品种），启动实时行情")
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
