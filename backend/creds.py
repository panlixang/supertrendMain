"""逐 asyncio Task 隔离的「显式凭据」。

背景
----
trade.py / bitget_trade.py 的 API Key 是**模块级全局**，两个页面（首页交易、
形态识别页交易）各填一套会互相覆盖。这里用 contextvars 做「上下文内覆盖」：

- 在某个 Task 里 set 之后，该 Task 及其子协程发起的所有下单 / 查询都用
  它自己的 key、自己的交易所、自己的模拟盘开关；
- 其它 Task 完全不受影响 —— contextvars 在 Task 创建时被复制，天然隔离，
  不需要加锁，也不会像"临时改全局"那样在并发下串号。

未设置 override 时，行为与改造前完全一致（沿用模块全局），首页链路零影响。

用法（后台任务开头设一次，终身有效）：:

    creds.bind(creds.Creds(exchange="bitget", api_key=..., ..., label="pattern"))
    await trade.place_order(...)

临时借用别的凭据::

    with creds.use(some_creds):
        await trade.get_positions("BTC-USDT")
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional


@dataclass
class Creds:
    """一份完整的交易所凭据。"""

    exchange:   str          = "okx"      # okx / bitget
    api_key:    str          = ""
    api_secret: str          = ""
    passphrase: str          = ""
    paper:      Optional[bool] = None     # None = 沿用模块的 SIMULATED
    label:      str          = ""         # 日志用，区分来源（home / pattern）

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret and self.passphrase)

    @property
    def masked_key(self) -> str:
        s = (self.api_key or "").strip()
        if not s:
            return ""
        if len(s) <= 8:
            return "••••"
        return f"{s[:4]}••••{s[-4:]}"


_CREDENTIALS: contextvars.ContextVar[Optional[Creds]] = contextvars.ContextVar(
    "trade_credentials", default=None
)


def current() -> Optional[Creds]:
    """当前 Task 设置的显式凭据；没设就是 None（走模块全局）。"""
    return _CREDENTIALS.get()


def bind(c: Optional[Creds]) -> None:
    """给当前 Task 绑定凭据（不 reset，适合后台常驻任务设一次用到底）。"""
    _CREDENTIALS.set(c)


@contextmanager
def use(c: Optional[Creds]) -> Iterator[Optional[Creds]]:
    """临时切换凭据（退出自动恢复）。"""
    token = _CREDENTIALS.set(c)
    try:
        yield c
    finally:
        _CREDENTIALS.reset(token)


def configured_now(module_configured: bool) -> bool:
    """替代模块内零散的 `if not configured:`。

    有显式凭据就看它本身完整不完整；没有才回落到模块全局。
    """
    c = current()
    return module_configured if c is None else c.configured


def key_secret_pass(fallback: tuple) -> tuple:
    """(api_key, api_secret, passphrase)，优先取显式凭据。"""
    c = current()
    if c is None:
        return tuple(fallback)
    return (c.api_key, c.api_secret, c.passphrase)


def use_bitget(fallback_exchange: str) -> bool:
    """当前上下文是否该走 Bitget 通道。"""
    c = current()
    ex = (c.exchange if c is not None else fallback_exchange) or "okx"
    return ex.strip().lower() == "bitget"


def resolve_sim(sim: Optional[bool], fallback_sim: bool) -> bool:
    """模拟盘判定优先级：显式 sim 参数 > 凭据的 paper > 模块全局 SIMULATED。"""
    if sim is not None:
        return sim
    c = current()
    if c is not None and c.paper is not None:
        return c.paper
    return fallback_sim
