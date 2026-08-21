"""多品种资金路径离线验证（不触网、不动真单）。

桩掉 trade.place_order / get_spec / set_leverage，驱动两个品种并发验证：
  1. 双品种各自开仓，互不覆盖
  2. 同品种 1m 噪声反向信号（未过闸门）不平自己的 15m 仓
  2b. 异周期反向即使过闸门也不平 15m 仓、不开反向
  2c. 串到 A 执行器的 BBB 信号被丢弃
  3. on_price 各吃各的 ticker：A 止盈+保本时 B 纹丝不动
  4. B 独立触发止损全平、落 closed 历史
  5. 同品种同周期反向信号平掉剩余仓位
用法: .venv/bin/python test_multi.py
"""
import asyncio
import sys

import trade

# ── 桩 ──────────────────────────────────────────────────────────
CALLS = []


async def fake_place_order(symbol, side, price, sz=None, margin_usdt=None,
                           leverage=1, category="SWAP", order_type="limit",
                           reduce_only=False, pos_side=None, mgn_mode="cross",
                           client_oid=None, sim=None, ref_price=None, **_):
    px = price or ref_price
    qty = sz if sz is not None else round(margin_usdt * max(1, leverage) / px / 0.01, 2)
    CALLS.append((symbol, side, "reduce" if reduce_only else "open", qty, px))
    iid = symbol if symbol.endswith("-SWAP") else f"{symbol}-SWAP"
    return {"ok": True, "orderId": f"o{len(CALLS)}", "symbol": iid, "side": side,
            "price": px, "qty": qty, "coin_qty": qty * 0.01, "notional": qty * 0.01 * px,
            "margin": 10, "leverage": leverage, "reduce_only": reduce_only,
            "category": category, "ct_val": 0.01, "paper": True, "ts": 1,
            "fill_confirmed": True}


async def fake_get_spec(iid, t="SWAP"):
    return {"tick_sz": 0.01, "lot_sz": 0.01, "min_sz": 0.01, "ct_val": 0.01,
            "ct_mult": 1, "ct_ccy": "X", "max_lev": 100, "state": "live", "inst_type": t}


async def fake_set_lev(*a, **k):
    return {"ok": True}


async def fake_cancel_pending(*a, **k):
    return {"ok": True, "cancelled": [], "errors": []}


trade.place_order = fake_place_order
trade.get_spec = fake_get_spec
trade.set_leverage = fake_set_lev
trade.cancel_pending = fake_cancel_pending
trade.configured = True

from state import SymbolStore, SymbolTradeConfig, state  # noqa: E402
from executor import Executor  # noqa: E402

state.save_settings = lambda: None          # 测试不落盘
state.stores.clear()
state.executors.clear()
for sym in ("AAA-USDT", "BBB-USDT"):
    st = SymbolStore(sym, cfg=SymbolTradeConfig(
        symbol=sym, enabled=True, margin_usdt=10, leverage=3, allow_tfs=["15m"]))
    state.stores[sym] = st
    state.executors[sym] = Executor(state, st)
state.trade_cfg.enabled = True
state.trade_cfg.paper = True

GATE_OK = {"trade": True, "profile": "normal", "regime": {}, "reasons": []}
GATE_NO = {"trade": False, "profile": None, "regime": {}, "reasons": ["周期不允许"]}


def ok(name, cond):
    print(("✅" if cond else "❌"), name)
    if not cond:
        sys.exit(1)


async def main():
    A, B = state.stores["AAA-USDT"], state.stores["BBB-USDT"]
    exA, exB = state.executors["AAA-USDT"], state.executors["BBB-USDT"]
    A.ticker.last, B.ticker.last = 100.0, 50.0

    # 0. 非允许周期即使闸门放行也不开仓（防动量捷径）
    n0 = len(CALLS)
    await exA.on_signal({"type": "buy", "tf": "1m", "ts": 0, "price": 100.0, "line": 95.0,
                         "grade": "A", "score": 3, "symbol": "AAA-USDT"}, GATE_OK)
    ok("1m 不在 allow_tfs，GATE_OK 也不开仓", A.position is None)
    ok("1m 没有产生下单", len(CALLS) == n0)

    # 1. 双品种各自开仓
    await exA.on_signal({"type": "buy", "tf": "15m", "ts": 1, "price": 100.0, "line": 95.0,
                         "grade": "A", "score": 3}, GATE_OK)
    await exB.on_signal({"type": "sell", "tf": "15m", "ts": 1, "price": 50.0, "line": 52.0,
                         "grade": "A", "score": 3}, GATE_OK)
    ok("A 开多 / B 开空，互不覆盖",
       A.position and A.position.side == "long"
       and B.position and B.position.side == "short")
    ok("持仓符号为各自合约形式",
       A.position.symbol == "AAA-USDT-SWAP" and B.position.symbol == "BBB-USDT-SWAP")

    # 2. 同品种 1m 噪声反向信号（未过闸门、周期不同）不平仓
    await exA.on_signal({"type": "sell", "tf": "1m", "ts": 2, "price": 101.0}, GATE_NO)
    ok("A 的 1m 噪声翻转没有平掉 15m 仓位", A.position and A.position.qty > 0)

    # 2b. 异周期反向即使过闸门也不平仓、不开反向仓（1h 不能动 15m 仓）
    n_calls = len(CALLS)
    await exA.on_signal({"type": "sell", "tf": "1h", "ts": 21, "price": 99.0, "line": 102.0,
                         "grade": "A", "score": 3, "symbol": "AAA-USDT"}, GATE_OK)
    ok("A 的 1h 反向过闸门也没有平掉 15m 仓", A.position and A.position.side == "long")
    ok("异周期反向没有额外下单", len(CALLS) == n_calls)

    # 2c. 串品种信号直接忽略
    await exA.on_signal({"type": "sell", "tf": "15m", "ts": 22, "price": 99.0,
                         "symbol": "BBB-USDT"}, GATE_OK)
    ok("A 忽略打到自己执行器上的 BBB 信号", A.position and A.position.side == "long")

    # 3. A 止盈：+2% ≥ 1.5%，平 70% 并保本；B 完全不受影响
    b_qty = B.position.qty
    await exA.on_price(102.0)
    ok("A 止盈 70% + 止损抬保本",
       A.position.tp1_done and A.position.breakeven and A.position.qty < A.position.init_qty)
    ok("A 止盈时 B 仓位纹丝不动", B.position.qty == b_qty and not B.position.tp1_done)

    # 4. B 独立止损：空头价格涨破止损线 52 → 全平进 closed
    await exB.on_price(52.5)
    ok("B 触发止损全平、落 closed 历史",
       B.position is None and len(B.closed) == 1 and B.closed[0]["sym"] == "BBB-USDT")
    ok("B 止损时 A 剩余仓位还在", A.position and A.position.qty > 0)

    # 5. 同品种同周期反向信号 → 平掉 A 剩余仓位
    await exB.on_price(52.6)   # B 已无仓位，再喂价不该出错
    await exA.on_signal({"type": "sell", "tf": "15m", "ts": 3, "price": 103.0}, GATE_NO)
    ok("A 的 15m 反向信号平掉剩余仓位（闸门不通过也要离场）",
       A.position is None and len(A.closed) == 1)

    # 5b. 误开的非允许周期残留仓，同周期反向仍能平掉
    from position import Position
    A.position = Position(
        symbol="AAA-USDT-SWAP", side="short", tf="1m", entry=100.0, qty=1.0,
        init_qty=1.0, stop=110.0, leverage=3, entry_ts=1,
    )
    await exA.on_signal({"type": "buy", "tf": "1m", "ts": 4, "price": 101.0,
                         "symbol": "AAA-USDT"}, GATE_NO)
    ok("1m 残留仓可被 1m 反向清掉", A.position is None)

    # 6. cfg_for 合并视图：品种关 → enabled False；总开关关 → 全 False
    A2 = state.stores["AAA-USDT"]
    A2.cfg.enabled = False
    ok("品种开关关闭 → 合并 enabled=False", not state.cfg_for("AAA-USDT").enabled)
    A2.cfg.enabled = True
    state.trade_cfg.enabled = False
    ok("总开关关闭 → 全品种 enabled=False",
       not state.cfg_for("AAA-USDT").enabled and not state.cfg_for("BBB-USDT").enabled)
    ok("合并视图带品种参数", state.cfg_for("BBB-USDT").leverage == 3
       and state.cfg_for("BBB-USDT").amount_usdt == 10)

    from regime import limit_price, TradeConfig
    lp = TradeConfig(price_offset=0.05)
    ok("买单追价（高于现价）", limit_price({"type": "buy", "price": 100}, lp) > 100)
    ok("卖单追价（低于现价）", limit_price({"type": "sell", "price": 100}, lp) < 100)

    print("\n下单流水:")
    for c in CALLS:
        print("  ", c)
    print("\n全部通过 ✅")


asyncio.run(main())
