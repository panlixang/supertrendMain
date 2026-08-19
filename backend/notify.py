"""
信号推送 — Server酱（推到微信）

环境变量：
  SERVERCHAN_SENDKEY   SendKey，不填则整体禁用（不报错）
  SERVERCHAN_URL       可选地址模板，需含 {key}
                       默认 https://sctapi.ftqq.com/{key}.send（Server酱·Turbo）
  NOTIFY_MIN_INTERVAL  两条推送最小间隔秒数，默认 2
  NOTIFY_MIN_GRADE     只推 >= 该等级的信号（A / B / C），默认 B
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

SENDKEY      = os.environ.get("SERVERCHAN_SENDKEY", "").strip()
URL_TMPL     = os.environ.get("SERVERCHAN_URL", "https://sctapi.ftqq.com/{key}.send")
MIN_INTERVAL = float(os.environ.get("NOTIFY_MIN_INTERVAL", "2"))
MIN_GRADE    = os.environ.get("NOTIFY_MIN_GRADE", "B").upper()

enabled = bool(SENDKEY)
GRADE_RANK = {"A": 3, "B": 2, "C": 1}

_pushed: set = set()
_lock = asyncio.Lock()
_last_push = 0.0

logger.info("信号推送" + ("已启用（Server酱 → 微信）" if enabled else "未启用（未设 SERVERCHAN_SENDKEY）"))


def _send_blocking(title: str, desp: str) -> bool:
    url = URL_TMPL.format(key=SENDKEY)
    data = urllib.parse.urlencode({"title": title[:32], "desp": desp}).encode()
    try:
        req = urllib.request.Request(url, data=data, headers={"User-Agent": "supertrend-monitor/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", "ignore")
        if '"code":0' in body or '"code": 0' in body:
            return True
        logger.warning(f"Server酱返回异常: {body[:200]}")
        return False
    except Exception as e:
        logger.warning(f"推送失败: {e}")
        return False


async def push(title: str, desp: str, dedup_key: str | None = None) -> bool:
    """去重 + 限速后发送。dedup_key 相同的只推一次。"""
    if not enabled:
        return False
    global _last_push
    async with _lock:
        if dedup_key:
            if dedup_key in _pushed:
                return False
            _pushed.add(dedup_key)
            if len(_pushed) > 2000:
                _pushed.clear()
        wait = MIN_INTERVAL - (time.time() - _last_push)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_push = time.time()

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _send_blocking, title, desp)


async def push_signal(symbol: str, sig: dict, order: dict | None = None) -> bool:
    """推送买卖信号。

    NOTIFY_MIN_GRADE 只过滤「等级」，不过滤行情状态 —— 震荡市的假信号照样推给你，
    因为你要的是「提醒我但别下单」，标题上会写清楚是挂单还是仅提醒。
    """
    grade = sig.get("grade", "C")
    if GRADE_RANK.get(grade, 0) < GRADE_RANK.get(MIN_GRADE, 2):
        return False

    is_buy = sig["type"] == "buy"
    icon = "🟢 BUY" if is_buy else "🔴 SELL"
    reg = sig.get("regime") or {}
    traded = bool(order and order.get("ok"))

    prof = sig.get("profile")
    prof_label = {"quick": "快进快出档", "normal": "标准档"}.get(prof)

    if traded:
        tag = "已成交" + ("(模拟)" if order.get("paper") else "(实盘)")
    elif order and not order.get("ok"):
        err = str(order.get("error") or "")
        tag = "未成交已撤" if "未成交" in err else "挂单失败"
    else:
        tag = "仅提醒"

    title = f"{icon} {symbol} {sig['tf']} [{grade}] {tag}"
    ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(sig["ts"] / 1000))
    line = sig.get("line")

    rows = [
        f"## {icon} 超级趋势翻转 · {tag}",
        "",
        f"- 品种：**{symbol}**　周期：**{sig['tf']}**",
        f"- 触发价：**{sig['price']}**",
    ]
    if line is not None:
        rows.append(f"- 跟踪止损（超趋线）：**{line}**")
    rows += [
        f"- 信号等级：**{grade}**（强度 {sig.get('score', 0)}/3）",
        f"- MTF Bias：**{sig.get('bias_label', '—')}**"
        f"（多 {sig.get('bulls', '?')} / 空 {sig.get('bears', '?')}）",
        f"- 行情状态：**{reg.get('label', '—')}**（ER {reg.get('er', '—')}）",
        f"- 时间：{ts}",
        "",
    ]

    if traded:
        rows += [
            "### ✅ 已成交",
            f"- 成交价：**{order.get('price')}**　数量：**{order.get('qty')}**",
            f"- 金额：约 **{order.get('amount')} USDT**",
            f"- 订单号：`{order.get('orderId')}`",
            f"- 环境：**{'模拟盘' if order.get('paper') else '⚠️ 实盘'}**",
        ]
        if prof_label:
            # 两档的出场规则完全不同，推送里要说清这单按哪套跑
            rows.append(f"- 出场档位：**{prof_label}**")
    elif order and not order.get("ok"):
        err = str(order.get("error") or "")
        if "未成交" in err:
            rows += ["### ⏸ 未成交已撤", f"- 原因：{err}", "- 价格离开盘口，没有记成持仓，不会止损"]
        else:
            rows += ["### ❌ 挂单失败", f"- 原因：{err}", "- 信号本身有效，请手动处理"]
    else:
        reasons = sig.get("gate_reasons") or []
        rows += ["### ⏸ 未挂单，原因："] + [f"- {r}" for r in reasons[:4]]
        if reg.get("regime") == "range":
            rows.append("")
            rows.append("> 实测：震荡行情下信号亏损率约 80%，已按你的设置跳过挂单。")
        elif reg.get("regime") == "edge":
            rows.append("")
            rows.append("> 这条落在弱档区间，勾选「弱档自动下单」后这类信号就会挂单。")

    return await push(title, "\n".join(rows),
                      dedup_key=f"{symbol}:{sig['tf']}:{sig['ts']}:{sig['type']}")
