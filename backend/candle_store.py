"""
本地 K 线落盘（SQLite）

用途：
  - 启动时先读本地，缺口再用 OKX REST 补，缩短冷启动翻页
  - 收盘 K 持续写入，7×24 跑越久本地历史越完整
  - 按品种+周期裁剪，不超过 TF_CONFIG.maxlen
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from typing import Iterable

from state import Candle, TF_CONFIG

logger = logging.getLogger(__name__)

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "candle_data.db")
_lock = threading.Lock()
_PERSIST_TFS = frozenset({"15m", "1h", "4h", "1d"})  # 只持久化长展示窗口周期


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB_PATH, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


def init_db():
    with _lock:
        con = _conn()
        try:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS candles (
                    symbol TEXT NOT NULL,
                    tf     TEXT NOT NULL,
                    ts     INTEGER NOT NULL,
                    o REAL NOT NULL,
                    h REAL NOT NULL,
                    l REAL NOT NULL,
                    c REAL NOT NULL,
                    vol REAL NOT NULL,
                    PRIMARY KEY (symbol, tf, ts)
                )
                """
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_candles_sym_tf_ts "
                "ON candles(symbol, tf, ts)"
            )
            con.commit()
        finally:
            con.close()


def should_persist(tf: str) -> bool:
    return tf in _PERSIST_TFS


def load_candles(symbol: str, tf: str, limit: int | None = None) -> list[Candle]:
    """按时间升序返回本地 K 线。"""
    if not should_persist(tf):
        return []
    lim = limit or TF_CONFIG.get(tf, {}).get("maxlen", 1000)
    with _lock:
        con = _conn()
        try:
            rows = con.execute(
                """
                SELECT ts, o, h, l, c, vol FROM candles
                WHERE symbol=? AND tf=?
                ORDER BY ts DESC
                LIMIT ?
                """,
                (symbol.upper(), tf, lim),
            ).fetchall()
        finally:
            con.close()
    out = [
        Candle(ts=int(r[0]), o=float(r[1]), h=float(r[2]), l=float(r[3]),
               c=float(r[4]), vol=float(r[5]), confirm=True)
        for r in reversed(rows)
    ]
    return out


def save_candles(symbol: str, tf: str, candles: Iterable[Candle],
                 prune: bool = True):
    """批量 upsert 已收盘 K；可选按 maxlen 裁剪旧数据。"""
    if not should_persist(tf):
        return
    rows = []
    for c in candles:
        if not getattr(c, "confirm", True):
            continue
        rows.append((
            symbol.upper(), tf, int(c.ts),
            float(c.o), float(c.h), float(c.l), float(c.c), float(c.vol),
        ))
    if not rows:
        return
    maxlen = TF_CONFIG.get(tf, {}).get("maxlen", 1000)
    with _lock:
        con = _conn()
        try:
            con.executemany(
                """
                INSERT INTO candles(symbol, tf, ts, o, h, l, c, vol)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, tf, ts) DO UPDATE SET
                    o=excluded.o, h=excluded.h, l=excluded.l,
                    c=excluded.c, vol=excluded.vol
                """,
                rows,
            )
            if prune:
                con.execute(
                    """
                    DELETE FROM candles
                    WHERE symbol=? AND tf=? AND ts NOT IN (
                        SELECT ts FROM candles
                        WHERE symbol=? AND tf=?
                        ORDER BY ts DESC
                        LIMIT ?
                    )
                    """,
                    (symbol.upper(), tf, symbol.upper(), tf, maxlen),
                )
            con.commit()
        finally:
            con.close()


def save_one(symbol: str, tf: str, candle: Candle):
    if candle.confirm:
        save_candles(symbol, tf, [candle], prune=True)


init_db()
