"""成交 / 平仓账本落盘。

背景：state.orders 只在内存里保留最近 200 条、store.closed 每品种 50 条，
服务一重启账本就断，各品种的真实盈亏没法统计（也没法回答「MU 到底行不行」）。

这里把每笔订单、每笔结束的仓位追加写入 backend/logs/ 下按月滚动的 jsonl：
    logs/orders_YYYYMM.jsonl   每笔下单请求（含失败单和失败原因）
    logs/closed_YYYYMM.jsonl   每笔结束的仓位（方向、开平价、已实现盈亏）

写入失败只警告不中断 —— 记账永远不该影响下单。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")

ORDERS_PREFIX = "orders_"
CLOSED_PREFIX = "closed_"

_lock = threading.Lock()


def _month_tag(ts_ms) -> str:
    """按记录时间（毫秒）归档到对应月份文件。"""
    try:
        ts = float(ts_ms or 0)
        if ts > 1e11:                      # 毫秒
            ts /= 1000.0
        dt = datetime.fromtimestamp(ts, timezone.utc) if ts > 0 else datetime.now(timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        dt = datetime.now(timezone.utc)
    return dt.strftime("%Y%m")


def _append(prefix: str, row: dict) -> None:
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        rec = {"logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        rec.update(row or {})
        tag = _month_tag(rec.get("ts") or rec.get("entry_ts"))
        path = os.path.join(LOG_DIR, f"{prefix}{tag}.jsonl")
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with _lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
    except Exception as e:
        logger.warning(f"成交日志写入失败: {e}")


def log_order(order: dict) -> None:
    """每笔下单结果（成功 / 失败都记），供事后查「为什么没成交」。"""
    _append(ORDERS_PREFIX, order)


def log_closed(row: dict) -> None:
    """一笔仓位结束时的快照，realized 是这笔的已实现盈亏（USDT）。"""
    _append(CLOSED_PREFIX, row)


def _recent_files(prefix: str, months: int) -> list[str]:
    if not os.path.isdir(LOG_DIR):
        return []
    names = sorted(n for n in os.listdir(LOG_DIR)
                   if n.startswith(prefix) and n.endswith(".jsonl"))
    if months and months > 0:
        names = names[-months:]
    return [os.path.join(LOG_DIR, n) for n in names]


def read_rows(prefix: str, months: int = 3, sym: str | None = None) -> list[dict]:
    want = (sym or "").upper()
    rows: list[dict] = []
    for path in _recent_files(prefix, months):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if want and (r.get("sym") or "").upper() != want:
                        continue
                    rows.append(r)
        except FileNotFoundError:
            continue
    return rows


def read_closed(months: int = 3, sym: str | None = None) -> list[dict]:
    return read_rows(CLOSED_PREFIX, months, sym)


def read_orders(months: int = 1, sym: str | None = None) -> list[dict]:
    return read_rows(ORDERS_PREFIX, months, sym)


def _pnl(r: dict) -> float:
    try:
        return float(r.get("realized") or 0)
    except (TypeError, ValueError):
        return 0.0


def summarize(rows: list[dict]) -> dict:
    """按品种汇总：笔数、已实现盈亏、胜 / 负、最大单笔盈 / 亏。"""
    out: dict[str, dict] = {}
    for r in rows:
        k = r.get("sym") or "?"
        s = out.setdefault(k, {"trades": 0, "win": 0, "loss": 0, "flat": 0,
                               "realized": 0.0, "best": None, "worst": None})
        pnl = _pnl(r)
        s["trades"] += 1
        s["realized"] = round(s["realized"] + pnl, 4)
        if pnl > 0:
            s["win"] += 1
        elif pnl < 0:
            s["loss"] += 1
        else:
            s["flat"] += 1
        s["best"] = pnl if s["best"] is None else max(s["best"], round(pnl, 4))
        s["worst"] = pnl if s["worst"] is None else min(s["worst"], round(pnl, 4))
    return out


def summary(months: int = 3, sym: str | None = None, recent: int = 30) -> dict:
    """给面板 / 排查用的总账。months=0 表示全部历史。"""
    rows = read_closed(months, sym)
    agg = summarize(rows)
    return {
        "months": months,
        "log_dir": LOG_DIR,
        "files": [os.path.basename(p) for p in _recent_files(CLOSED_PREFIX, months)],
        "total_trades": len(rows),
        "total_realized": round(sum(_pnl(r) for r in rows), 4),
        "by_symbol": agg,
        "recent": rows[-recent:],
    }
