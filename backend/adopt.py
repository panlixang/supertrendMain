# -*- coding: utf-8 -*-
"""启动自动接管：交易所有真实持仓但本地状态机为空时，重建 Position 接管管理。

背景：持仓只存内存，重启会丢。若重启时交易所还有旧仓（未成交单事后成交、
历史遗留仓、或上次进程被杀），本地没有对应状态机 → 无人止损/止盈的裸仓。

方案：启动清理阶段查交易所持仓，对每个「本地为空 + 交易所非空」的品种，
用交易所均价/数量重建增强持仓，并按当前品种出场规则算出初始止损。
此后 on_price 的秒级止损检查、on_st_line 的移动止损、反向信号平仓全部生效。
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


def parse_pos_row(row: dict) -> dict | None:
    """把交易所持仓行解析为统一 dict，兼容 OKX / Bitget 两种字段。

    OKX:    pos（正=多/负=空）、avgPx、lever、ctVal
    Bitget: pos（归一化为绝对值）、holdSide（long/short）、
            averageOpenPrice、leverage（无 ctVal，仓位单位是币数）
    """
    if not row:
        return None
    try:
        raw = float(row.get("pos") or 0)
    except (TypeError, ValueError):
        return None
    if raw == 0:
        return None
    side = "long" if raw > 0 else "short"
    hold_side = row.get("holdSide")
    if hold_side:
        side = "long" if str(hold_side).strip().lower() == "long" else "short"
    qty = abs(raw)
    try:
        avg = float(row.get("avgPx") or row.get("averageOpenPrice") or 0)
    except (TypeError, ValueError):
        avg = 0.0
    try:
        leverage = int(float(row.get("lever") or row.get("leverage") or 0))
    except (TypeError, ValueError):
        leverage = 0
    try:
        ct_val = float(row.get("ctVal") or 1)
    except (TypeError, ValueError):
        ct_val = 1.0
    if qty <= 0 or avg <= 0:
        return None
    return {"side": side, "qty": qty, "avg": avg, "leverage": leverage, "ct_val": ct_val}


def main_tf(allow_tfs: list | None) -> str:
    """接管仓的监控周期：优先 1h（移动止损跟随 / 反向平仓都认这个周期），
    不在允许列表时退回列表第一个。"""
    tfs = list(allow_tfs or [])
    return "1h" if "1h" in tfs else (tfs[0] if tfs else "1h")


async def adopt_exchange_position(state, ex, store, row: dict) -> bool:
    """接管一笔交易所真实持仓，重建本地状态机。

    - 本地已有持仓（qty>0）时视为无需接管，返回 True（不重复建仓）。
    - 返回 False 表示该行无法解析或无法接管（调用方另行告警）。
    """
    if not ex or not store:
        return False
    if store.position and store.position.qty > 0:
        return True
    p = parse_pos_row(row)
    if not p:
        return False

    from position_enhanced import EnhancedPosition, EnhancedExitRules, enhanced_initial_stop

    cfg = store.cfg
    leverage = p["leverage"] or getattr(cfg, "leverage", 3) or 3
    tf = main_tf(list(getattr(cfg, "allow_tfs", None) or []))

    rules = state.rules_for("normal", store.symbol)
    if not hasattr(rules, "sl_buffer_atr"):
        from dataclasses import fields
        rules = EnhancedExitRules(**{f.name: getattr(rules, f.name) for f in fields(rules)})

    atr = None
    try:
        atr = ex._calc_atr(tf)
    except Exception:
        atr = None

    sig = {"price": p["avg"], "type": "buy" if p["side"] == "long" else "sell", "line": None}
    try:
        stop = enhanced_initial_stop(sig, rules, atr, leverage)
    except Exception:
        # 兜底：价格止损退化为固定 5% 距离，至少保证有止损线
        stop = p["avg"] * (0.95 if p["side"] == "long" else 1.05)

    store.position = EnhancedPosition(
        symbol=store.symbol, side=p["side"], tf=tf, entry=p["avg"],
        qty=p["qty"], init_qty=p["qty"], stop=stop, leverage=leverage,
        entry_ts=int(time.time() * 1000), order_id="", ct_val=p["ct_val"],
        profile="normal",
    )
    store.position.log(
        "adopt",
        f"启动接管交易所持仓 {p['side']} {p['qty']} @ {p['avg']}（止损 {stop}，{tf} 周期）",
    )
    try:
        await ex._push_position()
    except Exception:
        pass
    logger.warning(
        f"[启动接管] {store.symbol} 接管交易所持仓 {p['side']} {p['qty']} @ {p['avg']} "
        f"杠杆{leverage}x 止损 {stop}（tf={tf}，ATR={atr if atr else 'N/A'}）"
    )
    return True
