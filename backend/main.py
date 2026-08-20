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
from integration import create_enhanced_executor
import trade

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
        # 使用增强版执行器（支持三级止盈、智能止损、盈利保护）
        state.executors[sym] = create_enhanced_executor(state, st)
    # 重启会丢掉内存持仓，但交易所上的旧限价单还在。不撤的话事后成交就变成没人管的仓。
    if trade.configured:
        for sym in list(state.stores):
            try:
                r = await trade.cancel_pending(
                    sym, state.trade_cfg.category, sim=state.trade_cfg.paper)
                n = len(r.get("cancelled") or [])
                if n:
                    logger.warning(f"启动清理：撤销 {sym} 未成交挂单 {n} 笔 {r.get('cancelled')}")
                elif not r.get("ok"):
                    logger.warning(f"启动清理挂单失败 [{sym}]: {r.get('error')}")
                posr = await trade.get_positions(sym, state.trade_cfg.category, sim=state.trade_cfg.paper)
                if posr.get("ok"):
                    for row in posr.get("data") or []:
                        try:
                            q = abs(float(row.get("pos") or 0))
                        except (TypeError, ValueError):
                            q = 0
                        if q > 0:
                            logger.warning(
                                f"启动时交易所仍有仓 {row.get('instId')} "
                                f"pos={row.get('pos')} avgPx={row.get('avgPx')} "
                                f"— 本地持仓已空，请到 OKX 手动处理或点对账"
                            )
            except Exception as e:
                logger.warning(f"启动清理挂单失败 [{sym}]: {e}")
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
